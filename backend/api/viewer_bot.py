"""Isolated browser sessions for YouTube live playback.

YouTube does not expose Kick's viewer WebSocket protocol. Each worker instead
opens the actual YouTube player in its own Chromium context. Playback is not a
promise that YouTube will count the session as a viewer.
"""

import asyncio
import logging
import os
import re
import threading
from urllib.parse import unquote, urlsplit

from .youtube import normalize_youtube_target, youtube_video_id

logger = logging.getLogger(__name__)
MAX_SESSIONS = 100
# Try a small number of different proxies before giving up on initial playback,
# regardless of whether post-playback reconnection is enabled.
MAX_STARTUP_PROXY_ATTEMPTS = 5
STARTUP_PROXY_RETRY_DELAY = 0.5
RECONNECT_BASE_DELAY = 5
MAX_RECONNECT_DELAY = 60
PLAYER_CHECK_INTERVAL = 5


class PlaybackFailure(Exception):
    """An expected playback failure containing only a safe, actionable message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def http_playback_failure(status: int) -> PlaybackFailure:
    if status == 407:
        return PlaybackFailure(
            "proxy_auth_required",
            "The proxy requires authentication. Check its username and password.",
        )
    if status == 429:
        return PlaybackFailure(
            "http_rate_limited",
            "The request was rate limited (HTTP 429). Wait and check the connection or proxy.",
        )
    if status == 403:
        return PlaybackFailure(
            "http_forbidden",
            "The request was refused (HTTP 403). Check the video, network and proxy.",
        )
    if status >= 500:
        return PlaybackFailure(
            "http_server_error",
            f"The request failed with HTTP {status}. Check the service and proxy.",
        )
    return PlaybackFailure(
        "http_error",
        f"The request failed with HTTP {status}. Check the video URL and proxy.",
    )


def diagnose_playback_error(exc: Exception, stage: str, using_proxy: bool) -> tuple[str, str]:
    """Classify common errors without returning URLs, credentials or exception text."""
    if isinstance(exc, PlaybackFailure):
        return exc.code, exc.message

    # Playwright includes request URLs in exception messages. Read only known error
    # tokens and never copy the rest of the exception into logs or status events.
    match = re.search(r"\bnet::(ERR_[A-Z_]+)\b", str(exc))
    network_error = match.group(1) if match else ""
    if network_error in {"ERR_PROXY_AUTH_REQUESTED", "ERR_INVALID_AUTH_CREDENTIALS"}:
        return "proxy_auth_required", "The proxy rejected authentication. Check its credentials."
    if network_error in {
        "ERR_PROXY_CONNECTION_FAILED",
        "ERR_TUNNEL_CONNECTION_FAILED",
        "ERR_MANDATORY_PROXY_CONFIGURATION_FAILED",
        "ERR_NO_SUPPORTED_PROXIES",
        "ERR_SOCKS_CONNECTION_FAILED",
        "ERR_SOCKS_CONNECTION_HOST_UNREACHABLE",
    }:
        return (
            "proxy_connection_failed",
            "The proxy connection failed or was blocked. Check the proxy and network access.",
        )
    if network_error in {"ERR_NAME_NOT_RESOLVED", "ERR_NAME_RESOLUTION_FAILED", "ERR_DNS_TIMED_OUT"}:
        return (
            "dns_failed",
            "A hostname could not be resolved. Check DNS, network access and any proxy.",
        )
    if network_error in {
        "ERR_CERT_AUTHORITY_INVALID",
        "ERR_CERT_COMMON_NAME_INVALID",
        "ERR_CERT_DATE_INVALID",
        "ERR_PROXY_CERTIFICATE_INVALID",
    }:
        return (
            "certificate_error",
            "A TLS certificate was rejected. Check the system clock, network and proxy.",
        )
    if network_error in {"ERR_SSL_PROTOCOL_ERROR", "ERR_SSL_VERSION_OR_CIPHER_MISMATCH"}:
        return "tls_failed", "The TLS connection failed. Check the network and proxy."
    if network_error in {"ERR_CONNECTION_TIMED_OUT", "ERR_TIMED_OUT"} or "timeout" in type(exc).__name__.lower():
        if stage == "player":
            return (
                "player_timeout",
                "The video player did not appear in time. Check the video, network and proxy.",
            )
        if stage == "playback":
            return (
                "playback_timeout",
                "Playback did not start in time. Check the video, network and proxy.",
            )
        return (
            "connection_timeout",
            "The connection timed out. Check network access and any proxy, or increase the timeout.",
        )
    if network_error in {
        "ERR_CONNECTION_REFUSED",
        "ERR_CONNECTION_CLOSED",
        "ERR_CONNECTION_RESET",
        "ERR_CONNECTION_ABORTED",
        "ERR_CONNECTION_FAILED",
        "ERR_EMPTY_RESPONSE",
        "ERR_ADDRESS_UNREACHABLE",
        "ERR_INTERNET_DISCONNECTED",
        "ERR_NETWORK_CHANGED",
        "ERR_NETWORK_ACCESS_DENIED",
        "ERR_NETWORK_ACCESS_REVOKED",
    }:
        return (
            "network_connection_failed",
            "The network connection failed or was interrupted. Check network access and any proxy.",
        )
    if stage == "proxy_setup" and using_proxy:
        return "proxy_setup_failed", "The browser could not use the proxy. Check its format and protocol."
    if stage == "browser_setup":
        return "browser_context_failed", "The browser could not open a session. Check its installation and resources."
    if stage == "playback":
        return (
            "playback_failed",
            "The player could not start playback. Check that the stream is public and available.",
        )
    if stage == "player":
        return (
            "player_unavailable",
            "The video player could not be loaded. Check the video, network and proxy.",
        )
    if using_proxy:
        return (
            "connection_failed",
            "The page could not be loaded through the proxy. Check its protocol and network access.",
        )
    return (
        "connection_failed",
        "The page could not be loaded. Check the video URL and network access.",
    )


def parse_proxy(line: str, default_type: str = "http") -> dict:
    """Parse one HTTP/SOCKS proxy for Playwright without logging credentials."""
    raw = line.strip()
    if "://" not in raw:
        raw = f"{default_type if default_type != 'all' else 'http'}://{raw}"
    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower()
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise ValueError("Invalid proxy address or port.") from None
    if (scheme not in {"http", "https", "socks4", "socks5"} or not host or not port
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Expected an HTTP, SOCKS4, or SOCKS5 proxy with a host and port.")
    if not 1 <= port <= 65535:
        raise ValueError("Invalid proxy port.")
    username, password = parsed.username, parsed.password
    if scheme.startswith("socks") and (username is not None or password is not None):
        raise ValueError("Chromium does not support authenticated SOCKS proxies; use an HTTP proxy instead.")
    if (username is None) != (password is None):
        raise ValueError("Proxy credentials must include both username and password.")
    # IPv6 literals need brackets in the proxy URI.
    host = f"[{host}]" if ":" in host else host
    result = {"server": f"{scheme}://{host}:{port}"}
    if username is not None:
        result.update(username=unquote(username), password=unquote(password))
    return result


def parse_proxy_list(text: str, default_type: str = "http") -> list:
    proxies = []
    seen = set()
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            proxy = parse_proxy(line, default_type)
        except ValueError as exc:
            raise ValueError(f"Proxy on line {number}: {exc}") from None
        identity = (proxy["server"], proxy.get("username"), proxy.get("password"))
        if identity not in seen:
            seen.add(identity)
            proxies.append(proxy)
    if not proxies:
        raise ValueError("The proxy file is empty or contains no proxies.")
    return proxies


class ViewerBot:
    def __init__(self, nb_of_threads, channel_name, proxy_text=None, timeout=15000,
                 type_of_proxy="http", reconnect=True):
        self.nb_of_threads = int(nb_of_threads)
        if not 1 <= self.nb_of_threads <= MAX_SESSIONS:
            raise ValueError(f"Sessions must be between 1 and {MAX_SESSIONS}.")
        self.timeout = int(timeout)
        if not 1000 <= self.timeout <= 120000:
            raise ValueError("Timeout must be between 1000 and 120000 milliseconds.")
        if type_of_proxy not in {"http", "socks4", "socks5", "all"}:
            raise ValueError("Unsupported default proxy type.")
        self.channel_url = normalize_youtube_target(channel_name)
        self.channel_name = channel_name.strip()
        self.video_id = youtube_video_id(self.channel_url)
        self.all_proxies = parse_proxy_list(proxy_text, type_of_proxy) if proxy_text is not None else []
        self.reconnect = bool(reconnect)
        self.active_threads = 0
        self.active_connections = 0
        self.request_count = 0  # Number of browser playback attempts, not YouTube views.
        self.successful_sessions = 0
        self.failed_attempts = 0
        self.last_error = None
        self.status = {
            "state": "initialized", "message": "Ready to start YouTube playback.",
            "proxy_count": len(self.all_proxies), "proxy_loading_progress": 100,
            "startup_progress": 0, "error_code": None,
        }
        self._proxy_cursor = 0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._loop = None
        self._async_stop = None

    def _update_status(self, state, message, progress=None, error_code=None):
        with self._lock:
            self.status.update(state=state, message=message, error_code=error_code)
            if progress is not None:
                self.status["startup_progress"] = progress

    def _next_proxy(self, excluded):
        """Rotate across workers while avoiding repeats in one worker's attempt batch."""
        if not self.all_proxies:
            return None, None
        with self._lock:
            for _ in range(len(self.all_proxies)):
                proxy_index = self._proxy_cursor % len(self.all_proxies)
                self._proxy_cursor += 1
                if proxy_index not in excluded:
                    return proxy_index, self.all_proxies[proxy_index]
        raise RuntimeError("Proxy selection exhausted.")

    def snapshot(self):
        with self._lock:
            return {
                "active_threads": self.active_threads,
                "active_connections": self.active_connections,
                "request_count": self.request_count,
                "successful_sessions": self.successful_sessions,
                "failed_attempts": self.failed_attempts,
                "last_error": dict(self.last_error) if self.last_error else None,
                "total_proxies": len(self.all_proxies),
                "video_id": self.video_id,
                "status": dict(self.status),
            }

    def stop(self):
        self._stop.set()
        self._update_status("stopping", "Stopping YouTube sessions...")
        with self._lock:
            loop, event = self._loop, self._async_stop
        if loop and event and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                pass

    def main(self):
        if self._stop.is_set():
            self._update_status("stopped", "YouTube sessions stopped.")
            return
        self._update_status("starting", "Launching Chromium for YouTube playback...", 10)
        try:
            asyncio.run(self._run())
            if self._stop.is_set():
                self._update_status("stopped", "YouTube sessions stopped.", 0)
            elif self.status["state"] != "error":
                self._update_status("stopped", "All YouTube sessions have ended.", 0)
        except Exception as exc:
            logger.error("Unable to start YouTube sessions: %s", type(exc).__name__)
            message = ("Chromium is not installed. Run: python -m playwright install chromium"
                       if "executable doesn't exist" in str(exc).lower()
                       else "Could not launch Chromium. Check the browser installation and system dependencies.")
            if self._stop.is_set():
                self._update_status("stopped", "YouTube sessions stopped.", 0)
            else:
                self._update_status("error", message, 0)
        finally:
            with self._lock:
                self._loop = None
                self._async_stop = None

    async def _run(self):
        from playwright.async_api import async_playwright

        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._async_stop = asyncio.Event()
            if self._stop.is_set():
                self._async_stop.set()
        if self._stop.is_set():
            return

        async with async_playwright() as playwright:
            options = {"headless": True, "args": ["--mute-audio", "--autoplay-policy=no-user-gesture-required"]}
            if os.environ.get("CHROMIUM_PATH"):
                options["executable_path"] = os.environ["CHROMIUM_PATH"]
            # Launching Chromium can take a while; allow Stop to interrupt startup.
            launch_task = asyncio.create_task(playwright.chromium.launch(**options))
            stop_during_launch = asyncio.create_task(self._async_stop.wait())
            try:
                done, _ = await asyncio.wait((launch_task, stop_during_launch), return_when=asyncio.FIRST_COMPLETED)
                if stop_during_launch in done and launch_task not in done:
                    launch_task.cancel()
                    await asyncio.gather(launch_task, return_exceptions=True)
                    return
                browser = await launch_task
            finally:
                stop_during_launch.cancel()
                await asyncio.gather(stop_during_launch, return_exceptions=True)
            try:
                if self._stop.is_set():
                    return
                self._update_status("starting", "Opening YouTube players...", 30)
                workers = [asyncio.create_task(self._worker(browser, i)) for i in range(self.nb_of_threads)]
                all_workers = asyncio.gather(*workers, return_exceptions=True)
                stop_waiter = asyncio.create_task(self._async_stop.wait())
                try:
                    done, _ = await asyncio.wait((all_workers, stop_waiter), return_when=asyncio.FIRST_COMPLETED)
                    if stop_waiter in done:
                        for worker in workers:
                            worker.cancel()
                    await all_workers
                finally:
                    stop_waiter.cancel()
                    await asyncio.gather(stop_waiter, return_exceptions=True)
            finally:
                await browser.close()

    async def _worker(self, browser, index):
        with self._lock:
            self.active_threads += 1
        failures = 0
        startup_attempts = 0
        has_played = False
        attempted_proxies = set()
        startup_limit = min(MAX_STARTUP_PROXY_ATTEMPTS, len(self.all_proxies))
        try:
            # Stagger browser creation to avoid launching every page at once.
            try:
                await asyncio.wait_for(self._async_stop.wait(), timeout=min(index * 0.2, 10) or 0.001)
                return
            except asyncio.TimeoutError:
                pass
            while not self._stop.is_set():
                context = None
                playing = False
                proxy = None
                stage = "browser_setup"
                retry_delay = None
                try:
                    if startup_limit and len(attempted_proxies) >= startup_limit:
                        attempted_proxies.clear()
                    proxy_index, proxy = self._next_proxy(attempted_proxies)
                    if proxy_index is not None:
                        attempted_proxies.add(proxy_index)
                    if not has_played:
                        startup_attempts += 1
                    with self._lock:
                        self.request_count += 1
                    options = {"locale": "en-US"}
                    if proxy:
                        options["proxy"] = proxy
                        stage = "proxy_setup"
                    context = await browser.new_context(**options)
                    page = await context.new_page()
                    stage = "navigation"
                    response = await page.goto(self.channel_url, wait_until="domcontentloaded", timeout=self.timeout)
                    if response and response.status >= 400:
                        raise http_playback_failure(response.status)
                    # Consent dialogs can occur in some regions. This does not bypass sign-in or access restrictions.
                    if "consent.youtube.com" in page.url:
                        button = page.get_by_role("button", name="Reject all")
                        if await button.count():
                            await button.first.click(timeout=self.timeout)
                    stage = "player"
                    await page.wait_for_selector("video", state="attached", timeout=self.timeout)
                    stage = "playback"
                    await page.evaluate("""() => {
                        const video = document.querySelector('video');
                        video.muted = true;
                        const promise = video.play();
                        if (promise) promise.catch(() => {});
                    }""")
                    await page.wait_for_function(
                        "() => { const v = document.querySelector('video'); return v && !v.paused && !v.ended && v.readyState >= 2; }",
                        timeout=self.timeout,
                    )
                    try:
                        resolved_id = youtube_video_id(page.url)
                        if resolved_id:
                            with self._lock:
                                self.video_id = resolved_id
                    except ValueError:
                        pass
                    with self._lock:
                        self.active_connections += 1
                        self.successful_sessions += 1
                        playing = True
                    failures = 0
                    has_played = True
                    attempted_proxies.clear()
                    self._update_status("running", "YouTube playback is active. Counts are determined by YouTube.", 100)
                    stalled = 0
                    while not self._stop.is_set():
                        try:
                            await asyncio.wait_for(self._async_stop.wait(), timeout=PLAYER_CHECK_INTERVAL)
                            break
                        except asyncio.TimeoutError:
                            pass
                        state = await page.evaluate("""() => {
                            const v = document.querySelector('video');
                            if (!v) return {missing: true};
                            if (v.paused && !v.ended) { const p = v.play(); if (p) p.catch(() => {}); }
                            return {ended: v.ended, error: !!v.error, playing: !v.paused && v.readyState >= 2};
                        }""")
                        if state.get("ended"):
                            return
                        now_playing = bool(state.get("playing")) and not state.get("error")
                        if now_playing != playing:
                            with self._lock:
                                self.active_connections += 1 if now_playing else -1
                                playing = now_playing
                        stalled = 0 if now_playing else stalled + 1
                        if state.get("missing") or state.get("error") or stalled >= 3:
                            raise PlaybackFailure(
                                "player_stopped",
                                "The player stopped. The stream may have ended or become unavailable.",
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failures += 1
                    code, message = diagnose_playback_error(exc, stage, proxy is not None)
                    logger.warning("YouTube session failed during %s (%s)", stage, code)
                    # Startup proxy failover is bounded and does not imply reconnecting
                    # after playback has begun. A configured proxy is never bypassed.
                    if not has_played and proxy is not None and startup_attempts < startup_limit:
                        retry_delay = STARTUP_PROXY_RETRY_DELAY
                        suffix = f" Trying another proxy ({startup_attempts + 1}/{startup_limit})."
                    elif self.reconnect:
                        retry_delay = min(RECONNECT_BASE_DELAY * 2 ** min(failures - 1, 4), MAX_RECONNECT_DELAY)
                        suffix = " Automatic retry is enabled."
                    else:
                        suffix = ""
                    with self._lock:
                        self.failed_attempts += 1
                        self.last_error = {"code": code, "message": message}
                        has_playing = self.active_connections > (1 if playing else 0)
                        if not has_playing and not self._stop.is_set():
                            self._update_status(
                                "warning" if retry_delay is not None else "error",
                                message + suffix, 100, error_code=code,
                            )
                    if retry_delay is None:
                        return
                finally:
                    if playing:
                        with self._lock:
                            self.active_connections -= 1
                    if context:
                        try:
                            await context.close()
                        except Exception:
                            pass
                if retry_delay is not None and not self._stop.is_set():
                    try:
                        await asyncio.wait_for(self._async_stop.wait(), timeout=retry_delay)
                    except asyncio.TimeoutError:
                        pass
        finally:
            with self._lock:
                self.active_threads -= 1
