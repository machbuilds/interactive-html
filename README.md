# Interactive HTML

> **Inline comments on any HTML page — an agent applies them.**
>
> Highlight text, pick an element, or leave a general note in any local
> HTML file. The page POSTs your comment to a tiny local server. An agent
> reads it, edits the HTML, and the page reloads with the change
> highlighted — scroll preserved.

<!-- ![demo](docs/demo.gif) -->
<!-- Demo GIF goes here. See "Recording the demo" below. -->

Works with Claude Code, Cursor, or any agent CLI. ~43KB client, zero
dependencies, no build step. Static HTML stays static — the comment layer
is a `<link>` + `<script>` you can strip back out in one command.

---

## Quickstart

```bash
git clone https://github.com/machbuilds/interactive-html
cd interactive-html
python cli/ih.py examples
```

That's it — server, watcher, and agent all start in one supervised
process. Your browser opens to `http://localhost:5050/sample.html`.
Highlight text in the page, write a comment, hit Submit. Within seconds
the page reloads with the change applied and a tour walking you through it.

`Ctrl-C` stops everything. By default the watcher drives `claude -p`
(Claude Code in headless mode — uses your existing auth, no API key
needed). [Other agents](#agents) below.

---

## Three ways to drive it

### Claude Code skill — lowest friction

Install once, then in any Claude session, in any folder with HTML:

```bash
python cli/install_skill.py
```

This installs two skills:

> *"make this page interactive"* — **interactive-html**: the session
> **itself** becomes the agent. No separate process, no cold start. When
> you comment, your live Claude session edits the HTML directly.
>
> *"build me a page about …"* — **html-designer**: generates a polished,
> self-contained HTML page from a description or pasted content (semantic
> markup, dark mode, responsive, zero dependencies), then offers to make
> it interactive so you iterate by commenting instead of describing.

### Cursor — agent-agnostic adapter

```bash
cp adapters/cursor/interactive-html.mdc .cursor/rules/
```

In any Cursor chat: *"make this page interactive"*, then *"process new
comments"* after you submit. See [adapters/cursor/](adapters/cursor/) for
the workflow note (Cursor lacks Claude Code's idle-monitor primitive).

### Manual — three terminals, full control

```bash
python cli/inject.py examples         # add the <link>/<script> tags
python server/server.py examples      # terminal 1: serve
python cli/watch.py examples          # terminal 2: dispatch to an agent
```

Same loop as `cli/ih.py`, just unbundled.

---

## What you get

- **Three comment modes**: text selection (highlight prose), element pick
  (click any region; shift-click to add more), or a general note that
  isn't anchored anywhere
- **Live agent status** in the page busy banner: "editing page.html…",
  "recording the changes…" — fed by SSE from `.ih/progress.json`
- **Tour on reload**: every change gets a title from the agent and a
  highlighted region; arrow keys walk through them
- **Scroll preserved**: leave a comment near the bottom, get the change,
  stay there
- **SSE-driven, not polling**: page reacts within ~1s of the agent's last
  write. A 15s slow-poll runs only as a fallback if SSE drops
- **Restart-safe**: the watcher tracks processed batch IDs in a cursor
  file so killing and restarting doesn't replay old comments
- **Self-contained**: Python standard library, vanilla JS, no `pip
  install`, no `npm install`, no build step

---

## How it works

One loop, a few seconds per round trip:

```mermaid
sequenceDiagram
    autonumber
    actor You
    participant Page as 🌐 Page
    participant Server as ⚡ Server
    participant Agent as 🤖 Agent

    You->>Page: highlight text, write a comment
    Page->>Server: POST /comments
    Server->>Agent: new entry in .ih/comments.jsonl
    Agent->>Agent: edits the HTML
    Agent->>Server: appends .ih/updates.json
    Server-->>Page: SSE: "updates"
    Page->>You: reloads + tour of what changed
```

| | Who | What happens |
|---|---|---|
| 💬 | **You** | Highlight prose, pick an element, or leave a note — then Submit |
| 📥 | **Page → Server** | Comment lands in `.ih/comments.jsonl` |
| ✏️ | **Agent** | Reads it, edits the HTML, records changes in `.ih/updates.json` |
| 🔔 | **Server → Page** | SSE event the instant the file changes |
| ✨ | **Page → You** | Auto-reload (scroll preserved) + guided tour of every change |

The file contract (`.ih/comments.jsonl` + `.ih/updates.json` +
`data-ih-change` anchors in the HTML) is the **real product** — see
[PROTOCOL.md](PROTOCOL.md). Any agent that implements it interoperates
with this in-page client. The bundled server, JS client, watcher, and
agents are reference implementations.

---

## Agents

By default the watcher pipes the prompt to `claude -p
--permission-mode acceptEdits`. To switch:

```bash
# bundled dependency-free Anthropic agent (needs ANTHROPIC_API_KEY)
python cli/ih.py --agent builtin

# any other CLI that reads a prompt on stdin and can edit files
python cli/ih.py --agent-cmd "your-cli --flags"

# no agent — capture comments only (writes to .ih/comments.jsonl for later)
python cli/ih.py --no-watch
```

The bundled agent (`agent/agent.py`) talks to the Messages API over
`urllib` with a four-tool file loop (list, read, edit, write). It writes
`.ih/progress.json` as it goes, so the in-page busy banner shows live
status — something `claude -p` doesn't provide.

---

## Layout

```
interactive-html/
├── PROTOCOL.md       # the file/HTTP contract — implement this and you're done
├── LICENSE           # MIT
├── client/
│   ├── ih.js         # injected into every page
│   └── ih.css
├── server/
│   └── server.py     # stdlib HTTP + SSE
├── cli/
│   ├── ih.py         # one-command launcher
│   ├── inject.py     # idempotent tag injection / removal
│   ├── watch.py      # dispatches new comment batches to an agent
│   └── install_skill.py   # assemble a self-contained Claude Code skill
├── agent/
│   └── agent.py      # bundled Anthropic agent (urllib, no SDK)
├── skills/
│   ├── interactive-html/   # Claude Code skill — "make this page interactive"
│   └── html-designer/      # Claude Code skill — "build me a page about …"
├── adapters/
│   └── cursor/       # Cursor .mdc rule + install notes
└── examples/
    └── sample.html
```

---

## Recording the demo

The magic of this project happens in the browser (highlight → page reload
+ tour), so the canonical demo GIF needs a screen recorder, not a
terminal recorder. Two recommended paths on macOS:

```bash
# A. macOS native screen capture (⇧⌘5, select the browser window),
#    convert to GIF with ffmpeg
ffmpeg -i recording.mov -vf "fps=15,scale=900:-1:flags=lanczos" docs/demo.gif

# B. Kap — free, OSS, native macOS, records straight to GIF
brew install --cask kap
```

Drop the result at `docs/demo.gif` and uncomment the `![demo]` line near
the top of this file.

---

## Remove the comment layer

When you want a clean static copy back:

```bash
python cli/inject.py examples --remove
```

The `.ih/` directory is left in place; `rm -rf .ih/` if you don't need
the comment history.

---

## Status

V1, single-author. Used in real iteration loops; not battle-tested at
team-scale.

What's stable:
- File protocol (`PROTOCOL.md`) — v1, additive changes only
- HTTP surface — same
- The bundled server, client, watcher, and Claude Code skill

What's on the roadmap (and where contributions land cleanly):
- Comment threads (replies, resolved/unresolved)
- Multi-user presence over the existing SSE channel
- More agent adapters (Codex, Gemini CLI, local models)
- Single-shot built-in agent (cut the agent loop to one API call for
  small edits)

Issues and PRs welcome. The protocol is the contract — anything that
respects it is fair game.

---

## License

[MIT](LICENSE). Use it however you want.
