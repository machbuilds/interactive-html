# Interactive HTML

Turn any folder of static HTML into a live commenting surface. Highlight text,
pick an element, or leave a general note — comments queue up locally and an
agent reads them, edits the HTML in response, and the page auto-reloads with
a short tour of what changed.

## Layout

```
interactive-html/
├── client/
│   ├── ih.js         # injected into every HTML page
│   └── ih.css
├── server/
│   └── server.py     # stdlib-only HTTP server
├── cli/
│   ├── inject.py     # idempotent <link>/<script> injection + removal
│   └── watch.py      # tails comments.jsonl and dispatches to an agent CLI
├── examples/
│   └── sample.html   # smoke-test page
├── README.md
└── .gitignore
```

## Quickstart

Three commands across two terminals:

```bash
# one-time: inject the client tags into every *.html
python cli/inject.py examples

# terminal 1: serve the artifact directory
python server/server.py examples

# terminal 2: watch for comments and dispatch them to an agent
python cli/watch.py examples
```

Open the printed URL (e.g. `http://localhost:5050/sample.html`). Comments
land in `examples/.ih/comments.jsonl`; the watcher hands each new batch to
the agent, which edits the HTML and appends to `examples/.ih/updates.json`.
The page reloads (scroll preserved) and walks through each
`data-ih-change` region.

The watcher defaults to `claude -p` (Claude Code in headless mode). Override
with `--agent-cmd` to pipe the prompt into a different CLI. Use `--dry-run`
to see the prompt without burning tokens.

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
3. `client/ih.js` polls `.ih/updates.json` every ~4s. When a new batch
   responds to one of the user's submitted comment IDs, the page reloads
   (scroll preserved via sessionStorage) and a walkthrough is offered.

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
