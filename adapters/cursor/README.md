# Interactive HTML — Cursor adapter

Lets you drive the Interactive HTML loop from Cursor.

## Install

1. Clone or install [Interactive HTML](https://github.com/machbuilds/interactive-html)
   somewhere on your machine. The path it ends up at is what we'll call
   `IH_HOME`.

2. Copy this directory's `interactive-html.mdc` into the project where you
   want to leave comments on HTML pages:

   ```bash
   mkdir -p .cursor/rules
   cp <IH_HOME>/adapters/cursor/interactive-html.mdc .cursor/rules/
   ```

3. Edit the copied rule: replace `<IH_HOME>` with the absolute path from
   step 1. (One find-and-replace; the placeholder appears three times.)

## Use

In any Cursor chat tied to that project, say:

> "make this page interactive"

Cursor starts the server in a terminal (it'll print the URL), and *you*
become the agent: when you submit a comment in the page, ask Cursor:

> "process new comments"

Cursor reads `.ih/comments.jsonl`, edits the HTML, appends `.ih/updates.json`,
and the page reloads with the changes highlighted.

## How this differs from the Claude Code skill

Claude Code can sit idle watching the inbox via its `Monitor` tool, so the
loop is fully automatic — comment, blink, change appears. Cursor doesn't
have that primitive yet, so the Cursor flow is one short turn per round:
**user comments → user asks Cursor to process → change lands**. Same
protocol, slightly more user friction.

If you want fully-automatic behaviour from Cursor: run the standalone
watcher in a terminal instead of using this rule.

```bash
python <IH_HOME>/cli/ih.py <dir>      # full loop, uses Cursor's claude/codex/agent CLI
```

## Protocol

See [`PROTOCOL.md`](../../PROTOCOL.md) in the repo root for the full file
contract — `.ih/` layout, JSON schemas, HTTP surface, conformance levels.
Implement that, and any Cursor (or non-Cursor) workflow you build
interoperates with the same in-page client.
