# Privacy

Interactive HTML is a local-first developer tool. We do not collect,
store, or transmit any user data.

## What stays on your machine

- Your HTML files
- Your comments (written to `<artifact>/.ih/comments.jsonl` on local disk)
- Agent responses (written to `<artifact>/.ih/updates.json` on local disk)
- Server logs (printed to your terminal)

The server binds to `127.0.0.1` (localhost) by default, so the comment
endpoint is not reachable from outside your machine. The bind host can be
overridden with `--host`; if you do that, you are responsible for whatever
network exposure follows.

## What gets sent over the network

Only what your chosen agent sends to its own provider, per that
provider's terms:

- **Claude Code skill mode** — uses your existing Claude Code session.
  Data flows per Anthropic's standard terms.
- **`claude -p` watcher mode** — same path, via the Claude Code CLI.
- **Built-in agent** (optional) — sends prompts directly to the Anthropic
  Messages API using your `ANTHROPIC_API_KEY`. Standard Anthropic terms
  apply.
- **Custom `--agent-cmd`** — whatever that CLI sends. Not under our
  control.

Interactive HTML itself ships no telemetry, no analytics, no error
reporting, and no update-check pings. The repo has no analytics scripts,
no tracking pixels, no third-party JavaScript.

## Third parties

None at runtime. The project has zero runtime dependencies (Python
standard library + vanilla JavaScript).

## Cookies / local storage

The injected client uses `localStorage` to persist your pending comment
queue across page reloads, and `sessionStorage` to preserve scroll
position. Both stay in your browser, on your machine.

## License and contact

MIT. See [LICENSE](LICENSE) for terms.

Privacy questions: open an issue at
https://github.com/machbuilds/interactive-html/issues
