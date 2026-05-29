"""
Interactive HTML — built-in agent.

A dependency-free agent that resolves a batch of in-page comments by editing
the HTML and appending to .ih/updates.json. It talks to the Anthropic
Messages API directly over urllib (no SDK install required) and runs a
tool-use loop with a small set of file tools scoped to the working directory.

It is a drop-in agent command for the watcher: the watcher pipes the task
prompt on stdin and runs us with cwd set to the artifact directory, so the
file tools operate relative to the pages being commented on.

    # standalone
    cat prompt.txt | python agent/agent.py
    python agent/agent.py --batch-file batch.json
    python agent/agent.py --dry-run            # print the planned setup, no API call

    # via the watcher
    python cli/watch.py examples --agent builtin

Environment:
    ANTHROPIC_API_KEY   required to make real calls
    IH_AGENT_MODEL      overrides the default model
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_ITERATIONS = 24
DEFAULT_MAX_TOKENS = 8192

META_DIR_NAME = ".ih"
UPDATES_FILE = "updates.json"
PROGRESS_FILE = "progress.json"

# Files the agent is allowed to create/overwrite. Anything else is rejected by
# the write tools so a misbehaving model can't wander outside the artifact.
WRITABLE_SUFFIXES = (".html", ".htm")
WRITABLE_META = {f"{META_DIR_NAME}/{UPDATES_FILE}"}

SYSTEM_PROMPT = """\
You are the Interactive HTML agent. You resolve reader comments on a set of
static HTML pages by editing those pages directly, then recording what you
changed.

You have file tools scoped to the current working directory. Use them to:
  1. Find the elements referenced by each comment (match on the visible text
     or html_snippet — never rely on data-* attributes that may be assigned
     at runtime).
  2. Make the minimal edits that address each comment.
  3. Wrap each logical change in <span data-ih-change="ch-<slug>">…</span>, or
     add a data-ih-change attribute to an existing wrapping element. Exactly
     one anchor per change.
  4. Append a single batch object to .ih/updates.json (it is a JSON array —
     read it, append, write the whole array back).

Work autonomously: do not ask questions, do not start servers, edit only HTML
files and .ih/updates.json. When everything is done and updates.json is
written, stop."""

TOOLS = [
    {
        "name": "list_pages",
        "description": "List the HTML files in the working directory (recursively, excluding the .ih meta directory). Use this first to discover which pages exist.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file relative to the working directory. Use it to read HTML pages and .ih/updates.json.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "path relative to the working directory"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace an exact substring in a file. old_string must occur exactly once unless replace_all is true. Prefer this over write_file for targeted HTML edits.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean", "default": False},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "write_file",
        "description": "Write (create or overwrite) a UTF-8 text file. Allowed for *.html pages and .ih/updates.json only. Use read_file then write_file to append to the updates.json array.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
]


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------
def write_progress(meta_dir: Path, batch_id: str, status: str, phase: str = "working") -> None:
    """Atomically write a small status file. The server watches this and pushes
    a 'progress' SSE event so the browser's busy banner can show live status."""
    payload = {
        "batch_id": batch_id,
        "status": status,
        "phase": phase,
        "ts": time.time(),
    }
    try:
        meta_dir.mkdir(exist_ok=True)
        tmp = meta_dir / (PROGRESS_FILE + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(meta_dir / PROGRESS_FILE)
    except OSError:
        pass


def emit(msg: str) -> None:
    print(f"[agent] {msg}", flush=True)


# ---------------------------------------------------------------------------
# file tools (scoped to root)
# ---------------------------------------------------------------------------
class ToolError(Exception):
    pass


def _resolve_within(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and not str(target).startswith(str(root_resolved) + os.sep):
        raise ToolError(f"path escapes the working directory: {rel}")
    return target


def _is_writable(root: Path, target: Path) -> bool:
    rel = target.relative_to(root.resolve()).as_posix()
    if target.suffix.lower() in WRITABLE_SUFFIXES:
        return True
    return rel in WRITABLE_META


def tool_list_pages(root: Path, _inp: dict) -> str:
    pages = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*.html")
        if META_DIR_NAME not in p.parts
    )
    return json.dumps(pages) if pages else "[] (no html files found)"


def tool_read_file(root: Path, inp: dict) -> str:
    target = _resolve_within(root, inp["path"])
    if not target.is_file():
        raise ToolError(f"not a file: {inp['path']}")
    return target.read_text(encoding="utf-8")


def tool_edit_file(root: Path, inp: dict) -> str:
    target = _resolve_within(root, inp["path"])
    if not target.is_file():
        raise ToolError(f"not a file: {inp['path']}")
    if not _is_writable(root, target):
        raise ToolError(f"not allowed to edit: {inp['path']}")
    text = target.read_text(encoding="utf-8")
    old = inp["old_string"]
    new = inp["new_string"]
    count = text.count(old)
    if count == 0:
        raise ToolError("old_string not found")
    if count > 1 and not inp.get("replace_all"):
        raise ToolError(f"old_string occurs {count} times; pass replace_all or make it unique")
    text = text.replace(old, new) if inp.get("replace_all") else text.replace(old, new, 1)
    target.write_text(text, encoding="utf-8")
    return f"edited {inp['path']} ({'all' if inp.get('replace_all') else 1} occurrence(s))"


def tool_write_file(root: Path, inp: dict) -> str:
    target = _resolve_within(root, inp["path"])
    if not _is_writable(root, target):
        raise ToolError(f"not allowed to write: {inp['path']} (only *.html and .ih/updates.json)")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write so the server's file-watcher never reads a half-written file.
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(inp["content"], encoding="utf-8")
    tmp.replace(target)
    return f"wrote {inp['path']} ({len(inp['content'])} bytes)"


TOOL_FUNCS = {
    "list_pages": tool_list_pages,
    "read_file": tool_read_file,
    "edit_file": tool_edit_file,
    "write_file": tool_write_file,
}


def describe_tool_call(name: str, inp: dict) -> str:
    if name == "list_pages":
        return "scanning the pages"
    if name == "read_file":
        return f"reading {inp.get('path', '?')}"
    if name == "edit_file":
        return f"editing {inp.get('path', '?')}"
    if name == "write_file":
        path = inp.get("path", "?")
        if path.endswith(UPDATES_FILE):
            return "recording the changes"
        return f"writing {path}"
    return name


# ---------------------------------------------------------------------------
# Anthropic API (raw HTTP)
# ---------------------------------------------------------------------------
def call_messages(api_key: str, model: str, system_blocks: list, tools: list, messages: list,
                  max_tokens: int) -> dict:
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "tools": tools,
        "messages": messages,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", API_VERSION)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise ToolError(f"API HTTP {e.code}: {detail[:500]}")
    except urllib.error.URLError as e:
        raise ToolError(f"network error reaching the API: {e.reason}")


def build_system_blocks() -> list:
    # cache_control on the (static) system prompt — repeated batches reuse it.
    return [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


def build_cached_tools() -> list:
    tools = [dict(t) for t in TOOLS]
    # cache breakpoint on the last tool definition caches the whole tools array.
    tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


def run_agent(task_prompt: str, root: Path, meta_dir: Path, batch_id: str,
              model: str, max_iterations: int, max_tokens: int, api_key: str) -> int:
    messages = [{"role": "user", "content": task_prompt}]
    system_blocks = build_system_blocks()
    tools = build_cached_tools()

    write_progress(meta_dir, batch_id, "thinking", phase="start")

    for iteration in range(1, max_iterations + 1):
        response = call_messages(api_key, model, system_blocks, tools, messages, max_tokens)
        stop_reason = response.get("stop_reason")
        content = response.get("content", [])

        # Surface any narration text as progress + log.
        for block in content:
            if block.get("type") == "text" and block.get("text", "").strip():
                line = block["text"].strip().splitlines()[0]
                emit(line)
                write_progress(meta_dir, batch_id, line[:160])

        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if not tool_uses:
            emit(f"finished (stop_reason={stop_reason})")
            write_progress(meta_dir, batch_id, "done", phase="done")
            return 0

        # Echo the assistant turn back, then run each requested tool.
        messages.append({"role": "assistant", "content": content})
        tool_results = []
        for tu in tool_uses:
            name = tu.get("name", "")
            inp = tu.get("input", {}) or {}
            status = describe_tool_call(name, inp)
            emit(status)
            write_progress(meta_dir, batch_id, status)
            func = TOOL_FUNCS.get(name)
            if func is None:
                result_text, is_error = f"unknown tool: {name}", True
            else:
                try:
                    result_text, is_error = func(root, inp), False
                except (ToolError, KeyError, OSError) as e:
                    result_text, is_error = str(e), True
                    emit(f"  ! {result_text}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.get("id"),
                "content": result_text,
                **({"is_error": True} if is_error else {}),
            })
        messages.append({"role": "user", "content": tool_results})

    emit(f"hit max iterations ({max_iterations}) without finishing")
    write_progress(meta_dir, batch_id, "stopped: max iterations", phase="error")
    return 2


def extract_batch_id(task_prompt: str) -> str:
    """Best-effort: pull the batch_id out of the embedded JSON so progress
    events can be attributed. Falls back to a generic label."""
    marker = '"batch_id"'
    idx = task_prompt.find(marker)
    if idx != -1:
        rest = task_prompt[idx + len(marker):]
        colon = rest.find(":")
        if colon != -1:
            seg = rest[colon + 1:].strip()
            if seg and seg[0] in "\"'":
                quote = seg[0]
                end = seg.find(quote, 1)
                if end != -1:
                    return seg[1:end]
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-file", help="read the task prompt from this file instead of stdin")
    parser.add_argument("--root", default=".", help="working directory containing the HTML pages (default: cwd)")
    parser.add_argument("--model", default=os.environ.get("IH_AGENT_MODEL", DEFAULT_MODEL))
    parser.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERATIONS)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--dry-run", action="store_true", help="print the resolved setup and exit without calling the API")
    args = parser.parse_args()

    if args.batch_file:
        task_prompt = Path(args.batch_file).read_text(encoding="utf-8")
    else:
        task_prompt = sys.stdin.read()
    if not task_prompt.strip():
        emit("no task prompt provided (stdin was empty)")
        return 1

    root = Path(args.root).resolve()
    if not root.is_dir():
        emit(f"root is not a directory: {root}")
        return 1
    meta_dir = root / META_DIR_NAME
    batch_id = extract_batch_id(task_prompt)

    if args.dry_run:
        emit(f"model        {args.model}")
        emit(f"root         {root}")
        emit(f"batch_id     {batch_id}")
        emit(f"tools        {', '.join(TOOL_FUNCS)}")
        emit(f"pages        {tool_list_pages(root, {})}")
        emit("dry-run: not calling the API")
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        emit("ANTHROPIC_API_KEY is not set — export it to run the built-in agent,")
        emit("or use the watcher's default `claude -p` agent instead.")
        return 1

    emit(f"resolving batch {batch_id} with {args.model}")
    try:
        return run_agent(
            task_prompt=task_prompt,
            root=root,
            meta_dir=meta_dir,
            batch_id=batch_id,
            model=args.model,
            max_iterations=args.max_iterations,
            max_tokens=args.max_tokens,
            api_key=api_key,
        )
    except ToolError as e:
        emit(f"failed: {e}")
        write_progress(meta_dir, batch_id, f"error: {e}"[:160], phase="error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
