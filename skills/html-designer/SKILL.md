---
name: html-designer
description: Create a polished, self-contained HTML page from a description, content, or notes — then offer to make it interactive so the user can iterate by commenting on the page itself. Trigger phrases — "build me a page", "create an html page", "turn this into a page", "make a page about", "design a page for", "make an interactive page from this".
---

# HTML Designer

Generate a production-quality, self-contained HTML page from whatever the
user has — a description, pasted content, notes, a markdown doc — and then
offer to make it interactive (via the `interactive-html` skill) so they can
iterate by commenting on the page instead of describing changes in chat.

## When to invoke

- "build me a page (about X)" / "create an html page" → **Generate flow**
- "turn this into a page" / "make a page from this content" → **Generate flow**
- "make an interactive page from this" → **Generate flow**, then auto-chain
  into the `interactive-html` skill's setup flow

## Generate flow

### 1. Gather just enough context

If the user gave content or a clear description, don't interrogate them —
infer and build. Only ask when genuinely ambiguous, and at most once:

- **Purpose & audience** — who reads this and what should they do/feel?
- **Visual feel** — light or dark, dense or spacious, serious or playful?
- **Structure** — report, landing page, dashboard, docs, portfolio?

Default when unstated: light theme with `prefers-color-scheme` dark
support, spacious, content-first.

### 2. Write the page

One self-contained `*.html` file. Inline CSS in a `<style>` block. No
external dependencies — no CDNs, no Google Fonts, no frameworks. The file
must render perfectly from `file://` and offline.

Where to save: the current directory unless the user names one. Filename
from the content (`q3-report.html`, not `page1.html`).

#### Quality baseline (always)

- **Semantic HTML5** — `<header>`, `<nav>`, `<main>`, `<section>`,
  `<article>`, `<footer>`; one `<h1>`; heading levels never skip.
- **System font stack** — `-apple-system, BlinkMacSystemFont, "Segoe UI",
  system-ui, sans-serif` (and a mono stack for code). Crisp everywhere,
  zero network requests.
- **Design tokens as CSS custom properties** — colors, spacing scale, and
  radii defined once in `:root`, used everywhere. Makes later edits (and
  agent edits via interactive comments) surgical.
- **Responsive by default** — readable at 375px, 768px, 1024px, 1440px.
  Prefer intrinsic layouts (max-width, flex/grid with minmax, `clamp()`
  for type) over breakpoint thickets.
- **Dark mode is mandatory, not optional.** Every page ships both modes:
  define all colors as tokens in `:root`, flip them in a single
  `@media (prefers-color-scheme: dark)` block, and never hardcode a color
  outside the tokens. A page that breaks in either mode is not done.
- **Reduced motion** — wrap animations in `prefers-reduced-motion: no-preference`.
- **Diagrams are inline SVG.** When the content describes structure, flow,
  sequence, comparison, or architecture, draw it — don't describe it in a
  paragraph. Inline `<svg>` only (no external images, no Mermaid script
  dependency). Use `currentColor` for strokes/text and the CSS tokens for
  fills so every diagram adapts to dark mode automatically. Label axes and
  nodes; a diagram that needs the surrounding prose to be understood has
  failed.
- **Accessibility** — visible focus states (`:focus-visible`), ARIA only
  where semantics don't already cover it, alt text, 44px minimum touch
  targets, AA contrast.
- **Real content** — never lorem ipsum. If the user's content is thin,
  write plausible, specific copy from context and mark anything invented
  with an HTML comment `<!-- TODO: verify -->`.

#### Design judgment (apply, don't recite)

- **Users scan, they don't read.** Strong visual hierarchy: prominence =
  importance. Clear sections. Front-load key terms in headings.
- **Don't make them think.** Self-evident beats clever. Use conventions —
  logo/title top-left, nav where people expect it.
- **Omit ruthlessly.** Cut half the words, then cut again. No happy talk,
  no filler introductions, no "Welcome to…".
- **One focal point per screen.** If everything shouts, nothing is heard.
- **Group related things visually; contain nested things.** Whitespace is
  the grouping tool, borders are the fallback.
- **Make clickable things look clickable** without hover — shape, color,
  placement.

#### Anti-slop list (never)

- Purple-to-blue gradient heroes as a default aesthetic
- Generic three-column "feature" grids with icon + blurb
- Decorative blobs, waves, or floating geometric shapes
- "Get Started" / "Learn More" buttons that lead nowhere
- Cookie-cutter testimonial sections with invented names
- Emoji as a substitute for visual design
- Drop-shadow rounded cards as the only component idea
- Center-everything layouts with no hierarchy
- Stock-photo placeholder rectangles

### 2.5 Self-QA before delivering (mandatory)

Run this checklist against the file you just wrote. Fix failures before
showing the user — do not deliver a page that fails any of these:

- [ ] **Both color modes** — mentally render light and dark: every text/
      background pair stays readable, no hardcoded colors outside tokens
- [ ] **Heading hierarchy** — exactly one `<h1>`, no skipped levels
- [ ] **No placeholder content** — zero lorem ipsum; invented facts are
      marked `<!-- TODO: verify -->`
- [ ] **Zero network requests** — no CDN links, no Google Fonts, no
      external images; the page works from `file://` with WiFi off
- [ ] **375px sanity** — nothing overflows horizontally; touch targets
      ≥44px
- [ ] **SVGs in both modes** — diagrams use `currentColor`/tokens, legible
      on dark backgrounds
- [ ] **Focus visible** — interactive elements have a `:focus-visible`
      state

### 3. Show, then offer the loop

1. Tell the user the file path you wrote.
2. Offer to open it (`open <file>` on macOS).
3. **Offer to make it interactive** — this is the signature move:

   > "Want to iterate on it by commenting on the page itself? Say *make it
   > interactive* and I'll start the comment loop."

   If they accept (or if the original request was "make an interactive
   page…"), invoke the **`interactive-html` skill's Setup flow** on the
   file's directory. From then on the user highlights and comments in the
   page, and you respond by editing the HTML.

## Revision etiquette

When the user iterates (in chat or via interactive comments):

- Edit the existing file; never regenerate from scratch unless asked —
  regeneration destroys their accumulated tweaks.
- Keep edits scoped to what was asked. The design tokens make global
  changes (colors, spacing) one-line edits.
- If a request conflicts with the quality baseline (e.g. "add a purple
  gradient hero"), do what they asked — the baseline is a default, not a
  veto over the user.

## Relationship to interactive-html

This skill creates pages; `interactive-html` iterates on them. They chain:

```
"make a page about our Q3 results"        → html-designer writes q3-report.html
"make it interactive"                     → interactive-html starts the loop
[user highlights a chart caption]         → you edit, page reloads with a tour
```

If the user starts with "make an interactive page from this", run both
flows back-to-back without making them ask twice.
