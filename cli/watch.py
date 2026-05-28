"""
Interactive HTML — comment watcher.

Polls <artifact>/.ih/comments.jsonl for new batches and dispatches each one
to an agent CLI. The agent is expected to follow the protocol baked into
PROMPT_TEMPLATE — edit the HTML in place, wrap changes with data-ih-change
anchors, and append a matching entry to .ih/updates.json.

A small cursor file (<meta>/.watch-cursor.json) tracks which batch_ids have
been processed so restarting the watcher doesn't re-trigger old comments.

    python cli/watch.py <artifact_dir> [--agent-cmd "claude -p"]
                                       [--interval 2]
                                       [--dry-run]
                                       [--once]

The default agent command is `claude -p`, which works with Claude Code's
headless mode (file tools enabled, runs in the artifact directory). Override
with --agent-cmd if you're piping the prompt to a different CLI.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

META_DIR_NAME = ".ih"
COMMENTS_FILE = "comments.jsonl"
UPDATES_FILE = "updates.json"
CURSOR_FILE = ".watch-cursor.json"
LOG_FILE = "watch.log"

DEFAULT_AGENT_CMD = "claude -p"
DEFAULT_INTERVAL = 2.0

PROMPT_TEMPLATE = """\
You are responding to in-page feedback on HTML pages in the current directory.

A reader of the page just submitted this batch of comments:

{batch_json}

YOUR JOB
1. For each comment in `comments`, locate the element it refers to. Use
   `anchor.html_snippet` and `anchor.quote` to find it — do NOT search by
   `anchor.selector` if it includes `data-ih-id`, because those attributes
   are assigned client-side and are not persisted to disk. Match on the
   visible text or the html_snippet instead.
2. Edit the relevant .html file(s) to address each comment. Keep changes
   minimal and focused on what the comment asked for.
3. For each logical change, wrap the modified region in
   `<span data-ih-change="ch-<slug>">…</span>`, OR add a `data-ih-change`
   attribute to an existing wrapping element. Exactly one anchor per change.
4. When all edits are done, append a SINGLE batch object to
   `.ih/updates.json`. That file is a JSON array — read it, append your
   new entry to the end, and write the whole array back. Schema:

   {{
     "batch_id": "u-<short slug>",
     "timestamp": "<ISO 8601 UTC, e.g. 2026-05-28T11:19:26Z>",
     "in_response_to_batch": "{batch_id}",
     "changes": [
       {{
         "id": "ch-<slug>",
         "anchor": "ch-<slug>",
         "in_response_to": ["<comment id from the batch above>"],
         "title": "short label of what changed",
         "description": "one or two sentences on what changed and why"
       }}
     ]
   }}

RULES
- Edit only .html files and `.ih/updates.json`. Do not touch
  `.ih/comments.jsonl` or other files.
- Every `id` you list in updates.json must have a matching
  `data-ih-change` somewhere in the HTML, and every new `data-ih-change`
  must be listed in updates.json.
- Do not start any servers or background processes.
- Do not ask follow-up questions; do the work and finish.
"""


def stamp() -> str:
    return time.strftime("%H:%M:%S")


def log(meta: Path, msg: str) -> None:
    line = f"[{stamp()}] {msg}"
    print(line, flush=True)
    try:
        with (meta / LOG_FILE).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_cursor(meta: Path) -> set[str]:
    path = meta / CURSOR_FILE
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return set(data.get("processed", []))


def save_cursor(meta: Path, processed: set[str]) -> None:
    payload = {"processed": sorted(processed)}
    (meta / CURSOR_FILE).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_batches(meta: Path) -> list[dict]:
    """Return all well-formed batches in comments.jsonl, in file order."""
    path = meta / COMMENTS_FILE
    if not path.exists():
        return []
    batches: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("batch_id"):
            batches.append(obj)
    return batches


def build_prompt(batch: dict) -> str:
    return PROMPT_TEMPLATE.format(
        batch_json=json.dumps(batch, indent=2, ensure_ascii=False),
        batch_id=batch["batch_id"],
    )


def run_agent(prompt: str, artifact_dir: Path, agent_cmd: str) -> tuple[bool, str]:
    cmd = shlex.split(agent_cmd)
    if not cmd:
        return False, "agent command is empty"
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=str(artifact_dir),
            check=False,
        )
    except FileNotFoundError as e:
        return False, f"agent command not found: {cmd[0]} ({e})"
    out = (proc.stdout or "").strip()
    if out:
        # Stream a short tail of the agent's response into the live log.
        tail = out if len(out) <= 1200 else out[:1200] + "…"
        print(tail, flush=True)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip() or "no stderr"
        return False, f"agent exited {proc.returncode}: {err[:400]}"
    return True, ""


def dispatch_loop(
    artifact: Path,
    meta: Path,
    agent_cmd: str,
    interval: float,
    dry_run: bool,
    once: bool,
) -> None:
    processed = load_cursor(meta)
    log(meta, f"watcher up — agent={agent_cmd!r}{' [dry-run]' if dry_run else ''}, interval={interval}s")
    log(meta, f"previously processed: {len(processed)} batch(es)")
    while True:
        batches = read_batches(meta)
        pending = [b for b in batches if b["batch_id"] not in processed]
        for batch in pending:
            bid = batch["batch_id"]
            n = len(batch.get("comments") or [])
            log(meta, f"→ batch {bid} ({n} comment(s)) — dispatching")
            if dry_run:
                print("--- prompt ---")
                print(build_prompt(batch))
                print("--- end prompt ---")
                ok, err = True, ""
                dur = 0.0
            else:
                start = time.monotonic()
                ok, err = run_agent(build_prompt(batch), artifact, agent_cmd)
                dur = time.monotonic() - start
            # Mark processed either way — failures don't auto-retry. The user
            # can edit .ih/.watch-cursor.json to force a retry.
            processed.add(bid)
            save_cursor(meta, processed)
            if ok:
                log(meta, f"✓ batch {bid} done in {dur:.1f}s")
            else:
                log(meta, f"✗ batch {bid} failed in {dur:.1f}s — {err}")
        if once:
            return
        time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("artifact_dir", help="directory containing the HTML pages and .ih/")
    parser.add_argument(
        "--agent-cmd",
        default=DEFAULT_AGENT_CMD,
        help=f"shell command to invoke the agent; the prompt is sent on stdin (default: {DEFAULT_AGENT_CMD!r})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="seconds between comments.jsonl checks",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the prompt for each new batch instead of running the agent",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="process pending batches once and exit",
    )
    args = parser.parse_args()

    artifact = Path(args.artifact_dir).resolve()
    if not artifact.is_dir():
        print(f"[watch] error: {artifact} is not a directory", file=sys.stderr)
        return 1
    meta = artifact / META_DIR_NAME
    if not meta.is_dir():
        print(
            f"[watch] error: {meta} does not exist — run `python cli/inject.py {args.artifact_dir}` first",
            file=sys.stderr,
        )
        return 1

    print(f"[watch] artifact   {artifact}")
    print(f"[watch] watching   {meta / COMMENTS_FILE}")
    print(f"[watch] writes     {meta / UPDATES_FILE} (via the agent)")
    print(f"[watch] cursor     {meta / CURSOR_FILE}")

    try:
        dispatch_loop(
            artifact=artifact,
            meta=meta,
            agent_cmd=args.agent_cmd,
            interval=args.interval,
            dry_run=args.dry_run,
            once=args.once,
        )
    except KeyboardInterrupt:
        print("\n[watch] stopping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
