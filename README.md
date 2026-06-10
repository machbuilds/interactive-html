# Interactive HTML

Turn any folder of static HTML into a live commenting surface. Highlight text,
pick an element, or leave a general note — comments queue up locally and an
agent reads them, edits the HTML in response, and the page auto-reloads with
a short tour of what changed.

## Layout

```
interactive-html/
├── PROTOCOL.md       # the spec — what makes this agent-agnostic
├── LICENSE           # MIT
├── client/
│   ├── ih.js         # injected into every HTML page
│   └── ih.css
├── server/
│   └── server.py     # stdlib-only HTTP server (+ SSE)
├── cli/
│   ├── ih.py         # one-command launcher (inject + serve + watch)
│   ├── inject.py     # idempotent <link>/<script> injection + removal
│   ├── watch.py      # tails comments.jsonl and dispatches to an agent
│   └── install_skill.py   # assemble a self-contained Claude Code skill
├── agent/
│   └── agent.py      # built-in, dependency-free Anthropic tool-use agent
├── skill/
│   └── SKILL.md      # Claude Code skill — "make this page interactive"
├── adapters/
│   └── cursor/       # Cursor .mdc rule + install notes
├── examples/
│   └── sample.html   # smoke-test page
└── README.md
```

Everything is Python standard library — no pip install, no build step.
The built-in agent talks to the Anthropic API over `urllib`, so even that
needs nothing beyond an API key.

The file contract is the real product. Read [`PROTOCOL.md`](PROTOCOL.md)
for the spec — any agent (Claude, Cursor, Codex, a local LLM, your own
script) that implements it interoperates with this in-page client.

## Quickstart

### Inside Claude Code (lowest friction)

Install the skill once, then just ask in any session:

```bash
ln -s "$(pwd)/skill" ~/.claude/skills/interactive-html   # one-time
```

> "make this page interactive"

Claude finds the HTML in your current directory, starts the server, hands
you the URL, and then **acts as the agent itself** — when you comment in the
page, the live session edits the HTML and it reloads. No second agent
process, no cold start. (If you only have content and no file, say "make an
interactive page from this" and Claude writes the HTML first.)

### One command (manual)

```bash
python cli/ih.py examples       # inject + serve + watch + open browser
python cli/ih.py                # current directory
python cli/ih.py --no-watch     # serve + capture comments only (no agent)
```

This collapses the three steps below into one supervised process; Ctrl-C
stops everything. It auto-bumps off a busy port and prints every page URL.

### The three pieces, by hand

```bash
python cli/inject.py examples              # inject client tags
python server/server.py examples           # terminal 1: serve
python cli/watch.py examples               # terminal 2: dispatch to an agent
```

Open the printed URL (e.g. `http://localhost:5050/sample.html`). Comments
land in `examples/.ih/comments.jsonl`; the watcher hands each new batch to
the agent, which edits the HTML and appends to `examples/.ih/updates.json`.
The page reloads (scroll preserved) and walks through each
`data-ih-change` region.

### Choosing an agent

The watcher can drive two kinds of agent:

```bash
# default: pipe the prompt into Claude Code headless mode
python cli/watch.py examples                 # == --agent cli --agent-cmd "claude -p"

# any other CLI that takes a prompt on stdin and can edit files
python cli/watch.py examples --agent-cmd "your-cli --flags"

# the bundled, dependency-free agent (needs ANTHROPIC_API_KEY)
export ANTHROPIC_API_KEY=sk-ant-...
python cli/watch.py examples --agent builtin
```

The built-in agent (`agent/agent.py`) runs its own Anthropic tool-use loop
with file tools scoped to the artifact directory. As it works it writes
`.ih/progress.json`; the server streams that over SSE so the in-page busy
banner shows live status ("reading sample.html", "recording the changes",
…). Tune it with `--model` / `IH_AGENT_MODEL`, `--max-iterations`, and
`--max-tokens`; use `--dry-run` on either the watcher or the agent to
inspect without calling the API.

To remove the client layer cleanly:

```bash
python cli/inject.py examples --remove
```

## How it works

1. `cli/inject.py` adds `<link href="/client/ih.css">` and
   `<script src="/client/ih.js" defer>` to every page; creates `.ih/comments.jsonl`
   and `.ih/updates.json` so the server has somewhere to write.
2. `server/server.py` serves the artifact directory, routes `/client/*` to
   the sibling `client/` folder, and accepts:
   - `POST /comments` → appends a batch to `.ih/comments.jsonl`
   - `POST /_ih/seen` → records which update IDs the user has acknowledged
   - `GET /_ih/info` → diagnostic JSON
   - `GET /_ih/events` → Server-Sent Events stream. A background thread
     watches `.ih/updates.json` and `.ih/progress.json`; when either
     changes it broadcasts an `updates` or `progress` event.
3. `client/ih.js` subscribes to `/_ih/events`. On an `updates` event it
   refetches `.ih/updates.json`; when a new batch responds to one of the
   user's submitted comment IDs, the page reloads (scroll preserved via
   sessionStorage) and a walkthrough is offered. A `progress` event updates
   the busy banner. A 15s poll runs only as a fallback if the SSE channel
   drops.

The server auto-retires when its parent process dies or after the
configurable idle timeout (default 10 min, `--idle-timeout 0` disables).

### Protocol

**Comments batch** posted to `/comments` and appended to `.ih/comments.jsonl`:

```json
{
  "batch_id": "b-<timestamp>",
  "client_url": "/sample.html",
  "submitted_at": "2026-05-28T15:47:10Z",
  "comments": [
    {
      "id": "c-<timestamp>",
      "kind": "text" | "element" | "general",
      "anchor": {
        "selector": "...",
        "ih_id": "n3",
        "tag": "P",
        "quote": "first 220 chars",
        "html_snippet": "outerHTML truncated"
      },
      "body": "what the user wrote",
      "created_at": "ISO 8601"
    }
  ]
}
```

The server adds `received_at` (epoch seconds) and `received_iso` before
writing.

**Updates** are appended to `.ih/updates.json` (single JSON array, newest
last). Each entry looks like:

```json
{
  "batch_id": "u-<id>",
  "timestamp": "2026-05-28T15:48:00Z",
  "in_response_to_batch": "b-...",
  "changes": [
    {
      "id": "ch-<slug>",
      "anchor": "ch-<slug>",
      "in_response_to": ["c-..."],
      "title": "short label",
      "description": "longer prose"
    }
  ]
}
```

The agent wraps each modified region with
`<span data-ih-change="ch-<slug>">…</span>` (or adds the attribute to an
existing wrapping element). The client uses those anchors to walk through
the tour.

## Keyboard

- <kbd>E</kbd> — toggle element picker
- <kbd>R</kbd> — dismiss post-reload tour
- <kbd>Esc</kbd> — close any open popover
- <kbd>?</kbd> — show key hints
- <kbd>Cmd/Ctrl + Enter</kbd> in the editor — save the draft
- <kbd>←</kbd> / <kbd>→</kbd> in tour — step
