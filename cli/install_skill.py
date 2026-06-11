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
SKILLS_SRC = REPO_ROOT / "skills"
RUNTIME_DIRS = ["client", "server", "cli", "agent"]
TOKEN = "__IH_HOME__"
DEFAULT_SKILLS_HOME = Path.home() / ".claude" / "skills"

# interactive-html carries the runtime; html-designer is prompt-only.
SKILLS = {
    "interactive-html": {"runtime": True},
    "html-designer": {"runtime": False},
}

# Don't copy these into the installed skill.
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.tmp", ".DS_Store")


def install_one(name: str, runtime: bool, skills_home: Path, force: bool) -> Path | None:
    src_md = SKILLS_SRC / name / "SKILL.md"
    if not src_md.is_file():
        print(f"error: {src_md} not found", file=sys.stderr)
        return None
    dest = skills_home / name
    if dest.exists():
        if not force:
            print(f"error: {dest} already exists — pass --force to overwrite", file=sys.stderr)
            return None
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    if runtime:
        for rt in RUNTIME_DIRS:
            rt_src = REPO_ROOT / rt
            if not rt_src.is_dir():
                print(f"error: missing runtime dir {rt_src}", file=sys.stderr)
                return None
            shutil.copytree(rt_src, dest / rt, ignore=IGNORE)

    skill_text = src_md.read_text(encoding="utf-8").replace(TOKEN, str(dest))
    (dest / "SKILL.md").write_text(skill_text, encoding="utf-8")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_SKILLS_HOME,
        help=f"skills directory to install into (default: {DEFAULT_SKILLS_HOME})",
    )
    ap.add_argument("--force", action="store_true", help="overwrite existing installs")
    args = ap.parse_args()

    skills_home = args.dest.expanduser().resolve()
    installed = []
    for name, opts in SKILLS.items():
        dest = install_one(name, opts["runtime"], skills_home, args.force)
        if dest is None:
            return 1
        suffix = f" + {', '.join(RUNTIME_DIRS)}/ (self-contained runtime)" if opts["runtime"] else " (prompt-only)"
        print(f"installed {name} → {dest}{suffix}")

        installed.append(name)

    print()
    if skills_home == DEFAULT_SKILLS_HOME:
        print("Active now. In any Claude Code session, say:")
        print('  "make this page interactive"   (interactive-html)')
        print('  "build me a page about …"      (html-designer)')
    else:
        print("To activate, ensure this path is on Claude Code's skill search path,")
        print(f"or symlink each: ln -s {skills_home}/<name> ~/.claude/skills/<name>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
