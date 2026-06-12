from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import load_module

watch = load_module("cli/watch.py", "ih_watch")


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.meta = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_inbox(self, lines):
        path = self.meta / watch.COMMENTS_FILE
        path.write_text("\n".join(json.dumps(x) if not isinstance(x, str) else x for x in lines) + "\n")

    def test_read_batches_parses_well_formed_lines(self):
        self.write_inbox([
            {"batch_id": "b-1", "comments": []},
            "not json at all",
            {"batch_id": "b-2", "comments": []},
            {"no_batch_id": True},
        ])
        batches = watch.read_batches(self.meta)
        self.assertEqual([b["batch_id"] for b in batches], ["b-1", "b-2"])

    def test_cursor_roundtrip(self):
        watch.save_cursor(self.meta, {"b-1", "b-2"})
        self.assertEqual(watch.load_cursor(self.meta), {"b-1", "b-2"})

    def test_cursor_missing_file_is_empty(self):
        self.assertEqual(watch.load_cursor(self.meta), set())

    def test_prompt_embeds_batch(self):
        batch = {"batch_id": "b-xyz", "comments": [{"id": "c-1", "intent": "question", "body": "?"}]}
        prompt = watch.build_prompt(batch)
        self.assertIn("b-xyz", prompt)
        self.assertIn('"intent": "question"', prompt)
        self.assertIn("answers", prompt)  # schema must mention the answers array

    def test_default_agent_cmd_has_permission_mode(self):
        # headless claude refuses edits without an accept mode — regression guard
        self.assertIn("--permission-mode acceptEdits", watch.DEFAULT_AGENT_CMD)


if __name__ == "__main__":
    unittest.main()
