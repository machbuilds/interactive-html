from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import load_module

inject = load_module("cli/inject.py", "ih_inject")

PAGE = "<!doctype html><html><head><title>t</title></head><body><p>hi</p></body></html>"
HEADLESS = "<p>no head or body tags</p>"


class InjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        p = self.root / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_inject_adds_both_tags(self):
        p = self.write("a.html", PAGE)
        result = inject.inject_into_file(p)
        self.assertEqual(result, "wired")
        text = p.read_text()
        self.assertIn(inject.CSS_HREF, text)
        self.assertIn(inject.JS_SRC, text)

    def test_inject_is_idempotent(self):
        p = self.write("a.html", PAGE)
        inject.inject_into_file(p)
        first = p.read_text()
        result = inject.inject_into_file(p)
        self.assertEqual(result, "already wired")
        self.assertEqual(p.read_text(), first)

    def test_remove_strips_tags(self):
        p = self.write("a.html", PAGE)
        inject.inject_into_file(p)
        result = inject.strip_from_file(p)
        self.assertIn("stripped", result)
        text = p.read_text()
        self.assertNotIn(inject.CSS_HREF, text)
        self.assertNotIn(inject.JS_SRC, text)

    def test_remove_on_clean_file_is_noop(self):
        p = self.write("a.html", PAGE)
        self.assertEqual(inject.strip_from_file(p), "no tags found")
        self.assertEqual(p.read_text(), PAGE)

    def test_file_without_head_or_body_is_skipped(self):
        p = self.write("frag.html", HEADLESS)
        result = inject.inject_into_file(p)
        self.assertIn("skipped", result)
        self.assertEqual(p.read_text(), HEADLESS)

    def test_meta_dir_seeded(self):
        meta = inject.seed_meta_dir(self.root)
        self.assertTrue((meta / "comments.jsonl").exists())
        self.assertEqual((meta / "updates.json").read_text(), "[]")

    def test_html_files_skips_meta_dir(self):
        self.write("a.html", PAGE)
        meta = self.root / ".ih"
        meta.mkdir()
        (meta / "ghost.html").write_text(PAGE)
        files = inject.html_files(self.root, recursive=True)
        self.assertEqual([p.name for p in files], ["a.html"])


if __name__ == "__main__":
    unittest.main()
