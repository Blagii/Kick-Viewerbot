"""Playback diagnostics and proxy failover use fake browsers, never external hosts."""

import asyncio
import base64
import threading
import time
from collections import defaultdict
from types import SimpleNamespace

import pytest

import backend.api.viewer_bot as viewer_bot
import backend.main as backend
from backend.api.viewer_bot import (
    ViewerBot,
    diagnose_playback_error,
    http_playback_failure,
    parse_proxy_list,
)


SECRET = "private-proxy-password"


def wait_until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def fast_timing(monkeypatch):
    monkeypatch.setattr(viewer_bot, "STARTUP_PROXY_RETRY_DELAY", 0.001)
    monkeypatch.setattr(viewer_bot, "RECONNECT_BASE_DELAY", 0.01)
    monkeypatch.setattr(viewer_bot, "MAX_RECONNECT_DELAY", 0.1)
    monkeypatch.setattr(viewer_bot, "PLAYER_CHECK_INTERVAL", 0.01)


class ScriptedPage:
    url = "https://www.youtube.com/watch?v=AbCDef12_-X"

    def __init__(self, script):
        self.script = script

    async def goto(self, *args, **kwargs):
        if self.script.get("goto_error"):
            raise self.script["goto_error"]
        return SimpleNamespace(status=self.script.get("http_status", 200))

    async def wait_for_selector(self, *args, **kwargs):
        if self.script.get("selector_error"):
            raise self.script["selector_error"]

    async def wait_for_function(self, *args, **kwargs):
        if self.script.get("play_error"):
            raise self.script["play_error"]

    async def evaluate(self, script):
        if "video.muted = true" in script:
            return None
        return self.script.get("monitor_result", {"playing": True})


class ScriptedContext:
    def __init__(self, browser, script):
        self.browser = browser
        self.script = script

    async def new_page(self):
        return ScriptedPage(self.script)

    async def close(self):
        self.browser.closed_contexts += 1


class ScriptedBrowser:
    def __init__(self, scripts):
        # The last script is reused if the run makes more attempts than supplied.
        self.scripts = scripts
        self.options = []
        self.attempts_by_worker = defaultdict(list)
        self.closed_contexts = 0
        self.closed = False

    async def new_context(self, **options):
        index = len(self.options)
        self.options.append(options)
        self.attempts_by_worker[asyncio.current_task().get_name()].append(options)
        script = self.scripts[min(index, len(self.scripts) - 1)]
        if script.get("context_error"):
            raise script["context_error"]
        return ScriptedContext(self, script)

    async def close(self):
        self.closed = True


def install_fake_playwright(monkeypatch, browser):
    import playwright.async_api

    class Chromium:
        async def launch(self, **options):
            return browser

    class Playwright:
        chromium = Chromium()

    class Manager:
        async def __aenter__(self):
            return Playwright()

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: Manager())


def start_in_thread(bot):
    thread = threading.Thread(target=bot.main)
    thread.start()
    return thread


def stop_and_join(bot, thread):
    bot.stop()
    thread.join(3)
    assert not thread.is_alive()


@pytest.mark.parametrize(
    "exception,stage,proxy,expected",
    [
        (RuntimeError("net::ERR_PROXY_CONNECTION_FAILED"), "navigation", True, "proxy_connection_failed"),
        (RuntimeError("net::ERR_TUNNEL_CONNECTION_FAILED"), "navigation", True, "proxy_connection_failed"),
        (RuntimeError("net::ERR_SOCKS_CONNECTION_FAILED"), "navigation", True, "proxy_connection_failed"),
        (RuntimeError("net::ERR_NO_SUPPORTED_PROXIES"), "navigation", True, "proxy_connection_failed"),
        (RuntimeError("net::ERR_PROXY_AUTH_REQUESTED"), "navigation", True, "proxy_auth_required"),
        (RuntimeError("net::ERR_NAME_NOT_RESOLVED"), "navigation", False, "dns_failed"),
        (RuntimeError("net::ERR_DNS_TIMED_OUT"), "navigation", False, "dns_failed"),
        (RuntimeError("net::ERR_CERT_DATE_INVALID"), "navigation", True, "certificate_error"),
        (RuntimeError("net::ERR_SSL_PROTOCOL_ERROR"), "navigation", True, "tls_failed"),
        (RuntimeError("net::ERR_CONNECTION_TIMED_OUT"), "navigation", False, "connection_timeout"),
        (RuntimeError("net::ERR_CONNECTION_RESET"), "navigation", False, "network_connection_failed"),
        (RuntimeError("net::ERR_CONNECTION_FAILED"), "navigation", False, "network_connection_failed"),
        (TimeoutError("expired"), "player", True, "player_timeout"),
        (TimeoutError("expired"), "playback", False, "playback_timeout"),
        (RuntimeError("unknown"), "proxy_setup", True, "proxy_setup_failed"),
        (RuntimeError("unknown"), "browser_setup", False, "browser_context_failed"),
        (RuntimeError("unknown"), "player", False, "player_unavailable"),
        (RuntimeError("unknown"), "playback", False, "playback_failed"),
        (RuntimeError("unknown"), "navigation", False, "connection_failed"),
    ],
)
def test_error_classification_never_exposes_exception_text(exception, stage, proxy, expected):
    # Playwright errors often include both a target URL and proxy credentials.
    exception.args = (f"{exception} https://user:{SECRET}@private-proxy:1234/private-url",)
    code, message = diagnose_playback_error(exception, stage, proxy)
    assert code == expected
    assert SECRET not in message and "private-proxy:1234" not in message and "private-url" not in message


def test_unknown_navigation_error_mentions_proxy_without_details():
    code, message = diagnose_playback_error(RuntimeError(f"unexpected {SECRET}"), "navigation", True)
    assert code == "connection_failed" and "through the proxy" in message
    assert SECRET not in message


@pytest.mark.parametrize(
    "status,expected",
    [(403, "http_forbidden"), (407, "proxy_auth_required"), (429, "http_rate_limited"),
     (404, "http_error"), (503, "http_server_error")],
)
def test_http_failures_are_specific_and_safe(status, expected):
    code, message = diagnose_playback_error(http_playback_failure(status), "navigation", True)
    assert code == expected
    assert "password" in message.lower() or str(status) in message


def test_duplicate_normalized_proxies_are_removed():
    proxies = parse_proxy_list(
        "host1:8080\nhttp://host1:8080\n"
        "http://user:password@host1:8080\nhttp://user:password@host1:8080\n"
        "socks5://host1:1080\n"
    )
    assert len(proxies) == 3
    assert [p["server"] for p in proxies] == [
        "http://host1:8080", "http://host1:8080", "socks5://host1:1080"
    ]


def test_startup_tries_other_proxies_even_with_reconnect_off(monkeypatch, fast_timing, caplog):
    browser = ScriptedBrowser([
        {"goto_error": RuntimeError(f"net::ERR_TUNNEL_CONNECTION_FAILED {SECRET}")},
        {"goto_error": TimeoutError(SECRET)},
        {},
    ])
    install_fake_playwright(monkeypatch, browser)
    bot = ViewerBot(1, "AbCDef12_-X", proxy_text=f"u:{SECRET}@host1:8080\nhost2:8080\nhost3:8080", reconnect=False)
    thread = start_in_thread(bot)
    try:
        assert wait_until(lambda: bot.snapshot()["active_connections"] == 1)
        snap = bot.snapshot()
        assert snap["request_count"] == 3
        assert snap["failed_attempts"] == 2
        assert snap["last_error"]["code"] == "connection_timeout"
        assert snap["status"]["state"] == "running"
        assert snap["status"]["error_code"] is None
        assert [option["proxy"]["server"] for option in browser.options] == [
            "http://host1:8080", "http://host2:8080", "http://host3:8080"
        ]
        assert SECRET not in str(snap) and SECRET not in caplog.text
    finally:
        stop_and_join(bot, thread)
    assert browser.closed and browser.closed_contexts == 3


def test_startup_proxy_failover_is_bounded_and_never_uses_direct(monkeypatch, fast_timing, caplog):
    browser = ScriptedBrowser([{"goto_error": RuntimeError(f"net::ERR_PROXY_CONNECTION_FAILED {SECRET}")}])
    install_fake_playwright(monkeypatch, browser)
    proxies = "\n".join(f"u:{SECRET}@host{i}:8080" for i in range(8))
    bot = ViewerBot(1, "AbCDef12_-X", proxy_text=proxies, reconnect=False)
    bot.main()
    snap = bot.snapshot()
    assert len(browser.options) == snap["request_count"] == snap["failed_attempts"] == 5
    assert len({option["proxy"]["server"] for option in browser.options}) == 5
    assert snap["status"]["state"] == "error"
    assert snap["status"]["error_code"] == "proxy_connection_failed"
    assert snap["active_connections"] == snap["active_threads"] == 0
    assert SECRET not in str(snap) and SECRET not in caplog.text
    assert browser.closed and browser.closed_contexts == 5


def test_each_worker_tries_distinct_proxies_under_concurrency(monkeypatch, fast_timing):
    browser = ScriptedBrowser([{"goto_error": RuntimeError("net::ERR_PROXY_CONNECTION_FAILED")}])
    install_fake_playwright(monkeypatch, browser)
    bot = ViewerBot(3, "AbCDef12_-X", proxy_text="host1:8080\nhost2:8080\nhost3:8080", reconnect=False)
    bot.main()
    assert bot.snapshot()["request_count"] == 9
    assert len(browser.attempts_by_worker) == 3
    for attempts in browser.attempts_by_worker.values():
        assert len(attempts) == 3
        assert len({option["proxy"]["server"] for option in attempts}) == 3


def test_no_proxy_and_reconnect_off_does_not_retry(monkeypatch, fast_timing):
    browser = ScriptedBrowser([{"goto_error": RuntimeError("net::ERR_NAME_NOT_RESOLVED")}])
    install_fake_playwright(monkeypatch, browser)
    bot = ViewerBot(1, "AbCDef12_-X", reconnect=False)
    bot.main()
    snap = bot.snapshot()
    assert len(browser.options) == 1 and "proxy" not in browser.options[0]
    assert snap["status"]["error_code"] == "dns_failed"
    assert snap["status"]["state"] == "error"


def test_http_response_reaches_status_panel(monkeypatch):
    browser = ScriptedBrowser([{"http_status": 403}])
    install_fake_playwright(monkeypatch, browser)
    bot = ViewerBot(1, "AbCDef12_-X", reconnect=False)
    bot.main()
    status = bot.snapshot()["status"]
    assert status["state"] == "error" and status["error_code"] == "http_forbidden"
    assert "403" in status["message"]


def test_reconnect_continues_after_startup_failover_budget(monkeypatch, fast_timing):
    browser = ScriptedBrowser([{"goto_error": RuntimeError("net::ERR_PROXY_CONNECTION_FAILED")}] * 5 + [{}])
    install_fake_playwright(monkeypatch, browser)
    bot = ViewerBot(1, "AbCDef12_-X", proxy_text="\n".join(f"host{i}:8080" for i in range(8)), reconnect=True)
    thread = start_in_thread(bot)
    try:
        assert wait_until(lambda: bot.snapshot()["active_connections"] == 1)
        assert bot.snapshot()["request_count"] == 6
        assert bot.snapshot()["failed_attempts"] == 5
        assert all("proxy" in option for option in browser.options)
    finally:
        stop_and_join(bot, thread)


def test_reconnect_off_does_not_retry_after_playback_failure(monkeypatch, fast_timing):
    browser = ScriptedBrowser([{"monitor_result": {"missing": True}}, {}])
    install_fake_playwright(monkeypatch, browser)
    bot = ViewerBot(1, "AbCDef12_-X", proxy_text="host1:8080\nhost2:8080", reconnect=False)
    bot.main()
    snap = bot.snapshot()
    assert snap["request_count"] == snap["successful_sessions"] == 1
    assert snap["status"]["state"] == "error"
    assert snap["status"]["error_code"] == "player_stopped"
    assert snap["active_connections"] == 0


def test_reconnect_on_retries_after_playback_failure_with_next_proxy(monkeypatch, fast_timing):
    browser = ScriptedBrowser([{"monitor_result": {"missing": True}}, {}])
    install_fake_playwright(monkeypatch, browser)
    bot = ViewerBot(1, "AbCDef12_-X", proxy_text="host1:8080\nhost2:8080", reconnect=True)
    thread = start_in_thread(bot)
    try:
        assert wait_until(lambda: bot.snapshot()["successful_sessions"] == 2)
        snap = bot.snapshot()
        assert snap["status"]["state"] == "running" and snap["active_connections"] == 1
        assert snap["last_error"]["code"] == "player_stopped"
        assert [o["proxy"]["server"] for o in browser.options] == ["http://host1:8080", "http://host2:8080"]
    finally:
        stop_and_join(bot, thread)


def test_naturally_ended_player_is_not_reconnected(monkeypatch, fast_timing):
    browser = ScriptedBrowser([{"monitor_result": {"ended": True}}, {}])
    install_fake_playwright(monkeypatch, browser)
    bot = ViewerBot(1, "AbCDef12_-X", reconnect=True)
    bot.main()
    snap = bot.snapshot()
    assert snap["request_count"] == 1
    assert snap["successful_sessions"] == 1 and snap["failed_attempts"] == 0
    assert snap["status"]["state"] == "stopped"


def test_last_failure_is_preserved_when_another_player_is_running(monkeypatch):
    browser = ScriptedBrowser([{}, {"goto_error": RuntimeError(f"net::ERR_PROXY_CONNECTION_FAILED {SECRET}")}])
    install_fake_playwright(monkeypatch, browser)
    bot = ViewerBot(2, "AbCDef12_-X", reconnect=False)
    thread = start_in_thread(bot)
    try:
        assert wait_until(lambda: bot.snapshot()["failed_attempts"] == 1)
        snap = bot.snapshot()
        assert snap["active_connections"] == 1 and snap["status"]["state"] == "running"
        assert snap["last_error"]["code"] == "proxy_connection_failed"
        assert SECRET not in str(snap)
    finally:
        stop_and_join(bot, thread)


def test_stop_interrupts_startup_proxy_retry(monkeypatch):
    monkeypatch.setattr(viewer_bot, "STARTUP_PROXY_RETRY_DELAY", 10)
    browser = ScriptedBrowser([{"goto_error": RuntimeError("net::ERR_PROXY_CONNECTION_FAILED")}])
    install_fake_playwright(monkeypatch, browser)
    bot = ViewerBot(1, "AbCDef12_-X", proxy_text="host1:8080\nhost2:8080", reconnect=False)
    thread = start_in_thread(bot)
    try:
        assert wait_until(lambda: bot.snapshot()["failed_attempts"] == 1)
        snap = bot.snapshot()
        assert snap["status"]["state"] == "warning"
        assert "another proxy" in snap["status"]["message"]
    finally:
        stop_and_join(bot, thread)
    assert bot.snapshot()["request_count"] == 1
    assert bot.snapshot()["status"]["state"] == "stopped"


def test_safe_error_and_counters_are_delivered_by_socketio(monkeypatch, caplog):
    browser = ScriptedBrowser([{"goto_error": RuntimeError(f"net::ERR_PROXY_CONNECTION_FAILED {SECRET}")}])
    install_fake_playwright(monkeypatch, browser)
    manager = backend.BotManager()
    monkeypatch.setattr(backend, "bot_manager", manager)
    client = backend.socketio.test_client(backend.app)
    try:
        assert client.is_connected()
        data = {
            "channelName": "AbCDef12_-X", "threads": 1, "reconnect": False,
            "proxyFileData": base64.b64encode(f"u:{SECRET}@host1:8080".encode()).decode(),
        }
        assert client.emit("start_bot", data, callback=True)["success"]
        manager.bot_thread.join(3)
        assert not manager.bot_thread.is_alive()
        client.emit("get_stats")
        updates = [event for event in client.get_received() if event["name"] == "stats_update"]
        stats = updates[-1]["args"][0]
        assert not stats["is_running"] and stats["failed_attempts"] == 1
        assert stats["status"]["error_code"] == stats["last_error"]["code"] == "proxy_connection_failed"
        assert SECRET not in str(updates) and SECRET not in caplog.text
    finally:
        if manager.is_running:
            manager.stop_bot()
            manager.bot_thread.join(3)
        client.disconnect()
