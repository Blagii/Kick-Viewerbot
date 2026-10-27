"""Launch the Windows source checkout or self-contained Windows download.

The batch file starts this script. The portable download contains Python,
Node.js and the production dashboard; only Playwright's browser is downloaded
on first launch. A source checkout can use locally installed Python and Node.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND_PORT = 8765
FRONTEND_PORT = 3000
BACKEND_URL = f"http://127.0.0.1:{BACKEND_PORT}"
DASHBOARD_URL = f"http://127.0.0.1:{FRONTEND_PORT}"
MIN_PYTHON = (3, 9)
MIN_NODE = (18, 18, 0)
STARTUP_TIMEOUT = 180
# The launcher's health checks are strictly local, even when Windows has a
# system HTTP proxy configured. Playback proxy settings are unaffected.
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class LauncherError(Exception):
    pass


@dataclass(frozen=True)
class Runtime:
    python: Path
    node: Path
    frontend_command: tuple
    frontend_cwd: Path
    portable: bool


def run_checked(command, description, *, cwd=None, env=None):
    try:
        subprocess.run([str(part) for part in command], cwd=cwd, env=env, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LauncherError(f"{description} failed. Check the output above and your internet connection.") from exc


def read_stamp(path):
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        return ""


def file_signature(path, extra=""):
    digest = hashlib.sha256(path.read_bytes())
    digest.update(extra.encode("utf-8"))
    return digest.hexdigest()


def validate_node(node, env):
    try:
        result = subprocess.run([str(node), "--version"], env=env, capture_output=True,
                                text=True, check=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise LauncherError("Could not start Node.js. Install 64-bit Node.js 22 or use the portable build.") from exc
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", result.stdout.strip())
    version = tuple(int(part) for part in match.groups()) if match else (0, 0, 0)
    if version < MIN_NODE:
        raise LauncherError("Node.js 18.18 or newer is required; Node.js 22 is recommended.")
    return result.stdout.strip()


def find_npm_cli(node):
    """Find npm's JavaScript entry point, avoiding cmd.exe quoting problems."""
    candidate = node.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    if candidate.is_file():
        return candidate
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm:
        npm_path = Path(npm).resolve()
        if npm_path.name == "npm-cli.js":
            return npm_path
        candidate = npm_path.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if candidate.is_file():
            return candidate
    raise LauncherError("npm was not found. Install the official Node.js distribution or use the portable build.")


def venv_python(root):
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def prepare_source(root, env):
    node_name = "node.exe" if os.name == "nt" else "node"
    node_path = shutil.which(node_name)
    if not node_path:
        raise LauncherError("Node.js was not found. Install Node.js 22 or download the portable Windows build.")
    node = Path(node_path).resolve()
    node_version = validate_node(node, env)
    npm_cli = find_npm_cli(node)
    requirements = root / "requirements.txt"
    lockfile = root / "frontend" / "package-lock.json"
    if not requirements.is_file() or not lockfile.is_file():
        raise LauncherError("The project files are incomplete. Extract or clone the complete project first.")

    python = venv_python(root)
    venv = root / ".venv"
    if not python.is_file():
        if venv.exists() and any(venv.iterdir()):
            raise LauncherError("The existing .venv is not a Windows Python environment. Rename it and try again.")
        print("Creating a local Python environment...", flush=True)
        run_checked([sys.executable, "-m", "venv", venv], "Creating the Python environment", cwd=root, env=env)
    if not python.is_file():
        raise LauncherError("The Python environment was not created successfully.")

    requirement_hash = file_signature(requirements, f"{sys.version_info[:3]}|{sys.platform}")
    python_stamp = venv / ".youtube-viewer-requirements"
    imports = subprocess.run(
        [str(python), "-c", "import flask, flask_socketio, psutil, playwright, requests"],
        cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if read_stamp(python_stamp) != requirement_hash or imports.returncode:
        print("Installing Python dependencies (first launch or after an update)...", flush=True)
        run_checked([python, "-m", "pip", "install", "-r", requirements], "Installing Python dependencies", cwd=root, env=env)
        python_stamp.write_text(requirement_hash, encoding="ascii")

    frontend = root / "frontend"
    next_cli = frontend / "node_modules" / "next" / "dist" / "bin" / "next"
    node_stamp = frontend / "node_modules" / ".youtube-viewer-lock"
    node_hash = file_signature(lockfile, f"{node_version}|{sys.platform}")
    if read_stamp(node_stamp) != node_hash or not next_cli.is_file():
        print("Installing dashboard dependencies (first launch or after an update)...", flush=True)
        run_checked([node, npm_cli, "ci"], "Installing dashboard dependencies", cwd=frontend, env=env)
        if not next_cli.is_file():
            raise LauncherError("Dashboard dependencies are incomplete. Check the npm output above.")
        node_stamp.write_text(node_hash, encoding="ascii")

    return Runtime(python, node,
                   (node, next_cli, "dev", "--turbopack", "--hostname", "127.0.0.1", "--port", str(FRONTEND_PORT)),
                   frontend, False)


def prepare_runtime(root, env):
    portable_python = root / "runtime" / "python" / "python.exe"
    portable_node = root / "runtime" / "node" / "node.exe"
    server = root / "frontend" / "server.js"
    if portable_python.exists() or portable_node.exists() or server.exists():
        if not all(path.is_file() for path in (portable_python, portable_node, server)):
            raise LauncherError("The portable package is incomplete. Extract the entire downloaded ZIP and try again.")
        env["PYTHONHOME"] = str(portable_python.parent)
        env["PYTHONNOUSERSITE"] = "1"
        env.pop("PYTHONPATH", None)
        validate_node(portable_node, env)
        print("Using the included Python, Node.js and dashboard.", flush=True)
        return Runtime(portable_python, portable_node, (portable_node, server), server.parent, True)

    # PYTHONHOME can break virtual environments; a source checkout uses its own venv.
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    print("Running from source. Python and Node.js are needed for the first setup.", flush=True)
    return prepare_source(root, env)


def ensure_chromium(runtime, root, env):
    custom = env.get("CHROMIUM_PATH")
    if custom:
        if not Path(custom).is_file():
            raise LauncherError("CHROMIUM_PATH does not point to an existing browser executable.")
        print("Using the browser specified by CHROMIUM_PATH.", flush=True)
        return
    print("Checking Chromium (the first download can take several minutes)...", flush=True)
    run_checked([runtime.python, "-m", "playwright", "install", "chromium"],
                "Installing Chromium", cwd=root, env=env)


def port_is_free(port):
    # A bind-only probe may fail on recently closed TIME_WAIT sockets and stop
    # a quick restart. Check for an active listener; the OS still enforces the
    # actual bind when each server starts.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def read_url(url, timeout=2):
    with LOCAL_OPENER.open(url, timeout=timeout) as response:
        return response.read()


def backend_ready():
    try:
        data = json.loads(read_url(f"{BACKEND_URL}/health"))
        return data.get("service") == "YouTube Viewer Bot" and data.get("status") == "healthy"
    except (OSError, ValueError, AttributeError, urllib.error.URLError):
        return False


def frontend_ready():
    try:
        return b"YouTube" in read_url(DASHBOARD_URL + "/")
    except (OSError, urllib.error.URLError):
        return False


def check_ports(smoke_test):
    busy = [port for port in (BACKEND_PORT, FRONTEND_PORT) if not port_is_free(port)]
    if not busy:
        return False
    if not smoke_test and len(busy) == 2 and backend_ready() and frontend_ready():
        print("YouTube Viewer is already running; opening the dashboard.", flush=True)
        webbrowser.open(DASHBOARD_URL + "/")
        return True
    ports = ", ".join(str(port) for port in busy)
    raise LauncherError(f"Port(s) {ports} are already in use. Close the other app or the previous launcher and try again.")


class ChildProcesses:
    """Keep child processes in a Windows job that dies when this window closes."""

    def __init__(self):
        self.processes = []
        self.job = None
        if os.name == "nt":
            self.job = WindowsJob()

    def start(self, command, *, cwd, env, output):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            process = subprocess.Popen([str(part) for part in command], cwd=cwd, env=env,
                                       stdout=output, stderr=subprocess.STDOUT, creationflags=flags)
        except OSError as exc:
            raise LauncherError(f"Could not start {Path(str(command[0])).name}.") from exc
        self.processes.append(process)
        if self.job:
            try:
                self.job.assign(process)
            except Exception:
                process.terminate()
                raise
        return process

    def close(self):
        # Closing the job also terminates any browser subprocesses it owns.
        if self.job:
            self.job.close()
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


class WindowsJob:
    def __init__(self):
        import ctypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation), ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._ctypes = ctypes
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
        self._kernel.CreateJobObjectW.restype = ctypes.c_void_p
        self._kernel.SetInformationJobObject.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
        self._kernel.SetInformationJobObject.restype = ctypes.c_int
        self._kernel.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        self._kernel.AssignProcessToJobObject.restype = ctypes.c_int
        self._kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        self._kernel.CloseHandle.restype = ctypes.c_int
        self.handle = self._kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise LauncherError("Windows could not create a process group for the app.")
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise LauncherError("Windows could not configure process cleanup for the app.")

    def assign(self, process):
        if not self._kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise LauncherError("Windows could not attach a process to the launcher. Try closing conflicting process managers.")

    def close(self):
        if self.handle:
            self._kernel.CloseHandle(self.handle)
            self.handle = None


def wait_ready(name, process, predicate, log_path):
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LauncherError(f"{name} exited during startup. See {log_path}.")
        if predicate():
            return
        time.sleep(0.25)
    raise LauncherError(f"{name} did not become ready in time. See {log_path}.")


def run_smoke_test():
    try:
        response = read_url(DASHBOARD_URL + "/socket.io/?EIO=4&transport=polling")
    except (OSError, urllib.error.URLError) as exc:
        raise LauncherError("The dashboard could not reach the backend Socket.IO service.") from exc
    if not response.startswith(b'0{"sid":'):
        raise LauncherError("The dashboard returned an unexpected Socket.IO response.")
    print("Smoke test passed: dashboard, backend and Socket.IO are reachable.", flush=True)


def wait_for_stop(backend, frontend):
    print("\nLeave this window open while using the app. Press Enter here to stop it.", flush=True)
    if os.name == "nt" and sys.stdin.isatty():
        import msvcrt
        while True:
            if backend.poll() is not None or frontend.poll() is not None:
                raise LauncherError("A server stopped unexpectedly. Check the log files shown above.")
            if msvcrt.kbhit() and msvcrt.getwch() in ("\r", "\n"):
                return
            time.sleep(0.2)
    elif sys.stdin.isatty():
        input()
    else:
        # Non-interactive callers should normally use --smoke-test.
        while backend.poll() is None and frontend.poll() is None:
            time.sleep(0.5)
        raise LauncherError("A server stopped unexpectedly. Check the log files shown above.")


def launch(root, smoke_test=False):
    if sys.version_info < MIN_PYTHON:
        raise LauncherError("Python 3.9 or newer is required. Use the portable Windows build for a bundled Python.")
    if os.name == "nt" and sys.maxsize < 2 ** 32:
        raise LauncherError("A 64-bit Python is required. Use the portable Windows build or install 64-bit Python.")
    if check_ports(smoke_test):
        return
    env = os.environ.copy()
    env["BACKEND_URL"] = BACKEND_URL
    env["HOSTNAME"] = "127.0.0.1"
    env["PORT"] = str(FRONTEND_PORT)
    env["NEXT_TELEMETRY_DISABLED"] = "1"
    runtime = prepare_runtime(root, env)
    ensure_chromium(runtime, root, env)

    # Re-check after setup, which can take minutes on the first run.
    if check_ports(smoke_test):
        return
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".youtube-viewer")
    log_dir = base / "YouTubeViewer" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    backend_log = log_dir / "backend.log"
    frontend_log = log_dir / "frontend.log"
    print(f"Logs: {log_dir}", flush=True)
    children = ChildProcesses()
    try:
        with ExitStack() as stack:
            backend_out = stack.enter_context(backend_log.open("wb"))
            frontend_out = stack.enter_context(frontend_log.open("wb"))
            print("Starting the backend...", flush=True)
            backend = children.start(
                (runtime.python, "-u", root / "backend" / "main.py", "--host", "127.0.0.1", "--port", str(BACKEND_PORT)),
                cwd=root, env=env, output=backend_out,
            )
            wait_ready("Backend", backend, backend_ready, backend_log)
            print("Starting the dashboard...", flush=True)
            frontend = children.start(runtime.frontend_command, cwd=runtime.frontend_cwd, env=env, output=frontend_out)
            wait_ready("Dashboard", frontend, frontend_ready, frontend_log)
            print(f"Ready: {DASHBOARD_URL}/", flush=True)
            if smoke_test:
                run_smoke_test()
            else:
                webbrowser.open(DASHBOARD_URL + "/")
                wait_for_stop(backend, frontend)
    finally:
        children.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Start the YouTube Viewer dashboard")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        launch(ROOT, smoke_test=args.smoke_test)
        return 0
    except KeyboardInterrupt:
        print("\nStopping YouTube Viewer.", flush=True)
        return 0
    except (LauncherError, OSError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
