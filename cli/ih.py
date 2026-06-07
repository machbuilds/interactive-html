"""
Interactive HTML — one-command launcher.

Collapses the three-terminal dance (inject → serve → watch) into a single
supervised process. Point it at a folder of HTML (or none, to use the current
directory), and it injects the client tags, starts the server, optionally
starts the comment watcher, prints the URLs, and opens your browser.

    python cli/ih.py                  # current directory
    python cli/ih.py ./mydir          # a specific folder
    python cli/ih.py -r               # include HTML in subfolders
    python cli/ih.py --no-watch       # serve + capture comments only (no agent)
    python cli/ih.py --agent builtin  # use the bundled agent (needs ANTHROPIC_API_KEY)
    python cli/ih.py --port 6000 --no-open

Ctrl-C stops everything it started.
"""
from __future__ import annotations

import argparse
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INJECT = REPO_ROOT / "cli" / "inject.py"
SERVER = REPO_ROOT / "server" / "server.py"
WATCH = REPO_ROOT / "cli" / "watch.py"
META_DIR_NAME = ".ih"


def find_html(root: Path, recursive: bool) -> list[Path]:
    pattern = "**/*.html" if recursive else "*.html"
    return sorted(p for p in root.glob(pattern) if META_DIR_NAME not in p.parts)


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(preferred: int, attempts: int = 12) -> int | None:
    for candidate in range(preferred, preferred + attempts):
        if port_is_free(candidate):
            return candidate
    return None


def agent_preflight(agent: str, agent_cmd: str | None) -> tuple[bool, str]:
    """Return (ok, message). When not ok, the launcher serves without a watcher
    so the page still works and comments are still captured to disk."""
    if agent == "builtin":
        import os
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return False, "ANTHROPIC_API_KEY is not set — needed for --agent builtin"
        return True, "builtin agent"
    # cli
    cmd = agent_cmd or ""  # watch.py supplies the real default when None
    binary = (cmd.split() or ["claude"])[0] if cmd else "claude"
    if shutil.which(binary) is None:
        return False, f"agent command {binary!r} not found on PATH"
    return True, binary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", nargs="?", default=".", help="folder of HTML pages (default: current directory)")
    ap.add_argument("--port", type=int, default=5050)
    ap.add_argument("--recursive", "-r", action="store_true", help="include HTML in subfolders")
    ap.add_argument("--agent", choices=["cli", "builtin", "none"], default="cli")
    ap.add_argument("--agent-cmd", default=None, help="override the cli agent command")
    ap.add_argument("--no-watch", action="store_true", help="don't start the agent watcher")
    ap.add_argument("--no-open", action="store_true", help="don't open a browser")
    ap.add_argument("--idle-timeout", type=int, default=0, help="server idle auto-shutdown (0 = never; the launcher supervises lifecycle)")
    args = ap.parse_args()

    root = Path(args.dir).resolve()
    if not root.is_dir():
        print(f"[ih] error: {root} is not a directory", file=sys.stderr)
        return 1

    pages = find_html(root, args.recursive)
    if not pages:
        print(f"[ih] no .html files found in {root}")
        print("[ih] add an .html file there (or pass a folder that has one) and re-run.")
        print("[ih] tip: inside a Claude session just say \"make this page interactive\".")
        return 1

    # Inject tags.
    inject_cmd = [sys.executable, str(INJECT), str(root)]
    if args.recursive:
        inject_cmd.append("-r")
    inject = subprocess.run(inject_cmd)
    if inject.returncode != 0:
        return inject.returncode

    # Pick a port.
    port = pick_port(args.port)
    if port is None:
        print(f"[ih] no free port near {args.port}", file=sys.stderr)
        return 1
    if port != args.port:
        print(f"[ih] port {args.port} busy → using {port}")

    # Decide whether to run a watcher.
    want_watch = not args.no_watch and args.agent != "none"
    watch_ok, watch_msg = (False, "watcher disabled")
    if want_watch:
        watch_ok, watch_msg = agent_preflight(args.agent, args.agent_cmd)
        if not watch_ok:
            print(f"[ih] {watch_msg}")
            print("[ih] serving without an agent — comments will still be captured to")
            print(f"[ih]   {root / META_DIR_NAME / 'comments.jsonl'}")

    procs: list[subprocess.Popen] = []

    def shutdown(*_):
        for p in procs:
            if p.poll() is None:
                p.terminate()
        deadline = time.monotonic() + 5
        for p in procs:
            remaining = max(0, deadline - time.monotonic())
            try:
                p.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                p.kill()
        print("\n[ih] stopped.")

    signal.signal(signal.SIGINT, lambda *_: (shutdown(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (shutdown(), sys.exit(0)))

    # Start server.
    server = subprocess.Popen([
        sys.executable, str(SERVER), str(root),
        "--port", str(port),
        "--idle-timeout", str(args.idle_timeout),
    ])
    procs.append(server)

    # Start watcher.
    if want_watch and watch_ok:
        watch_cmd = [sys.executable, str(WATCH), str(root), "--agent", args.agent]
        if args.agent == "cli" and args.agent_cmd:
            watch_cmd += ["--agent-cmd", args.agent_cmd]
        procs.append(subprocess.Popen(watch_cmd))

    # Report URLs.
    base = f"http://localhost:{port}"
    print()
    print(f"[ih] serving {root}")
    print(f"[ih] agent   {watch_msg if (want_watch and watch_ok) else 'none (capture-only)'}")
    print("[ih] pages:")
    for p in pages:
        print(f"[ih]   {base}/{p.relative_to(root).as_posix()}")
    print("[ih] Ctrl-C to stop")

    if not args.no_open:
        first = pages[0].relative_to(root).as_posix()
        try:
            webbrowser.open(f"{base}/{first}")
        except Exception:
            pass

    # Supervise: exit if a child dies.
    try:
        while True:
            time.sleep(1)
            for p in procs:
                if p.poll() is not None:
                    print(f"[ih] a child process exited ({p.args[1] if len(p.args) > 1 else p.args}); shutting down")
                    shutdown()
                    return 1
    except KeyboardInterrupt:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
