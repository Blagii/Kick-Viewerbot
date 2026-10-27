"""Create the self-contained Windows download after installing and building.

Run on Windows with Python dependencies installed and `npm run build` complete.
The result bundles Python and Node; Chromium is installed on first launch.
"""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND_FILES = (
    "backend/__init__.py",
    "backend/main.py",
    "backend/api/__init__.py",
    "backend/api/viewer_bot.py",
    "backend/api/youtube.py",
)
ROOT_FILES = ("Start YouTube Viewer.bat", "README_WINDOWS.txt", "LICENSE.txt", "scripts/start_windows.py")


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def package(root, output, python_root, node):
    root, output, python_root, node = (Path(path).resolve() for path in (root, output, python_root, node))
    standalone = root / "frontend" / ".next" / "standalone"
    static = root / "frontend" / ".next" / "static"
    public = root / "frontend" / "public"
    node_license = next((node.parent / name for name in ("LICENSE", "LICENSE.txt")
                         if (node.parent / name).is_file()), None)
    if not node_license:
        raise FileNotFoundError("The Node.js license was not found next to node.exe.")
    required = [python_root / "python.exe", node, standalone / "server.js", static, public]
    required.extend(root / file for file in (*BACKEND_FILES, *ROOT_FILES))
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Build inputs are missing: " + ", ".join(missing))
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {output}")
    if output == root or root in output.parents or python_root == output or python_root in output.parents:
        raise ValueError("Choose an output directory outside the source and Python runtime directories.")

    output.mkdir(parents=True, exist_ok=True)
    for name in (*BACKEND_FILES, *ROOT_FILES):
        copy_file(root / name, output / name)
    shutil.copytree(standalone, output / "frontend", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(static, output / "frontend" / ".next" / "static", dirs_exist_ok=True)
    shutil.copytree(public, output / "frontend" / "public", dirs_exist_ok=True)
    shutil.copytree(python_root, output / "runtime" / "python", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    copy_file(node, output / "runtime" / "node" / "node.exe")
    copy_file(node_license, output / "runtime" / "node" / "LICENSE.txt")
    return output


def main():
    parser = argparse.ArgumentParser(description="Build the portable Windows folder")
    parser.add_argument("--output", required=True, help="A new, empty output directory outside the checkout")
    parser.add_argument("--python-root", default=str(Path(sys.executable).resolve().parent))
    parser.add_argument("--node", required=True, help="Path to the Windows node.exe")
    args = parser.parse_args()
    try:
        output = package(ROOT, args.output, args.python_root, args.node)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Packaging failed: {exc}\n")
    print(f"Windows package created: {output}")


if __name__ == "__main__":
    main()
