"""Offline tests for the one-click launcher and portable package layout."""

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import package_windows as packager
from scripts import start_windows as launcher


def touch(path, content="test"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_portable_runtime_uses_bundled_executables_and_clean_python_env(tmp_path, monkeypatch):
    root = tmp_path / "YouTube Viewer with spaces"
    python = touch(root / "runtime/python/python.exe")
    node = touch(root / "runtime/node/node.exe")
    server = touch(root / "frontend/server.js")
    monkeypatch.setattr(launcher, "validate_node", lambda node, env: "v20.0.0")
    env = {"PYTHONHOME": "wrong", "PYTHONPATH": "wrong", "YOUTUBE_API_KEY": "key"}
    runtime = launcher.prepare_runtime(root, env)
    assert runtime.portable and runtime.python == python and runtime.node == node
    assert runtime.frontend_command == (node, server)
    assert env["PYTHONHOME"] == str(python.parent)
    assert env["PYTHONNOUSERSITE"] == "1" and "PYTHONPATH" not in env
    assert env["YOUTUBE_API_KEY"] == "key"


def test_incomplete_portable_package_does_not_silently_fall_back(tmp_path):
    touch(tmp_path / "runtime/python/python.exe")
    with pytest.raises(launcher.LauncherError, match="incomplete"):
        launcher.prepare_runtime(tmp_path, {})


def test_validate_node_checks_minimum_version(monkeypatch, tmp_path):
    class Result:
        stdout = "v18.17.1\n"
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(launcher.LauncherError, match="18.18"):
        launcher.validate_node(tmp_path / "node", {})
    Result.stdout = "v20.11.0\n"
    assert launcher.validate_node(tmp_path / "node", {}) == "v20.11.0"


def test_source_dependencies_install_once_and_reinstall_after_changes(tmp_path, monkeypatch):
    root = tmp_path / "source"
    requirements = touch(root / "requirements.txt", "flask\n")
    lockfile = touch(root / "frontend/package-lock.json", "lock v1")
    python = touch(launcher.venv_python(root))
    node = touch(tmp_path / "node")
    npm = touch(tmp_path / "npm-cli.js")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: str(node))
    monkeypatch.setattr(launcher, "validate_node", lambda *args: "v20.11.0")
    monkeypatch.setattr(launcher, "find_npm_cli", lambda *args: npm)
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0))
    commands = []

    def fake_install(command, description, **kwargs):
        commands.append(description)
        if "dashboard" in description:
            touch(root / "frontend/node_modules/next/dist/bin/next")

    monkeypatch.setattr(launcher, "run_checked", fake_install)
    result = launcher.prepare_source(root, {})
    assert not result.portable and result.python == python
    assert commands == ["Installing Python dependencies", "Installing dashboard dependencies"]
    commands.clear()
    launcher.prepare_source(root, {})
    assert not commands
    lockfile.write_text("lock v2", encoding="utf-8")
    launcher.prepare_source(root, {})
    assert commands == ["Installing dashboard dependencies"]
    commands.clear()
    requirements.write_text("flask\nrequests\n", encoding="utf-8")
    launcher.prepare_source(root, {})
    assert commands == ["Installing Python dependencies"]


def test_existing_non_windows_venv_is_not_deleted(tmp_path, monkeypatch):
    root = tmp_path / "project"
    touch(root / ".venv/keep-me.txt", "important")
    touch(root / "requirements.txt")
    touch(root / "frontend/package-lock.json")
    node = touch(tmp_path / "node")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: str(node))
    monkeypatch.setattr(launcher, "validate_node", lambda *args: "v20.0.0")
    monkeypatch.setattr(launcher, "find_npm_cli", lambda *args: tmp_path / "npm")
    with pytest.raises(launcher.LauncherError, match="existing .venv"):
        launcher.prepare_source(root, {})
    assert (root / ".venv/keep-me.txt").read_text() == "important"


def test_custom_chromium_path_is_validated_without_downloading(tmp_path, monkeypatch):
    browser = touch(tmp_path / "chromium.exe")
    runtime = launcher.Runtime(tmp_path / "python", tmp_path / "node", (), tmp_path, True)
    monkeypatch.setattr(launcher, "run_checked", lambda *args, **kwargs: pytest.fail("unexpected download"))
    launcher.ensure_chromium(runtime, tmp_path, {"CHROMIUM_PATH": str(browser)})
    with pytest.raises(launcher.LauncherError, match="CHROMIUM_PATH"):
        launcher.ensure_chromium(runtime, tmp_path, {"CHROMIUM_PATH": str(tmp_path / "missing.exe")})


def test_browser_install_is_automatic_on_first_run(tmp_path, monkeypatch):
    runtime = launcher.Runtime(tmp_path / "python.exe", tmp_path / "node.exe", (), tmp_path, True)
    calls = []
    monkeypatch.setattr(launcher, "run_checked", lambda command, description, **kw: calls.append((command, description)))
    launcher.ensure_chromium(runtime, tmp_path, {})
    assert calls == [([runtime.python, "-m", "playwright", "install", "chromium"], "Installing Chromium")]


def test_busy_ports_are_reported_without_terminating_other_processes(monkeypatch):
    monkeypatch.setattr(launcher, "port_is_free", lambda port: port != launcher.FRONTEND_PORT)
    monkeypatch.setattr(launcher, "backend_ready", lambda: False)
    with pytest.raises(launcher.LauncherError, match="3000"):
        launcher.check_ports(smoke_test=False)


def test_second_launch_reopens_an_existing_dashboard(monkeypatch):
    opened = []
    monkeypatch.setattr(launcher, "port_is_free", lambda port: False)
    monkeypatch.setattr(launcher, "backend_ready", lambda: True)
    monkeypatch.setattr(launcher, "frontend_ready", lambda: True)
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    assert launcher.check_ports(smoke_test=False)
    assert opened == [launcher.DASHBOARD_URL + "/"]
    with pytest.raises(launcher.LauncherError, match="already in use"):
        launcher.check_ports(smoke_test=True)


def test_child_processes_are_cleaned_up():
    children = launcher.ChildProcesses()
    process = None
    try:
        with open(os.devnull, "wb") as output:
            process = children.start([sys.executable, "-c", "import time; time.sleep(60)"],
                                     cwd=None, env=os.environ.copy(), output=output)
            assert process.poll() is None
    finally:
        children.close()
    assert process.poll() is not None


def make_build_inputs(tmp_path):
    root = tmp_path / "source"
    for relative in (*packager.BACKEND_FILES, *packager.ROOT_FILES):
        touch(root / relative, relative)
    touch(root / "frontend/.next/standalone/server.js", "server")
    touch(root / "frontend/.next/standalone/.next/BUILD_ID", "build-id")
    touch(root / "frontend/.next/static/chunks/main.js", "static")
    touch(root / "frontend/public/icon.svg", "icon")
    python = tmp_path / "python-runtime"
    touch(python / "python.exe", "python")
    touch(python / "Lib/site-packages/playwright/__init__.py", "browser")
    touch(python / "Lib/site-packages/playwright/__pycache__/skip.pyc", "cache")
    node = touch(tmp_path / "node-runtime/node.exe", "node")
    touch(node.parent / "LICENSE", "node license")
    return root, python, node


def test_packager_includes_runtime_dashboard_and_hidden_next_files(tmp_path):
    root, python, node = make_build_inputs(tmp_path)
    output = tmp_path / "Windows Package"
    assert packager.package(root, output, python, node) == output
    for relative in (
        "Start YouTube Viewer.bat", "README_WINDOWS.txt", "scripts/start_windows.py",
        "backend/api/youtube.py", "frontend/server.js", "frontend/.next/BUILD_ID",
        "frontend/.next/static/chunks/main.js", "frontend/public/icon.svg",
        "runtime/python/python.exe", "runtime/python/Lib/site-packages/playwright/__init__.py",
        "runtime/node/node.exe", "runtime/node/LICENSE.txt",
    ):
        assert (output / relative).is_file(), relative
    assert not list(output.rglob("*.pyc"))


def test_packager_never_deletes_or_overwrites_nonempty_output(tmp_path):
    root, python, node = make_build_inputs(tmp_path)
    output = tmp_path / "existing"
    original = touch(output / "important.txt", "preserve")
    with pytest.raises(FileExistsError):
        packager.package(root, output, python, node)
    assert original.read_text() == "preserve"


def test_packager_rejects_missing_inputs_and_internal_output(tmp_path):
    root, python, node = make_build_inputs(tmp_path)
    (node.parent / "LICENSE").unlink()
    with pytest.raises(FileNotFoundError, match="license"):
        packager.package(root, tmp_path / "output", python, node)
    touch(node.parent / "LICENSE")
    with pytest.raises(ValueError, match="outside"):
        packager.package(root, root / "output", python, node)


@pytest.mark.skipif(os.name == "nt", reason="Windows runs the real packaged launcher in CI")
def test_full_launcher_starts_local_servers_and_cleans_up(tmp_path, monkeypatch):
    """Exercise lifecycle and paths with spaces, without network downloads or YouTube."""
    root = tmp_path / "viewer folder with spaces"
    backend_script = touch(root / "backend/main.py", '''
import http.server, json, sys
port = int(sys.argv[sys.argv.index("--port") + 1])
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps({"service": "YouTube Viewer Bot", "status": "healthy"}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(data)
    def log_message(self, *args): pass
http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
''')
    frontend_script = touch(root / "frontend/server.py", '''
import http.server, os
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'0{"sid":"local-test"}' if self.path.startswith("/socket.io/") else b"YouTube Viewer"
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass
http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
''')
    sockets = [socket.socket(), socket.socket()]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        back_port, front_port = [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()
    monkeypatch.setattr(launcher, "BACKEND_PORT", back_port)
    monkeypatch.setattr(launcher, "FRONTEND_PORT", front_port)
    monkeypatch.setattr(launcher, "BACKEND_URL", f"http://127.0.0.1:{back_port}")
    monkeypatch.setattr(launcher, "DASHBOARD_URL", f"http://127.0.0.1:{front_port}")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local data"))
    runtime = launcher.Runtime(Path(sys.executable), Path(sys.executable),
                               (sys.executable, frontend_script), frontend_script.parent, True)
    monkeypatch.setattr(launcher, "prepare_runtime", lambda root, env: runtime)
    monkeypatch.setattr(launcher, "ensure_chromium", lambda *args: None)
    launcher.launch(root, smoke_test=True)
    assert launcher.port_is_free(back_port) and launcher.port_is_free(front_port)
    assert (tmp_path / "local data/YouTubeViewer/logs/backend.log").is_file()
    assert (tmp_path / "local data/YouTubeViewer/logs/frontend.log").is_file()
    assert backend_script.exists()
