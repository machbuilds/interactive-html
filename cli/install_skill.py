"""
Install Interactive HTML as a self-contained Claude Code skill.

Assembles a standalone copy of the skill — SKILL.md plus the runtime
(client/, server/, cli/, agent/) — into the destination directory and bakes
the destination's absolute path into SKILL.md. The result depends on nothing
outside its own folder, so it keeps working even if this repo moves or is
deleted, and it can be copied to any machine.

    python cli/install_skill.py                 # → ~/.claude/skills/interactive-html
    python cli/install_skill.py --dest PATH     # custom location
    python cli/install_skill.py --force         # overwrite an existing install

Re-run any time to sync the installed skill with the repo.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_SRC = REPO_ROOT / "skill" / "SKILL.md"
RUNTIME_DIRS = ["client", "server", "cli", "agent"]
TOKEN = "__IH_HOME__"
DEFAULT_DEST = Path.home() / ".claude" / "skills" / "interactive-html"

# Don't copy these into the installed skill.
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.tmp", ".DS_Store")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"install location (default: {DEFAULT_DEST})")
    ap.add_argument("--force", action="store_true", help="overwrite an existing install")
    args = ap.parse_args()

    if not SKILL_SRC.is_file():
        print(f"error: {SKILL_SRC} not found", file=sys.stderr)
        return 1

    dest = args.dest.expanduser().resolve()
    if dest.exists():
        if not args.force:
            print(f"error: {dest} already exists — pass --force to overwrite", file=sys.stderr)
            return 1
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    # Copy the runtime.
    for name in RUNTIME_DIRS:
        src = REPO_ROOT / name
        if not src.is_dir():
            print(f"error: missing runtime dir {src}", file=sys.stderr)
            return 1
        shutil.copytree(src, dest / name, ignore=IGNORE)

    # Write SKILL.md with the destination path baked in.
    skill_text = SKILL_SRC.read_text(encoding="utf-8").replace(TOKEN, str(dest))
    (dest / "SKILL.md").write_text(skill_text, encoding="utf-8")

    print(f"installed self-contained skill → {dest}")
    print(f"  SKILL.md + {', '.join(RUNTIME_DIRS)}/  (runtime baked to {dest})")
    print()
    if dest == DEFAULT_DEST:
        print("Active now. In any Claude Code session, say:")
        print('  "make this page interactive"')
    else:
        print("To activate, ensure this path is on Claude Code's skill search path,")
        print(f"or symlink it:  ln -s {dest} ~/.claude/skills/interactive-html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
