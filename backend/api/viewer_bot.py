"""Isolated browser sessions for YouTube live playback.

YouTube does not expose Kick's viewer WebSocket protocol. Each worker instead
opens the actual YouTube player in its own Chromium context. Playback is not a
promise that YouTube will count the session as a viewer.
"""

import asyncio
import logging
import os
import threading
from urllib.parse import unquote, urlsplit

from .youtube import normalize_youtube_target, youtube_video_id

logger = logging.getLogger(__name__)
MAX_SESSIONS = 100


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
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            proxies.append(parse_proxy(line, default_type))
        except ValueError as exc:
            raise ValueError(f"Proxy on line {number}: {exc}") from None
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
        self.status = {
            "state": "initialized", "message": "Ready to start YouTube playback.",
            "proxy_count": len(self.all_proxies), "proxy_loading_progress": 100,
            "startup_progress": 0,
        }
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._loop = None
        self._async_stop = None

    def _update_status(self, state, message, progress=None):
        with self._lock:
            self.status.update(state=state, message=message)
            if progress is not None:
                self.status["startup_progress"] = progress

    def snapshot(self):
        with self._lock:
            return {
                "active_threads": self.active_threads,
                "active_connections": self.active_connections,
                "request_count": self.request_count,
                "successful_sessions": self.successful_sessions,
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
                try:
                    with self._lock:
                        self.request_count += 1
                    proxy = self.all_proxies[index % len(self.all_proxies)] if self.all_proxies else None
                    options = {"locale": "en-US"}
                    if proxy:
                        options["proxy"] = proxy
                    context = await browser.new_context(**options)
                    page = await context.new_page()
                    response = await page.goto(self.channel_url, wait_until="domcontentloaded", timeout=self.timeout)
                    if response and response.status >= 400:
                        raise RuntimeError("YouTube returned an error while loading the player.")
                    # Consent dialogs can occur in some regions. This does not bypass sign-in or access restrictions.
                    if "consent.youtube.com" in page.url:
                        button = page.get_by_role("button", name="Reject all")
                        if await button.count():
                            await button.first.click(timeout=self.timeout)
                    await page.wait_for_selector("video", state="attached", timeout=self.timeout)
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
                    self._update_status("running", "YouTube playback is active. Counts are determined by YouTube.", 100)
                    stalled = 0
                    while not self._stop.is_set():
                        try:
                            await asyncio.wait_for(self._async_stop.wait(), timeout=5)
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
                            raise RuntimeError("The YouTube player stopped or is unavailable.")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failures += 1
                    logger.warning("A YouTube session could not play (%s)", type(exc).__name__)
                    with self._lock:
                        has_playing = self.active_connections > (1 if playing else 0)
                    if not has_playing:
                        suffix = " Retrying..." if self.reconnect else ""
                        self._update_status(
                            "warning" if self.reconnect else "error",
                            "Playback unavailable. Check the live link, proxy, connection or YouTube restrictions." + suffix,
                            100,
                        )
                    if not self.reconnect:
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
                if not self._stop.is_set():
                    try:
                        await asyncio.wait_for(self._async_stop.wait(), timeout=min(5 * 2 ** min(failures, 4), 60))
                    except asyncio.TimeoutError:
                        pass
        finally:
            with self._lock:
                self.active_threads -= 1
