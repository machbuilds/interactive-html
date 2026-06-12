from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from helpers import load_module

server = load_module("server/server.py", "ih_server")

PAGE = "<!doctype html><html><head></head><body><h1>t</h1></body></html>"


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.artifact = Path(cls.tmp.name)
        (cls.artifact / "page.html").write_text(PAGE)
        cls.meta = server.prepare_meta_dir(cls.artifact)
        liveness = server.Liveness(idle_timeout_s=0)
        broadcaster = server.Broadcaster()
        handler = server.build_handler_class(cls.artifact, cls.meta, liveness, broadcaster)
        cls.srv = server.ReusableThreadingServer(("127.0.0.1", 0), handler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()
        cls.broadcaster = broadcaster

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str):
        return urllib.request.urlopen(self.url(path), timeout=5)

    def post(self, path: str, body: bytes, ctype="application/json"):
        req = urllib.request.Request(self.url(path), data=body, method="POST")
        req.add_header("Content-Type", ctype)
        return urllib.request.urlopen(req, timeout=5)

    def test_serves_artifact_page(self):
        with self.get("/page.html") as r:
            self.assertEqual(r.status, 200)
            self.assertIn(b"<h1>t</h1>", r.read())

    def test_info_endpoint(self):
        with self.get("/_ih/info") as r:
            info = json.loads(r.read())
        self.assertEqual(info["name"], "interactive-html")
        self.assertEqual(info["artifact_dir"], str(self.artifact))

    def test_serves_client_assets(self):
        with self.get("/client/ih.js") as r:
            self.assertEqual(r.status, 200)
        with self.get("/client/ih.css") as r:
            self.assertEqual(r.status, 200)

    def test_client_path_traversal_blocked(self):
        # urllib normalizes "..", so issue the raw request over a socket
        import socket
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET /client/../server/server.py HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        data = s.recv(200).decode()
        s.close()
        status = int(data.split()[1])
        self.assertIn(status, (403, 404))

    def test_comments_post_appends_jsonl(self):
        batch = {"batch_id": "b-test", "comments": [{"id": "c-1", "body": "x"}]}
        with self.post("/comments", json.dumps(batch).encode()) as r:
            reply = json.loads(r.read())
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["received"], 1)
        lines = (self.meta / "comments.jsonl").read_text().strip().splitlines()
        stored = json.loads(lines[-1])
        self.assertEqual(stored["batch_id"], "b-test")
        self.assertIn("received_at", stored)

    def test_comments_rejects_invalid_json(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/comments", b"{nope")
        self.assertEqual(ctx.exception.code, 400)

    def test_unknown_post_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/nope", b"{}")
        self.assertEqual(ctx.exception.code, 404)

    def test_seen_endpoint_writes_file(self):
        with self.post("/_ih/seen", json.dumps({"last": "u-1"}).encode()):
            pass
        seen = json.loads((self.meta / "seen.json").read_text())
        self.assertEqual(seen["last"], "u-1")

    def test_broadcaster_fanout_and_overflow(self):
        b = server.Broadcaster()
        q = b.subscribe()
        b.publish("updates", "{}")
        self.assertEqual(q.get(timeout=1)["name"], "updates")
        for _ in range(server.Broadcaster.QUEUE_SIZE + 10):
            b.publish("updates", "{}")  # overflow must not raise
        b.unsubscribe(q)
        self.assertEqual(b.subscriber_count(), 0)


if __name__ == "__main__":
    unittest.main()
