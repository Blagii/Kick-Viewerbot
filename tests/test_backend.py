import base64
import threading
import time

import pytest

import backend.main as backend
from backend.api.youtube import normalize_youtube_target


class FakeBot:
    def __init__(self, threads, channel_name, proxy_text=None, timeout=15000, type_of_proxy="http", reconnect=True):
        self.channel_url = normalize_youtube_target(channel_name)
        self.nb_of_threads = int(threads)
        self.timeout = timeout
        self.reconnect = reconnect
        self.proxy_text = proxy_text
        self.stopping = threading.Event()
        self.release = threading.Event()

    def main(self): self.release.wait(2)
    def stop(self): self.stopping.set()
    def snapshot(self):
        return {"active_threads": 0, "active_connections": 0, "request_count": 0,
                "successful_sessions": 0, "total_proxies": 0, "video_id": "AbCDef12_-X",
                "status": {"state": "stopping" if self.stopping.is_set() else "running", "message": "ok",
                           "proxy_count": 0, "proxy_loading_progress": 0, "startup_progress": 100}}


@pytest.fixture
def manager(monkeypatch):
    monkeypatch.setattr(backend, "ViewerBot", FakeBot)
    manager = backend.BotManager()
    monkeypatch.setattr(backend, "bot_manager", manager)
    yield manager
    if manager.bot:
        manager.bot.release.set()
    if manager.bot_thread:
        manager.bot_thread.join(3)


def test_idle_stats_expose_empty_session_state_counts(manager):
    counts = manager.get_stats()["session_counts"]
    assert counts == {state: 0 for state in backend.SESSION_STATES}


def test_manager_prevents_overlapping_start_and_stop(manager):
    assert manager.start_bot("AbCDef12_-X", 1)["success"]
    assert not manager.start_bot("AbCDef12_-X", 1)["success"]
    assert manager.stop_bot()["success"]
    assert manager.is_running  # remains running until the old thread actually exits
    assert not manager.start_bot("AbCDef12_-X", 1)["success"]
    manager.bot.release.set()
    manager.bot_thread.join(2)
    assert not manager.is_running
    assert manager.start_bot("AbCDef12_-X", 1)["success"]


def test_socket_ack_validates_and_returns_stats(manager):
    client = backend.socketio.test_client(backend.app)
    assert client.is_connected()
    assert not client.emit("start_bot", {"channelName": "https://kick.com/a", "threads": 1}, callback=True)["success"]
    assert not client.emit("start_bot", {"channelName": "AbCDef12_-X", "threads": "abc"}, callback=True)["success"]
    assert not client.emit("start_bot", {"channelName": "AbCDef12_-X", "proxyFileData": "***"}, callback=True)["success"]
    text = "user:password@example.com:8080"
    started = client.emit("start_bot", {"channelName": "AbCDef12_-X", "threads": 1,
                           "proxyFileData": base64.b64encode(text.encode()).decode()}, callback=True)
    assert started["success"] and manager.bot.proxy_text == text
    client.emit("get_stats")
    updates = [event for event in client.get_received() if event["name"] == "stats_update"]
    assert updates and updates[-1]["args"][0]["is_running"]
    assert "password" not in str(updates)
    assert client.emit("stop_bot", callback=True)["success"]
    client.disconnect()


def test_viewer_endpoint_handles_missing_key_invalid_url_and_channel_resolution(manager, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    client = backend.app.test_client()
    response = client.get("/api/youtube/viewers", query_string={"target": "https://kick.com/x"})
    assert response.status_code == 400
    response = client.get("/api/youtube/viewers", query_string={"target": "AbCDef12_-X"})
    assert response.json["viewer_count"] is None and "YOUTUBE_API_KEY" in response.json["message"]
    assert manager.start_bot("@SomeChannel", 1)["success"]
    response = client.get("/api/youtube/viewers", query_string={"target": "@SomeChannel"})
    assert response.json["video_id"] == "AbCDef12_-X"
