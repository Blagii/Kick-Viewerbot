import threading
import time

import pytest

from backend.api.viewer_bot import ViewerBot, parse_proxy, parse_proxy_list


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("line, default, expected", [
    ("127.0.0.1:8080", "all", {"server": "http://127.0.0.1:8080"}),
    ("socks5://host.example:1080", "http", {"server": "socks5://host.example:1080"}),
    ("socks4://[::1]:1080", "all", {"server": "socks4://[::1]:1080"}),
    ("user:p%40ss@host.example:3128", "http", {"server": "http://host.example:3128", "username": "user", "password": "p@ss"}),
])
def test_parse_proxy(line, default, expected):
    assert parse_proxy(line, default) == expected


@pytest.mark.parametrize("line", ["", "http://host", "ftp://example.com:21", "host:notaport", "http://user@host:8080", "socks5://u:p@host:1080"])
def test_reject_invalid_proxy(line):
    with pytest.raises(ValueError):
        parse_proxy(line)


def test_proxy_file_is_strict_and_never_prints_credentials():
    assert len(parse_proxy_list("# comment\nhost:80\nsocks5://host:1080\n", "all")) == 2
    with pytest.raises(ValueError, match="line 2") as error:
        parse_proxy_list("host:80\nsocks5://alice:supersecret@host:1080")
    assert "supersecret" not in str(error.value)
    with pytest.raises(ValueError, match="empty"):
        parse_proxy_list("# none\n")


@pytest.mark.parametrize("threads,timeout", [(0, 10000), (101, 10000), (1, 999), (1, 120001)])
def test_session_validation(threads, timeout):
    with pytest.raises(ValueError):
        ViewerBot(threads, "AbCDef12_-X", timeout=timeout)


class FakePage:
    url = "https://www.youtube.com/watch?v=AbCDef12_-X"
    async def goto(self, *args, **kwargs):
        class Response: status = 200
        return Response()
    async def wait_for_selector(self, *args, **kwargs): pass
    async def wait_for_function(self, *args, **kwargs): pass
    async def evaluate(self, script): return {"playing": True}


class FakeContext:
    def __init__(self, browser):
        self.browser = browser
    async def new_page(self): return FakePage()
    async def close(self): self.browser.closed_contexts += 1


class FakeBrowser:
    def __init__(self, fail=False):
        self.options = []
        self.closed_contexts = 0
        self.closed = False
        self.fail = fail
    async def new_context(self, **options):
        self.options.append(options)
        if self.fail:
            raise RuntimeError("proxy unavailable")
        return FakeContext(self)
    async def close(self): self.closed = True


def install_fake_playwright(monkeypatch, browser):
    import playwright.async_api
    class Chromium:
        async def launch(self, **options): return browser
    class Playwright:
        chromium = Chromium()
    class Manager:
        async def __aenter__(self): return Playwright()
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: Manager())


def test_browser_sessions_use_separate_contexts_and_stop_cleanly(monkeypatch):
    browser = FakeBrowser()
    install_fake_playwright(monkeypatch, browser)
    bot = ViewerBot(2, "AbCDef12_-X", proxy_text="http://alice:pass@host1:8080\nsocks5://host2:1080", reconnect=True)
    thread = threading.Thread(target=bot.main)
    thread.start()
    try:
        assert wait_until(lambda: bot.snapshot()["active_connections"] == 2)
        snap = bot.snapshot()
        assert snap["status"]["state"] == "running"
        assert snap["request_count"] == 2
        assert [x["proxy"]["server"] for x in browser.options] == ["http://host1:8080", "socks5://host2:1080"]
        assert "pass" not in str(snap)
    finally:
        bot.stop()
        thread.join(3)
    assert not thread.is_alive()
    assert bot.snapshot()["active_connections"] == bot.snapshot()["active_threads"] == 0
    assert bot.snapshot()["status"]["state"] == "stopped"
    assert browser.closed and browser.closed_contexts == 2


def test_failed_proxy_never_falls_back_to_direct_connection(monkeypatch):
    browser = FakeBrowser(fail=True)
    install_fake_playwright(monkeypatch, browser)
    bot = ViewerBot(1, "AbCDef12_-X", proxy_text="host1:8080", reconnect=False)
    bot.main()
    assert browser.options and all("proxy" in option for option in browser.options)
    assert bot.snapshot()["status"]["state"] == "error"
    assert bot.snapshot()["active_connections"] == 0


def test_stop_interrupts_a_slow_browser_launch(monkeypatch):
    import asyncio
    import playwright.async_api
    launch_started = threading.Event()
    launch_cancelled = threading.Event()

    class Chromium:
        async def launch(self, **options):
            launch_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                launch_cancelled.set()
    class Playwright:
        chromium = Chromium()
    class Manager:
        async def __aenter__(self): return Playwright()
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: Manager())

    bot = ViewerBot(1, "AbCDef12_-X")
    thread = threading.Thread(target=bot.main)
    thread.start()
    try:
        assert launch_started.wait(2)
    finally:
        bot.stop()
        thread.join(3)
    assert not thread.is_alive()
    assert launch_cancelled.is_set()
    assert bot.snapshot()["status"]["state"] == "stopped"
