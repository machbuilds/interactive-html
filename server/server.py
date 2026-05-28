"""
Interactive HTML — local server.

Serves a directory of static HTML pages plus the sibling client/ folder under
/client/*. Accepts comment batches at POST /comments and appends them to
<artifact>/.ih/comments.jsonl. The agent reads that file, edits the HTML,
and appends to <artifact>/.ih/updates.json which the in-page client polls.

Stdlib only. No external dependencies.

    python server/server.py <artifact_dir> [--port 5050] [--idle-timeout 600]
"""
from __future__ import annotations

import argparse
import http.server
import json
import mimetypes
import os
import queue
import socketserver
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

# Sibling client/ folder lives at <repo>/client. Resolved once at startup.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLIENT_DIR = (PROJECT_ROOT / "client").resolve()

META_DIR_NAME = ".ih"
COMMENTS_FILE = "comments.jsonl"
UPDATES_FILE = "updates.json"
SEEN_FILE = "seen.json"

NO_CACHE_HEADERS = (
    ("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"),
    ("Pragma", "no-cache"),
    ("Expires", "0"),
)

UTF8_CONTENT_TYPES = {
    "application/javascript",
    "application/json",
    "application/xml",
}


def with_utf8_charset(content_type: str) -> str:
    if not content_type:
        return content_type
    needs = content_type.startswith("text/") or content_type in UTF8_CONTENT_TYPES
    if needs and "charset=" not in content_type.lower():
        return f"{content_type}; charset=utf-8"
    return content_type


class Broadcaster:
    """Fan-out for Server-Sent Events subscribers. Each subscriber gets its
    own bounded queue — slow consumers drop events rather than blocking
    publishers, so a stuck browser tab can't wedge the server."""

    QUEUE_SIZE = 64

    def __init__(self) -> None:
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self.QUEUE_SIZE)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def publish(self, event_name: str, data: str) -> None:
        envelope = {"name": event_name, "data": data}
        with self._lock:
            current = list(self._subscribers)
        for q in current:
            try:
                q.put_nowait(envelope)
            except queue.Full:
                pass

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


class Liveness:
    """Tracks parentage + last-request time so a watchdog can retire the
    process when the parent exits or no clients have called in a while."""

    def __init__(self, idle_timeout_s: float):
        self.idle_timeout_s = idle_timeout_s
        self.initial_ppid = os.getppid()
        self.detached_at_start = self.initial_ppid == 1
        self._lock = threading.Lock()
        self._last_request = time.monotonic()

    def touch(self) -> None:
        with self._lock:
            self._last_request = time.monotonic()

    def seconds_idle(self) -> float:
        with self._lock:
            return time.monotonic() - self._last_request

    def retirement_reason(self) -> str | None:
        if not self.detached_at_start and os.getppid() == 1:
            return "parent process exited"
        if self.idle_timeout_s > 0 and self.seconds_idle() > self.idle_timeout_s:
            return f"idle >{int(self.idle_timeout_s)}s"
        return None


def build_handler_class(
    artifact_dir: Path,
    meta_dir: Path,
    liveness: Liveness,
    broadcaster: Broadcaster,
):
    """Return a request-handler class closed over our per-server state. Using a
    closure (vs. mutating class attributes on a shared handler class) keeps
    state scoped to this server instance — friendlier to tests and to running
    multiple instances in the same process."""

    artifact_str = str(artifact_dir)
    client_str = str(CLIENT_DIR)
    client_prefix_sep = client_str + os.sep

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=artifact_str, **kwargs)

        # -- response shaping -------------------------------------------------
        def end_headers(self) -> None:
            liveness.touch()
            for name, value in NO_CACHE_HEADERS:
                self.send_header(name, value)
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()

        def guess_type(self, path):
            return with_utf8_charset(super().guess_type(path))

        # -- routing ---------------------------------------------------------
        def do_OPTIONS(self) -> None:  # noqa: N802 — stdlib API
            self.send_response(204)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/_ih/info":
                self._respond_json(200, self._info_payload())
                return
            if path == "/_ih/events":
                self._handle_event_stream()
                return
            if path.startswith("/client/"):
                self._serve_client_asset(path[len("/client/"):])
                return
            super().do_GET()

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/comments":
                self._receive_comments()
                return
            if path == "/_ih/seen":
                self._record_seen()
                return
            self._respond_json(404, {"ok": False, "error": "unknown endpoint"})

        # -- handlers --------------------------------------------------------
        def _info_payload(self) -> dict:
            return {
                "name": "interactive-html",
                "artifact_dir": artifact_str,
                "meta_dir": str(meta_dir),
                "client_dir": client_str,
                "port": self.server.server_address[1],
            }

        def _serve_client_asset(self, rel: str) -> None:
            try:
                target = (CLIENT_DIR / rel).resolve()
            except OSError:
                self.send_error(404)
                return
            target_str = str(target)
            if not (target_str == client_str or target_str.startswith(client_prefix_sep)):
                self.send_error(403, "forbidden")
                return
            if not target.is_file():
                self.send_error(404)
                return
            mime_guess, _ = mimetypes.guess_type(target.name)
            mime = with_utf8_charset(mime_guess or "application/octet-stream")
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict | None:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None

        def _receive_comments(self) -> None:
            body = self._read_json_body()
            if body is None:
                self._respond_json(400, {"ok": False, "error": "invalid json"})
                return
            now = time.time()
            body["received_at"] = now
            body["received_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            target = meta_dir / COMMENTS_FILE
            with target.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(body, ensure_ascii=False) + "\n")
            count = len(body.get("comments") or [])
            sys.stdout.write(f"[ih] received batch ({count} comment(s)) → {target}\n")
            sys.stdout.flush()
            self._respond_json(200, {"ok": True, "received": count})

        def _handle_event_stream(self) -> None:
            """SSE handler. Holds the connection open and forwards events
            published on the broadcaster until the client disconnects."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            # Disable proxy buffering (nginx, etc.) — irrelevant on localhost
            # but cheap to include and means this Just Works behind a proxy.
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            sub = broadcaster.subscribe()
            try:
                self._sse_write("ready", json.dumps({"now": time.time()}))
                last_ping = time.monotonic()
                while True:
                    # An open SSE connection is proof of a live client — keep
                    # the idle-timeout watchdog from retiring the server while
                    # someone has a browser tab open.
                    liveness.touch()
                    try:
                        envelope = sub.get(timeout=1.0)
                    except queue.Empty:
                        if time.monotonic() - last_ping > 15:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                            last_ping = time.monotonic()
                        continue
                    self._sse_write(envelope["name"], envelope["data"])
                    last_ping = time.monotonic()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                broadcaster.unsubscribe(sub)

        def _sse_write(self, event_name: str, data: str) -> None:
            chunks = [f"event: {event_name}\n".encode("utf-8")]
            for line in data.splitlines() or [""]:
                chunks.append(f"data: {line}\n".encode("utf-8"))
            chunks.append(b"\n")
            for c in chunks:
                self.wfile.write(c)
            self.wfile.flush()

        def _record_seen(self) -> None:
            body = self._read_json_body() or {}
            (meta_dir / SEEN_FILE).write_text(json.dumps(body, indent=2), encoding="utf-8")
            self._respond_json(200, {"ok": True})

        # -- utility ---------------------------------------------------------
        def _respond_json(self, status: int, payload: dict) -> None:
            blob = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def log_message(self, fmt: str, *args) -> None:
            line = fmt % args
            interesting = line.startswith(("POST", "PUT", "DELETE")) or " 4" in line or " 5" in line
            if interesting:
                sys.stderr.write(f"{self.address_string()} - {line}\n")

    return Handler


class ReusableThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def watchdog(liveness: Liveness) -> None:
    while True:
        time.sleep(5)
        reason = liveness.retirement_reason()
        if reason:
            sys.stdout.write(f"[ih] retiring: {reason}\n")
            sys.stdout.flush()
            os._exit(0)


def updates_watcher(meta_dir: Path, broadcaster: Broadcaster) -> None:
    """Poll updates.json's mtime once a second. When it changes, broadcast an
    'updates' event so connected browsers refetch immediately instead of
    waiting for their poll interval."""
    path = meta_dir / UPDATES_FILE
    last_mtime = path.stat().st_mtime if path.exists() else 0.0
    while True:
        time.sleep(1)
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime > last_mtime + 0.001:
            last_mtime = mtime
            broadcaster.publish("updates", json.dumps({"mtime": mtime}))


def prepare_meta_dir(artifact_dir: Path) -> Path:
    meta = artifact_dir / META_DIR_NAME
    meta.mkdir(exist_ok=True)
    (meta / COMMENTS_FILE).touch(exist_ok=True)
    updates = meta / UPDATES_FILE
    if not updates.exists():
        updates.write_text("[]", encoding="utf-8")
    return meta


def main() -> int:
    p = argparse.ArgumentParser(description="Interactive HTML server")
    p.add_argument("artifact_dir", help="directory containing .html files")
    p.add_argument("--port", type=int, default=5050)
    p.add_argument("--host", default="", help="bind host (default: all interfaces)")
    p.add_argument(
        "--idle-timeout",
        type=int,
        default=600,
        help="auto-retire after this many seconds without a client request (0 disables)",
    )
    args = p.parse_args()

    artifact = Path(args.artifact_dir).resolve()
    if not artifact.is_dir():
        print(f"[ih] error: {artifact} is not a directory", file=sys.stderr)
        return 1
    if not CLIENT_DIR.is_dir():
        print(f"[ih] error: client directory missing: {CLIENT_DIR}", file=sys.stderr)
        return 1

    meta = prepare_meta_dir(artifact)
    liveness = Liveness(idle_timeout_s=args.idle_timeout)
    broadcaster = Broadcaster()
    handler_cls = build_handler_class(artifact, meta, liveness, broadcaster)

    try:
        srv = ReusableThreadingServer((args.host, args.port), handler_cls)
    except OSError as e:
        print(f"[ih] port {args.port} unavailable: {e}", file=sys.stderr)
        print(f"   curl http://localhost:{args.port}/_ih/info   # see what's holding it", file=sys.stderr)
        print(f"   lsof -ti:{args.port} | xargs kill            # free it", file=sys.stderr)
        print(f"   --port {args.port + 1}                       # or pick another", file=sys.stderr)
        return 1

    threading.Thread(target=watchdog, args=(liveness,), daemon=True).start()
    threading.Thread(target=updates_watcher, args=(meta, broadcaster), daemon=True).start()

    with srv:
        host = args.host or "localhost"
        print(f"[ih] serving   {artifact}")
        print(f"[ih] open      http://{host}:{args.port}/")
        print(f"[ih] info      http://{host}:{args.port}/_ih/info")
        print(f"[ih] events    http://{host}:{args.port}/_ih/events  (SSE)")
        print(f"[ih] comments  {meta / COMMENTS_FILE}")
        print(f"[ih] updates   {meta / UPDATES_FILE}")
        if args.idle_timeout > 0:
            print(f"[ih] retire    parent-death OR {args.idle_timeout}s idle (override with --idle-timeout)")
        else:
            print(f"[ih] retire    parent-death only (idle timeout disabled)")
        print(f"[ih] Ctrl-C to stop")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n[ih] stopping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
