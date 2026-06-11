# Interactive HTML Protocol

An open, file-based protocol for in-page agent feedback on static HTML.

> The interesting unit isn't the script — it's the contract. Any agent
> (Claude, Cursor, Codex, a local LLM, or a hand-written script) that
> implements this protocol interoperates with the same in-page client and
> the same server.

## Overview

A user views an HTML page in a browser. They highlight text, pick an
element, or leave a general note. The comment is appended to a local file.
An agent that follows this protocol reads the file, edits the HTML, and
writes a response file. The page detects the response and reloads with the
changes highlighted, then optionally walks the user through what changed.

The whole protocol is three JSON file formats and four-to-six HTTP
endpoints. There is nothing else to learn.

## The `.ih/` directory

Every artifact directory (the folder of HTML pages being commented on) has
a `.ih/` subdirectory at its root:

```
my-artifact/
├── page-one.html
├── page-two.html
└── .ih/
    ├── comments.jsonl   # inbox (append-only, one batch per line)
    ├── updates.json     # agent responses (append-only JSON array)
    ├── progress.json    # optional: live agent status (overwritten)
    └── seen.json        # optional: most recent update id the user has seen
```

The agent and the in-page client communicate exclusively through these
files (and, for live notification, through the server's SSE channel).

## HTTP surface

A conforming server MUST expose:

| Method | Path | Body | Effect |
|---|---|---|---|
| `GET` | `/<any>.html` | — | Serve files from the artifact directory |
| `GET` | `/client/ih.js` | — | Serve the in-page library JS |
| `GET` | `/client/ih.css` | — | Serve the in-page library CSS |
| `POST` | `/comments` | a Comments Batch | Append the batch to `.ih/comments.jsonl` |
| `GET`  | `/_ih/info` | — | Diagnostic JSON (artifact path, port, etc.) |

A conforming server MAY expose:

| Method | Path | Effect |
|---|---|---|
| `GET`  | `/_ih/events` | Server-Sent Events stream. Emits `updates` events when `.ih/updates.json` changes, `progress` events when `.ih/progress.json` changes |
| `POST` | `/_ih/seen` | Persist a JSON body to `.ih/seen.json` (records acknowledged update ids) |

## Schemas

### Comments Batch (client → `/comments` → `.ih/comments.jsonl`)

```json
{
  "batch_id": "b-mq0mckjv-f0o0e",       // unique, client-generated
  "client_url": "/page-one.html",        // path of the page that was open
  "submitted_at": "2026-06-07T13:14:49Z",
  "comments": [
    {
      "id": "c-mq0mch83-c0ypn",          // unique, client-generated
      "kind": "text",                    // "text" | "element" | "region" | "general"
      "intent": "change",                // "change" | "question" (absent = "change")
      "anchor": {                        // null for kind="general"
        "selector": "body > h2:nth-of-type(1)",       // CSS selector
        "tag": "H2",
        "quote": "Three ways to leave a comment",     // visible text, ≤220 chars
        "html_snippet": "<h2>Three ways to leave a comment</h2>",  // ≤600 chars
        "multi": ["body > p:nth-of-type(2)", "…"],    // kind="region" only:
                                                       // every circled element
        "region": { "x": 0, "y": 0, "width": 0, "height": 0 }  // kind="region"
                                                       // only: drawn rectangle,
                                                       // page coordinates
      },
      "body": "change this to 2 ways",
      "created_at": "2026-06-07T13:14:30Z"
    }
  ],
  "received_at": 1779993400.5,           // server-added on write
  "received_iso": "2026-06-07T13:14:50"  // server-added on write
}
```

`comments.jsonl` is **append-only**. Agents MUST NOT modify existing lines.

### Update (agent → `.ih/updates.json`)

`.ih/updates.json` is a JSON array; it begins as `[]`. For each batch the
agent processes, it appends one object. The array MUST remain valid JSON
after each append.

```json
[
  {
    "batch_id": "u-three-to-two",
    "timestamp": "2026-06-07T13:15:09Z",
    "in_response_to_batch": "b-mq0mckjv-f0o0e",
    "changes": [
      {
        "id": "ch-three-to-two",
        "anchor": "ch-three-to-two",
        "in_response_to": ["c-mq0mch83-c0ypn"],
        "title": "Renamed heading",
        "description": "Changed 'Three ways' to '2 ways' as requested."
      }
    ],
    "answers": [
      {
        "id": "a-why-three",
        "in_response_to": ["c-somequestion"],
        "text": "Plain-prose reply to a comment with intent=question."
      }
    ]
  }
]
```

`changes` and `answers` are both optional — omit whichever is empty. An
update containing only `answers` means the HTML was not modified: the
client surfaces the answers in place (Q&A tab + anchored highlight)
without reloading the page.

**Questions.** A comment with `intent: "question"` is a request for an
explanation, not an edit. Agents MUST reply via `answers` and MUST NOT
modify the page for it (unless the question itself also asks for an
edit). This turns reading friction — "I don't understand this part" —
into an in-page Q&A loop with zero copy-paste.

For every `change`, the corresponding HTML edit MUST embed a matching
`data-ih-change` attribute somewhere in the page:

```html
<h2 data-ih-change="ch-three-to-two">2 ways to leave a comment</h2>
```

…either by wrapping the changed region in `<span data-ih-change="…">…</span>`
or by adding the attribute to an existing wrapping element. The in-page
client uses these anchors to highlight changes and drive the walkthrough.

### Progress (optional, agent → `.ih/progress.json`)

Agents MAY surface live status by overwriting `.ih/progress.json` with a
single JSON object as they work:

```json
{
  "batch_id": "b-mq0mckjv-f0o0e",
  "status": "editing page-one.html",
  "phase": "working",            // "start" | "working" | "done" | "error"
  "ts": 1779993405.123
}
```

Writes should be **atomic** (write to a temp file, then rename) so the
server's file-watcher never reads a half-written file. The server SHOULD
push a `progress` SSE event on each change.

### Seen (optional, client → `.ih/seen.json`)

The client MAY POST acknowledged update ids to `/_ih/seen`; the server
writes the body verbatim to `.ih/seen.json`. Useful for agents that want to
surface "you have N unread changes" in a later session.

## Lifecycle

```
 ┌───────────────────────────────────────────────────────────────────────┐
 │                                                                       │
 │   user highlights text / clicks element / writes a general note       │
 │                                                                       │
 │                              │ POST /comments                         │
 │                              ▼                                        │
 │                  server appends to .ih/comments.jsonl                 │
 │                              │                                        │
 │                              ▼ (file-watch or external trigger)       │
 │   agent reads new batch ──▶ edits HTML (wrapping data-ih-change) ──▶  │
 │                              │                                        │
 │                              ▼                                        │
 │                  agent appends to .ih/updates.json                    │
 │                              │ (file-watch)                           │
 │                              ▼                                        │
 │   server broadcasts 'updates' SSE event                               │
 │                              │                                        │
 │                              ▼                                        │
 │   client sees updates.json change, page reloads, scroll preserved,    │
 │   walkthrough offered (driven by data-ih-change anchors)              │
 │                                                                       │
 └───────────────────────────────────────────────────────────────────────┘
```

## Conformance levels

**Level 1 — minimum viable agent.** Reads `.ih/comments.jsonl`, edits the
relevant HTML files, appends `.ih/updates.json` with matching
`data-ih-change` anchors. The page will pick up the change via its
15-second poll fallback even with no SSE.

**Level 2 — live agent.** Adds progress writes to `.ih/progress.json` so
the user sees "editing page-one.html" instead of a static spinner.

**Level 3 — SSE-aware integration.** Agent runs in-process with the server
and publishes events directly. Optional — the server's file-watcher will
broadcast file changes regardless.

## Anchors and matching

When locating elements referenced in a Comments Batch, agents SHOULD match
on `anchor.quote` (visible text) and `anchor.html_snippet`. Avoid relying
solely on `anchor.selector`: the selector is reliable across a session but
may reference structural positions that an edit shifts.

When emitting `data-ih-change`:

- Each `change.id` in `updates.json` MUST appear exactly once as a
  `data-ih-change` attribute in some HTML file.
- Each new `data-ih-change` MUST appear in `updates.json`. A change without
  an update entry won't surface in the walkthrough; an update entry without
  a matching attribute logs "anchor not found" in the page.
- The slug after `ch-` is free-form (`[a-z0-9-]`). Keep it short.

## Why a protocol, not a library

Because the interesting part is the *file contract*, not the runtime. Any
agent CLI, IDE extension, local model, or one-off script can implement this
and interoperate with the same in-page client.

The Interactive HTML repository ships:

- A reference **server** in Python standard library.
- A reference **in-page client** in vanilla JS + CSS.
- Two reference **agents**:
  - A subprocess driver around `claude -p` (Claude Code's headless mode).
  - A dependency-free Anthropic API agent that talks to the Messages API
    over `urllib`.
- A reference **Claude Code skill** that drives the loop from a live
  session — turning the user's current Claude conversation into the agent.
- A reference **Cursor adapter** under `adapters/cursor/`.

These are reference implementations of the protocol, not the protocol
itself. Implement the file contract in any language and the rest still
works.

## Versioning

This is **Interactive HTML Protocol v1**. Backwards-incompatible changes
will bump the major version and add a `protocol_version` field to the
relevant payloads. Until then, treat unknown fields as additive and ignore
them — that's the conformance rule for forward compatibility.
