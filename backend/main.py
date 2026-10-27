"""Local Socket.IO service for the YouTube playback dashboard."""

import argparse
import base64
import binascii
import logging
import os
import sys
import threading
import time
from pathlib import Path

# Support both `python backend/main.py` and `python -m backend.main`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psutil
from flask import Flask, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit

from backend.api.viewer_bot import SESSION_STATES, ViewerBot
from backend.api.youtube import fetch_live_viewers, normalize_youtube_target, youtube_video_id

logging.basicConfig(
    level=getattr(logging, os.getenv("YOUTUBE_BOT_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("youtube_viewer_bot")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading", max_http_buffer_size=2 * 1024 * 1024)


class BotManager:
    def __init__(self):
        self.bot = None
        self.bot_thread = None
        self.is_running = False
        self.last_channel = None
        self.config = {}
        self._lock = threading.RLock()
        self.last_net_io = psutil.net_io_counters()
        self.last_net_io_time = time.monotonic()

    def start_bot(self, channel_name, threads, proxy_text=None, timeout=15000, proxy_type="http", reconnect=True):
        with self._lock:
            if self.bot_thread and self.bot_thread.is_alive():
                return {"success": False, "error": "A session is already running or stopping."}
            try:
                bot = ViewerBot(threads, channel_name, proxy_text=proxy_text, timeout=timeout,
                                type_of_proxy=proxy_type, reconnect=reconnect)
            except (ValueError, TypeError) as exc:
                return {"success": False, "error": str(exc)}

            self.bot = bot
            self.last_channel = bot.channel_url
            self.config = {"threads": bot.nb_of_threads, "timeout": bot.timeout,
                           "proxy_type": proxy_type, "reconnect": bot.reconnect}
            self.is_running = True  # Set before the thread starts, not inside it.

            def run_bot():
                try:
                    bot.main()
                finally:
                    with self._lock:
                        if self.bot is bot:
                            self.is_running = False

            self.bot_thread = threading.Thread(target=run_bot, name="youtube-viewer-bot", daemon=True)
            self.bot_thread.start()
            return {"success": True, "message": "Starting YouTube sessions. Check the status for playback results."}

    def stop_bot(self):
        with self._lock:
            if not self.is_running or not self.bot:
                return {"success": False, "error": "No session is running."}
            self.bot.stop()
            return {"success": True, "message": "Stopping YouTube sessions."}

    def get_stats(self):
        with self._lock:
            now = time.monotonic()
            net = psutil.net_io_counters()
            elapsed = max(now - self.last_net_io_time, 0.1)
            system_metrics = {
                "cpu": psutil.cpu_percent() or 0,
                "memory": psutil.virtual_memory().percent or 0,
                "network_up": max((net.bytes_sent - self.last_net_io.bytes_sent) / elapsed / 1048576, 0),
                "network_down": max((net.bytes_recv - self.last_net_io.bytes_recv) / elapsed / 1048576, 0),
            }
            self.last_net_io, self.last_net_io_time = net, now
            details = self.bot.snapshot() if self.bot else {
                "active_threads": 0, "active_connections": 0,
                "session_counts": {state: 0 for state in SESSION_STATES}, "request_count": 0,
                "successful_sessions": 0, "failed_attempts": 0, "last_error": None,
                "total_proxies": 0, "video_id": None,
                "status": {"state": "stopped", "message": "Ready to start YouTube playback.",
                           "proxy_count": 0, "proxy_loading_progress": 0, "startup_progress": 0,
                           "error_code": None},
            }
            return {"is_running": self.is_running, "channel_name": self.last_channel,
                    "config": dict(self.config), "system_metrics": system_metrics, **details}


bot_manager = BotManager()


@socketio.on("connect")
def handle_connect():
    emit("connected", {"message": "Connected to YouTube Viewer Bot", "status": "ok"})


@socketio.on("start_bot")
def handle_start_bot(data):
    try:
        if not isinstance(data, dict):
            raise ValueError("Invalid start request.")
        proxy_text = None
        if "proxyFileData" in data:
            encoded = data["proxyFileData"]
            if not isinstance(encoded, str) or len(encoded) > 1_400_000:
                raise ValueError("Proxy file is too large (maximum 1 MB).")
            try:
                decoded = base64.b64decode(encoded, validate=True)
                if len(decoded) > 1_000_000:
                    raise ValueError("Proxy file is too large (maximum 1 MB).")
                proxy_text = decoded.decode("utf-8-sig")
            except (binascii.Error, UnicodeDecodeError):
                raise ValueError("Proxy file must be UTF-8 text.") from None
        reconnect = data.get("reconnect", True)
        if not isinstance(reconnect, bool):
            raise ValueError("Reconnect must be true or false.")
        result = bot_manager.start_bot(
            channel_name=data.get("channelName", ""),
            threads=int(data.get("threads", 1)),
            proxy_text=proxy_text,
            timeout=int(data.get("timeout", 15000)),
            proxy_type=data.get("proxyType", "http"),
            reconnect=reconnect,
        )
    except (ValueError, TypeError) as exc:
        result = {"success": False, "error": str(exc)}
    if result["success"]:
        emit("bot_started", result)
    else:
        emit("bot_error", {"error": result["error"]})
    return result


@socketio.on("stop_bot")
def handle_stop_bot():
    result = bot_manager.stop_bot()
    if result["success"]:
        emit("bot_stopped", result)
    else:
        emit("bot_error", {"error": result["error"]})
    return result


@socketio.on("get_stats")
def handle_get_stats():
    emit("stats_update", bot_manager.get_stats())


@app.get("/health")
def health():
    return {"status": "healthy", "service": "YouTube Viewer Bot", "bot_running": bot_manager.is_running}


@app.get("/api/youtube/viewers")
def live_viewers():
    target = request.args.get("target", "")
    try:
        canonical = normalize_youtube_target(target)
        video_id = youtube_video_id(canonical)
    except ValueError as exc:
        return {"viewer_count": None, "message": str(exc)}, 400
    # A channel /live URL may redirect to a specific video in the actual browser.
    stats = bot_manager.get_stats()
    if not video_id and canonical == stats.get("channel_name"):
        video_id = stats.get("video_id")
    count, message = fetch_live_viewers(video_id, os.getenv("YOUTUBE_API_KEY", ""))
    return {"viewer_count": count, "message": message, "video_id": video_id}, 200, {"Cache-Control": "no-store"}


@app.get("/")
def index():
    return {"service": "YouTube Viewer Bot", "status": "online"}


def main():
    parser = argparse.ArgumentParser(description="Run the YouTube Viewer Bot backend")
    parser.add_argument("--host", default="127.0.0.1", help="Interface to bind (default: localhost)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Accepted for compatibility; the backend does not open a browser.")
    args = parser.parse_args()
    logger.info("YouTube Viewer Bot listening on %s:%s", args.host, args.port)
    socketio.run(app, host=args.host, port=args.port, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
