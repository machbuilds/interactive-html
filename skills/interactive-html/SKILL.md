---
name: interactive-html
description: Turn a folder of static HTML into a live commenting surface and act as the agent that responds. Injects a client library, starts a local server, and watches an on-disk inbox; when the user highlights text / clicks an element / leaves a note in the page, you read it and edit the HTML in response. Trigger phrases — "make this page interactive", "make these pages interactive", "let me comment on this page", "make this interactive", "add feedback to this page", "comment on this page".
---

# Interactive HTML

Turn any folder of HTML into a place the user can leave inline comments
(text selections, element selections, page-level notes), then **you are the
agent** that responds: comments land in an on-disk inbox, you read them, edit
the HTML, and the page auto-reloads with a walkthrough of what changed.

Because *you* (the current session) do the edits, there is no separate agent
process and no cold-start — the loop is as fast as you can make an edit.

## Runtime location

This skill is self-contained: its runtime ships in this same directory. The
installer bakes the absolute path below in at install time, so every command
here is runnable as written.

```
IH_HOME = __IH_HOME__
```

## When to invoke

- "make this page interactive" / "make these pages interactive" → **Setup flow**
- "let me comment on this page" / "add feedback to this page" → **Setup flow**
- "I have this content, make it an interactive page" → **Create-then-setup flow**
- "stop the interactive server" / "shut it down" → **Stop flow**
- "make these pages static again" / "remove the comment layer" → **Removal flow**

## Setup flow

1. **Pick the target directory.** Default to the current working directory
   (the project the user is in). If they name a folder, use that. If it has no
   `*.html`, see the Create-then-setup flow.

2. **Launch** — inject the client tags and start the server in one step, in the
   background (do NOT block the session):

   ```
   python __IH_HOME__/cli/ih.py <dir> --no-watch --no-open
   ```

   `--no-watch` is important: it starts the server only, because **you** are the
   agent — we do not want a second `claude -p` agent competing with you. The
   launcher prints the page URLs (it auto-bumps off a busy port).

3. **Tell the user the URL(s)** it printed, e.g. `http://localhost:5050/index.html`.

4. **Monitor the inbox** so new comments notify you immediately:

   ```
   Monitor on path: <dir>/.ih/comments.jsonl
   ```

   Do NOT poll — let the Monitor notification arrive.

## Responding to a comment batch

When a new line appears in `<dir>/.ih/comments.jsonl`:

- Read the new batch. Each comment has a stable `id`, a `kind`
  (`text` / `element` / `region` / `general`), an `intent`
  (`change` / `question`), and an `anchor` with a CSS `selector`, `tag`,
  `quote`, and `html_snippet`. **Locate the element by its visible text
  or `html_snippet`** — match on content, not on runtime-only attributes.
  `kind: "region"` comments add `anchor.multi` (selectors for every element
  the user circled) and `anchor.region` (the drawn rectangle, page
  coordinates) — treat the set as the target area.

- **`intent: "question"` comments are questions, not edit requests.** Do
  NOT modify the page for them. Answer in the update's `answers` array
  (schema below) — concretely, citing the relevant part of the page. The
  client shows your answer in its Q&A tab, anchored to where they asked.

- For `intent: "change"` comments, edit the relevant `*.html` file(s).
  Keep edits minimal and focused on what was asked.

- Wrap each logical change in `<span data-ih-change="ch-<slug>">…</span>`, or
  add a `data-ih-change="ch-<slug>"` attribute to an existing wrapping element.
  Exactly one anchor per change — the page uses these for the walkthrough.

- **Append** one batch object to `<dir>/.ih/updates.json`. It is a JSON array
  (newest last) — read it, append, write the whole array back:

  ```json
  {
    "batch_id": "u-<short-slug>",
    "timestamp": "<ISO 8601 UTC, e.g. 2026-06-07T12:00:00Z>",
    "in_response_to_batch": "<batch_id from the inbox line>",
    "changes": [
      {
        "id": "ch-<slug>",
        "anchor": "ch-<slug>",
        "in_response_to": ["<comment id you addressed>"],
        "title": "short, concrete label",
        "description": "one or two sentences for the record"
      }
    ],
    "answers": [
      {
        "id": "a-<slug>",
        "in_response_to": ["<comment id of the question>"],
        "text": "your answer, plain prose"
      }
    ]
  }
  ```

  Omit `changes` or `answers` when empty. An answers-only update doesn't
  reload the page — the client surfaces it in the Q&A tab in place.

The server detects the write and pushes an SSE event; the page reloads with
scroll preserved and offers a tour (or, for answers-only updates, shows the
Q&A tab without reloading). The page's busy banner clears when a change's or
answer's `in_response_to` matches a submitted comment id.

Rules:
- Edit only HTML files and `.ih/updates.json`.
- Every `id` under `changes` must have a matching `data-ih-change` in the HTML,
  and every new `data-ih-change` must appear in updates.json. Answers need no
  HTML anchor.
- Questions never modify the page unless the question explicitly asks for an
  edit too.

## Create-then-setup flow

If the user has content but no HTML file (or no folder), create one first:

1. Choose/confirm a directory (default: current directory).
2. Write a clean, self-contained `*.html` file with their content.
3. Run the **Setup flow** on that directory.

## On startup in a directory that already has `.ih/`

1. Read `.ih/comments.jsonl` for comment ids.
2. Read `.ih/updates.json` and union every `changes[*].in_response_to` —
   those are already handled.
3. If unhandled comments remain, tell the user the count and offer to process.
4. Either way, start the server (Setup step 2) and the Monitor (step 4).

## Stop flow

1. Find the port (the launcher printed it; default 5050). Confirm with
   `curl -s http://localhost:<port>/_ih/info`.
2. `lsof -ti:<port> | xargs kill`
3. Confirm `lsof -i:<port>` is silent.

The server also self-retires on parent-death or idle timeout, so if you simply
end the session it cleans itself up.

## Removal flow

Strip the client tags for a clean, server-independent copy:

```
python __IH_HOME__/cli/inject.py <dir> --remove
```

This leaves the `.ih/` directory in place; delete it manually if unwanted.

## Headless alternative (no live session)

When no Claude session is driving the page (e.g. an automated/background run),
use the standalone watcher instead of this skill — it dispatches batches to an
agent CLI:

```
python __IH_HOME__/cli/ih.py <dir>                  # full loop, claude -p agent
python __IH_HOME__/cli/ih.py <dir> --agent builtin  # bundled agent (ANTHROPIC_API_KEY)
```

## Gotchas

- The injected tags reference `/client/ih.css` and `/client/ih.js`, served by
  the server from the repo's `client/` folder. Pages only work through the
  server, not opened as `file://`.
- `.ih/updates.json` is append-only and order matters — append, don't prepend.
- `anchor` values in updates.json must match a real `data-ih-change` in the
  HTML, or the walkthrough warns "anchor not found".
