"""
Inject (or remove) the Interactive HTML client tags in every *.html file in a
directory. Also seeds <dir>/.ih/comments.jsonl and <dir>/.ih/updates.json so
the server has somewhere to write.

Running twice is a no-op — files already wired are left untouched. Files with
no </head> or no </body> are reported and skipped.

    python cli/inject.py <dir> [--remove] [--recursive]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CSS_HREF = "/client/ih.css"
JS_SRC = "/client/ih.js"
CSS_TAG = f'<link rel="stylesheet" href="{CSS_HREF}">'
JS_TAG = f'<script src="{JS_SRC}" defer></script>'

CSS_TAG_RE = re.compile(
    r'[ \t]*<link[^>]*href=["\']' + re.escape(CSS_HREF) + r'["\'][^>]*>\s*\n?',
    re.IGNORECASE,
)
JS_TAG_RE = re.compile(
    r'[ \t]*<script[^>]*src=["\']' + re.escape(JS_SRC) + r'["\'][^>]*></script>\s*\n?',
    re.IGNORECASE,
)

META_DIR_NAME = ".ih"


def already_has_css(text: str) -> bool:
    return CSS_HREF in text


def already_has_js(text: str) -> bool:
    return JS_SRC in text


def inject_into_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    new_text = text
    issues: list[str] = []
    wrote_css = False
    wrote_js = False

    if not already_has_css(new_text):
        if "</head>" in new_text:
            new_text = new_text.replace("</head>", f"  {CSS_TAG}\n</head>", 1)
            wrote_css = True
        else:
            issues.append("no </head>")
    if not already_has_js(new_text):
        if "</body>" in new_text:
            new_text = new_text.replace("</body>", f"  {JS_TAG}\n</body>", 1)
            wrote_js = True
        else:
            issues.append("no </body>")

    if wrote_css or wrote_js:
        path.write_text(new_text, encoding="utf-8")
        result = "wired"
    elif already_has_css(new_text) and already_has_js(new_text):
        result = "already wired"
    else:
        result = "skipped"
    if issues:
        result += " (" + ", ".join(issues) + ")"
    return result


def strip_from_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    after, n_css = CSS_TAG_RE.subn("", text)
    after, n_js = JS_TAG_RE.subn("", after)
    if n_css == 0 and n_js == 0:
        return "no tags found"
    path.write_text(after, encoding="utf-8")
    return f"stripped (css={n_css}, js={n_js})"


def html_files(root: Path, recursive: bool) -> list[Path]:
    pattern = "**/*.html" if recursive else "*.html"
    return sorted(
        p for p in root.glob(pattern)
        if META_DIR_NAME not in p.parts
    )


def seed_meta_dir(root: Path) -> Path:
    meta = root / META_DIR_NAME
    meta.mkdir(exist_ok=True)
    (meta / "comments.jsonl").touch(exist_ok=True)
    updates = meta / "updates.json"
    if not updates.exists():
        updates.write_text("[]", encoding="utf-8")
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dir", help="directory containing the HTML pages")
    parser.add_argument("--remove", action="store_true", help="strip the tags instead of injecting")
    parser.add_argument("--recursive", "-r", action="store_true", help="walk subdirectories")
    args = parser.parse_args()

    root = Path(args.dir).resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    files = html_files(root, args.recursive)
    if not files:
        print(f"no *.html files in {root}")
        return 0

    apply = strip_from_file if args.remove else inject_into_file
    verb = "removing tags from" if args.remove else "wiring"
    print(f"{verb} {len(files)} file(s) under {root}:")
    for path in files:
        rel = path.relative_to(root)
        print(f"  {rel}  →  {apply(path)}")

    if not args.remove:
        meta = seed_meta_dir(root)
        print(f"\nmeta dir ready: {meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
