"""Optional real-browser smoke test; never contacts YouTube or external hosts."""
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend.api.viewer_bot import ViewerBot


@pytest.mark.skipif(not os.getenv("TEST_CHROMIUM_PATH"), reason="Set TEST_CHROMIUM_PATH to run a real-browser smoke test")
def test_real_browser_sessions_play_and_stop(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"""<!doctype html><video autoplay muted playsinline></video><script>
                const canvas = document.createElement('canvas');
                canvas.width = 160; canvas.height = 90;
                const context = canvas.getContext('2d');
                setInterval(() => { context.fillStyle = '#f33'; context.fillRect(0, 0, 160, 90); }, 50);
                const video = document.querySelector('video');
                video.srcObject = canvas.captureStream(15); video.play();
            </script>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    monkeypatch.setenv("CHROMIUM_PATH", os.environ["TEST_CHROMIUM_PATH"])
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    bot = ViewerBot(2, "AbCDef12_-X", timeout=10000, reconnect=False)
    # Only the test substitutes a local synthetic player after validating a YouTube ID.
    bot.channel_url = f"http://127.0.0.1:{server.server_port}/"
    thread = threading.Thread(target=bot.main, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = bot.snapshot()
            if status["active_connections"] == 2 or status["status"]["state"] == "error":
                break
            time.sleep(0.05)
        assert bot.snapshot()["active_connections"] == 2, bot.snapshot()
        assert bot.snapshot()["successful_sessions"] == 2
    finally:
        bot.stop()
        thread.join(10)
        server.shutdown()
        server.server_close()
    assert not thread.is_alive()
    assert bot.snapshot()["active_connections"] == 0
    assert bot.snapshot()["status"]["state"] == "stopped"


@pytest.mark.skipif(not os.getenv("TEST_CHROMIUM_PATH"), reason="Set TEST_CHROMIUM_PATH to run a real-browser smoke test")
def test_real_browser_fails_over_to_local_proxy(monkeypatch):
    """A dead local proxy is followed by a synthetic player served by a local proxy."""
    import socket

    requests = []

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            body = b"""<!doctype html><video autoplay muted playsinline></video><script>
                const canvas = document.createElement('canvas');
                canvas.width = 160; canvas.height = 90;
                const context = canvas.getContext('2d');
                setInterval(() => { context.fillStyle = '#f33'; context.fillRect(0, 0, 160, 90); }, 50);
                const video = document.querySelector('video');
                video.srcObject = canvas.captureStream(15); video.play();
            </script>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    monkeypatch.setenv("CHROMIUM_PATH", os.environ["TEST_CHROMIUM_PATH"])
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    # Keep this socket bound but not listening, guaranteeing connection refusal.
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    bot = ViewerBot(1, "AbCDef12_-X", timeout=5000, reconnect=False,
                    proxy_text=f"127.0.0.1:{dead_port}\n127.0.0.1:{proxy.server_port}")
    # The reserved .invalid name is served entirely by our local proxy, not the internet.
    bot.channel_url = "http://synthetic.invalid/"
    thread = threading.Thread(target=bot.main, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = bot.snapshot()
            if status["active_connections"] == 1 or status["status"]["state"] == "error":
                break
            time.sleep(0.05)
        status = bot.snapshot()
        assert status["active_connections"] == 1, status
        assert status["request_count"] == 2 and status["failed_attempts"] == 1, status
        assert status["last_error"]["code"] == "proxy_connection_failed", status
        assert any("synthetic.invalid" in request for request in requests)
    finally:
        bot.stop()
        thread.join(10)
        dead.close()
        proxy.shutdown()
        proxy.server_close()
    assert not thread.is_alive()
    assert bot.snapshot()["status"]["state"] == "stopped"
