from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import load_module

agent = load_module("agent/agent.py", "ih_agent")

PAGE = "<!doctype html><html><head></head><body><p>alpha</p><p>beta</p></body></html>"


class AgentToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "page.html").write_text(PAGE)
        (self.root / ".ih").mkdir()
        (self.root / ".ih" / "updates.json").write_text("[]")

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_pages(self):
        pages = json.loads(agent.tool_list_pages(self.root, {}))
        self.assertEqual(pages, ["page.html"])

    def test_read_file(self):
        text = agent.tool_read_file(self.root, {"path": "page.html"})
        self.assertIn("alpha", text)

    def test_read_blocks_traversal(self):
        with self.assertRaises(agent.ToolError):
            agent.tool_read_file(self.root, {"path": "../outside.txt"})

    def test_write_allowlist_blocks_arbitrary_files(self):
        with self.assertRaises(agent.ToolError):
            agent.tool_write_file(self.root, {"path": "notes.txt", "content": "x"})

    def test_write_allows_html_and_updates(self):
        agent.tool_write_file(self.root, {"path": "new.html", "content": "<html></html>"})
        agent.tool_write_file(self.root, {"path": ".ih/updates.json", "content": "[]"})
        self.assertTrue((self.root / "new.html").exists())

    def test_edit_requires_unique_match(self):
        (self.root / "page.html").write_text("<p>dup</p><p>dup</p>")
        with self.assertRaises(agent.ToolError):
            agent.tool_edit_file(self.root, {"path": "page.html", "old_string": "dup", "new_string": "x"})

    def test_edit_replace_all(self):
        (self.root / "page.html").write_text("<p>dup</p><p>dup</p>")
        agent.tool_edit_file(self.root, {
            "path": "page.html", "old_string": "dup", "new_string": "x", "replace_all": True,
        })
        self.assertNotIn("dup", (self.root / "page.html").read_text())

    def test_edit_missing_string(self):
        with self.assertRaises(agent.ToolError):
            agent.tool_edit_file(self.root, {"path": "page.html", "old_string": "zzz", "new_string": "x"})

    def test_extract_batch_id(self):
        prompt = 'stuff "batch_id": "b-abc123", more'
        self.assertEqual(agent.extract_batch_id(prompt), "b-abc123")
        self.assertEqual(agent.extract_batch_id("no id here"), "unknown")

    def test_progress_write_is_atomic_and_valid(self):
        agent.write_progress(self.root / ".ih", "b-1", "working on it")
        payload = json.loads((self.root / ".ih" / "progress.json").read_text())
        self.assertEqual(payload["batch_id"], "b-1")
        self.assertFalse((self.root / ".ih" / "progress.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
