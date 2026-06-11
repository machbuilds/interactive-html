/*
 * Interactive HTML — in-page client.
 *
 * Lives inside an IIFE so nothing leaks onto window. The flow:
 *
 *   1. User selects text, picks an element, or opens "+ general"
 *      → a draft comment lands in the pending queue (persisted in localStorage)
 *   2. Submit → POST /comments → server writes <artifact>/.ih/comments.jsonl
 *   3. The page polls <artifact>/.ih/updates.json every few seconds.
 *   4. When the agent appends a batch whose `in_response_to` ids match our
 *      submitted comments, we reload with scroll preserved and offer a tour
 *      of the wrapped <span data-ih-change="ch-..."> regions.
 *
 * All DOM identifiers use the ih- prefix.  Data attributes use data-ih-*.
 */
(() => {
  if (window.__ihLoaded) return;
  window.__ihLoaded = true;

  // -- constants ----------------------------------------------------------
  const STATE_KEY = "ih-state-v1";
  const SCROLL_RESTORE_KEY = "ih-scroll-restore-v1";
  const POST_RELOAD_TOUR_KEY = "ih-auto-tour-v1";

  const UPDATES_URL = ".ih/updates.json";
  const COMMENTS_URL = "/comments";
  const SEEN_URL = "/_ih/seen";
  const EVENTS_URL = "/_ih/events";

  const FALLBACK_POLL_MS = 15000;
  const SUBMIT_STALE_MS = 90_000;
  const TEXT_QUOTE_LIMIT = 220;
  const HTML_SNIPPET_LIMIT = 600;

  const PICKABLE_TAGS = new Set([
    "P", "H1", "H2", "H3", "H4", "H5", "H6",
    "UL", "OL", "LI", "DL", "DT", "DD",
    "TABLE", "TR",
    "FIGURE", "IMG", "SVG", "CANVAS", "VIDEO",
    "BLOCKQUOTE", "PRE",
    "SECTION", "ARTICLE",
    "ASIDE", "DETAILS",
  ]);

  // -- tiny utilities -----------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const make = (tag, props = {}, html = "") => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") el.className = v;
      else if (k === "style") el.style.cssText = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else if (v != null && v !== false) el.setAttribute(k, v);
    }
    if (html) el.innerHTML = html;
    return el;
  };
  const escapeHTML = (s) =>
    String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  const truncate = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
  const newId = (prefix) => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const relTime = (iso) => {
    const t = Date.parse(iso);
    if (!t) return "";
    const s = Math.max(1, Math.floor((Date.now() - t) / 1000));
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return new Date(t).toLocaleDateString();
  };

  // -- persistent state ---------------------------------------------------
  const loadState = () => {
    try { return JSON.parse(localStorage.getItem(STATE_KEY) || "{}"); }
    catch { return {}; }
  };
  const saveState = (patch) => {
    const cur = loadState();
    const next = { ...cur, ...patch };
    try { localStorage.setItem(STATE_KEY, JSON.stringify(next)); } catch {}
  };

  // Per-tab in-memory state.
  const state = {
    pending: loadState().pending || [],
    seenUpdateIds: new Set(),
    history: [],
    eventSource: null,
    sseConnected: false,
    pollTimer: null,
    elementMode: false,
    regionMode: false,
    regionDrag: null,            // {x0,y0} while drawing
    picked: [],
    savedTextRange: null,
    activeEditor: null,
    busySince: null,
    submitted: loadState().submitted || null, // { batch_id, comment_ids, sent_at }
    // Questions we've submitted, kept so the Q&A tab can show the question
    // text next to the agent's answer (answers only reference comment ids).
    questions: loadState().questions || [],   // [{id, body, anchor, asked_at}]
    sawFirstHistory: false,
  };

  // -- anchor / selector --------------------------------------------------
  // A stable CSS selector built purely from structural position (or a real
  // id= attribute when one is present). We intentionally avoid attributes
  // that the client could add at runtime — the selector must work both
  // against the live DOM AND against the file as it sits on disk, so the
  // agent can find the same element.
  const stableSelector = (el) => {
    if (!el) return "";
    if (el.id) return `#${CSS.escape(el.id)}`;
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.body) {
      let part = cur.tagName.toLowerCase();
      if (cur.id) { part = `${part}#${CSS.escape(cur.id)}`; parts.unshift(part); break; }
      if (cur.parentElement) {
        const peers = [...cur.parentElement.children].filter((s) => s.tagName === cur.tagName);
        if (peers.length > 1) part += `:nth-of-type(${peers.indexOf(cur) + 1})`;
      }
      parts.unshift(part);
      cur = cur.parentElement;
    }
    return parts.join(" > ");
  };

  const elementInsideOurUI = (el) => !!(el && el.closest && el.closest(".ih-host"));

  const pickableAncestor = (node) => {
    let cur = node && node.nodeType === 3 ? node.parentElement : node;
    while (cur && cur !== document.body) {
      if (elementInsideOurUI(cur)) return null;
      if (PICKABLE_TAGS.has(cur.tagName)) return cur;
      cur = cur.parentElement;
    }
    return null;
  };

  const captureAnchor = (el) => {
    if (!el) return null;
    return {
      selector: stableSelector(el),
      tag: el.tagName,
      quote: truncate((el.textContent || "").trim(), TEXT_QUOTE_LIMIT),
      html_snippet: truncate(el.outerHTML, HTML_SNIPPET_LIMIT),
    };
  };

  // -- UI scaffolding -----------------------------------------------------
  let dom = {};
  const buildUI = () => {
    const host = make("div", { class: "ih-host", id: "ih-host" });

    // Launcher
    const launcher = make("button", { class: "ih-launcher", id: "ih-launcher", "aria-label": "Open comments panel" },
      `<span class="ih-launcher-icon" aria-hidden="true">💬</span><span class="ih-launcher-label">Comments</span><span class="ih-badge" id="ih-badge" hidden>0</span>`);

    // Busy indicator
    const busy = make("div", { class: "ih-busy", id: "ih-busy", hidden: true, role: "status" },
      `<span class="ih-spinner" aria-hidden="true"></span><span id="ih-busy-text">Agent is working…</span>`);

    // Text-selection popover
    const selpop = make("div", { class: "ih-selpop", id: "ih-selpop", role: "button" }, "💬 Comment");

    // Element-mode popover
    const elempop = make("div", { class: "ih-elempop", id: "ih-elempop" },
      `<button class="ih-primary" id="ih-elem-comment">Comment</button><button id="ih-elem-clear">Clear</button>`);

    // Editor card
    const editor = make("div", { class: "ih-editor", id: "ih-editor" },
      `<div class="ih-editor-head">
         <span class="ih-editor-kind" id="ih-editor-kind">Comment</span>
         <span id="ih-editor-where"></span>
       </div>
       <div class="ih-intent" id="ih-intent" role="radiogroup" aria-label="Comment intent">
         <button data-intent="change" class="is-active" aria-pressed="true">&#9998; Change</button>
         <button data-intent="question" aria-pressed="false">? Question</button>
       </div>
       <div class="ih-editor-quote" id="ih-editor-quote" hidden></div>
       <div class="ih-editor-body">
         <textarea id="ih-editor-text" placeholder="What should change?" rows="3"></textarea>
       </div>
       <div class="ih-editor-actions">
         <button id="ih-editor-cancel">Cancel</button>
         <button class="ih-primary" id="ih-editor-save">Add to queue</button>
       </div>`);

    // Panel
    const panel = make("div", { class: "ih-panel", id: "ih-panel", role: "dialog", "aria-label": "Comments" });
    panel.innerHTML = `
      <div class="ih-panel-head">
        <div class="ih-panel-title">Comments</div>
        <button class="ih-panel-close ih-ghost" id="ih-panel-close" aria-label="Close">✕</button>
      </div>
      <div class="ih-tabs">
        <button class="ih-tab is-active" data-tab="queue" id="ih-tab-queue">Queue</button>
        <button class="ih-tab" data-tab="qa" id="ih-tab-qa">Q&amp;A</button>
        <button class="ih-tab" data-tab="history" id="ih-tab-history">History</button>
      </div>
      <div class="ih-tab-body" id="ih-tab-body-queue">
        <div class="ih-tab-toolbar">
          <button id="ih-toggle-elem">⌖ Element picker</button>
          <button id="ih-toggle-region">▭ Region</button>
          <button id="ih-add-general">+ General</button>
        </div>
        <div id="ih-pending-list"></div>
      </div>
      <div class="ih-tab-body" id="ih-tab-body-qa" hidden>
        <div id="ih-qa-list"></div>
      </div>
      <div class="ih-tab-body" id="ih-tab-body-history" hidden>
        <div id="ih-history-list"></div>
      </div>
      <div class="ih-submit-bar">
        <span class="ih-count" id="ih-pending-count">0 in queue</span>
        <button id="ih-clear-pending">Clear</button>
        <button class="ih-primary" id="ih-submit">Submit</button>
      </div>`;

    // Tour
    const tour = make("div", { class: "ih-tour", id: "ih-tour" },
      `<div class="ih-tour-head">What changed</div>
       <div class="ih-tour-title" id="ih-tour-title"></div>
       <div class="ih-tour-actions">
         <button id="ih-tour-prev">‹ Prev</button>
         <button id="ih-tour-next">Next ›</button>
         <span class="ih-tour-step" id="ih-tour-step"></span>
         <button class="ih-ghost" id="ih-tour-exit" aria-label="Exit tour">✕</button>
       </div>`);

    // Toast
    const toast = make("div", { class: "ih-toast", id: "ih-toast" });

    // Region-draw overlay: a fixed full-viewport layer that captures the
    // drag, plus the live rectangle drawn inside it.
    const regionOverlay = make("div", { class: "ih-region-overlay", id: "ih-region-overlay", hidden: true },
      `<div class="ih-region-rect" id="ih-region-rect" hidden></div>`);

    host.append(launcher, busy, selpop, elempop, editor, panel, tour, toast, regionOverlay);
    document.body.appendChild(host);

    dom = {
      host, launcher, busy, selpop, elempop, editor, panel, tour, toast,
      regionOverlay,
      regionRect: $("ih-region-rect"),
      badge: $("ih-badge"),
      busyText: $("ih-busy-text"),
      editorKind: $("ih-editor-kind"),
      editorWhere: $("ih-editor-where"),
      editorQuote: $("ih-editor-quote"),
      editorText: $("ih-editor-text"),
      intentGroup: $("ih-intent"),
      pendingList: $("ih-pending-list"),
      historyList: $("ih-history-list"),
      qaList: $("ih-qa-list"),
      pendingCount: $("ih-pending-count"),
      tourTitle: $("ih-tour-title"),
      tourStep: $("ih-tour-step"),
      tabBodies: {
        queue: $("ih-tab-body-queue"),
        qa: $("ih-tab-body-qa"),
        history: $("ih-tab-body-history"),
      },
      tabs: {
        queue: $("ih-tab-queue"),
        qa: $("ih-tab-qa"),
        history: $("ih-tab-history"),
      },
    };
  };

  // -- toasts -------------------------------------------------------------
  let toastTimer = null;
  const toast = (msg, ms = 2400) => {
    dom.toast.textContent = msg;
    dom.toast.classList.add("is-visible");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => dom.toast.classList.remove("is-visible"), ms);
  };

  // -- panel + tabs -------------------------------------------------------
  const setPanelOpen = (open) => {
    dom.panel.classList.toggle("is-open", !!open);
  };
  const togglePanel = () => setPanelOpen(!dom.panel.classList.contains("is-open"));
  const setActiveTab = (name) => {
    Object.entries(dom.tabs).forEach(([k, btn]) => btn.classList.toggle("is-active", k === name));
    Object.entries(dom.tabBodies).forEach(([k, body]) => { body.hidden = k !== name; });
  };

  // -- selection popover (text mode) --------------------------------------
  const showSelectionPopover = (rect) => {
    const p = dom.selpop;
    p.style.top = `${window.scrollY + rect.bottom + 6}px`;
    p.style.left = `${window.scrollX + rect.left}px`;
    p.classList.add("is-visible");
  };
  const hideSelectionPopover = () => dom.selpop.classList.remove("is-visible");

  const onDocSelectionChange = () => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) { hideSelectionPopover(); state.savedTextRange = null; return; }
    const range = sel.getRangeAt(0);
    if (elementInsideOurUI(range.commonAncestorContainer)) return;
    const rect = range.getBoundingClientRect();
    if (!rect.width && !rect.height) return;
    state.savedTextRange = {
      range,
      quote: truncate(sel.toString().trim(), TEXT_QUOTE_LIMIT),
      anchorEl: pickableAncestor(range.commonAncestorContainer) || range.commonAncestorContainer.parentElement || document.body,
    };
    showSelectionPopover(rect);
  };

  // -- element-pick mode --------------------------------------------------
  const setElementMode = (on) => {
    state.elementMode = on;
    document.body.classList.toggle("ih-elem-mode-on", on);
    $("ih-toggle-elem").classList.toggle("ih-primary", on);
    if (!on) {
      hideElementPopover();
      clearPicked();
      removePickableMarkers();
      toast("Element picker off");
    } else {
      markPickable();
      toast("Click any region — shift-click to add more. Press E or Esc to exit.");
    }
  };

  const markPickable = () => {
    document.querySelectorAll("body *").forEach((el) => {
      if (elementInsideOurUI(el)) return;
      if (PICKABLE_TAGS.has(el.tagName)) el.setAttribute("data-ih-pickable", "");
    });
  };
  const removePickableMarkers = () => {
    document.querySelectorAll("[data-ih-pickable]").forEach((el) => el.removeAttribute("data-ih-pickable"));
  };

  const clearPicked = () => {
    state.picked.forEach((el) => el.removeAttribute("data-ih-picked"));
    state.picked = [];
  };

  const showElementPopover = (rect) => {
    const p = dom.elempop;
    p.style.top = `${window.scrollY + rect.top - p.offsetHeight - 8}px`;
    p.style.left = `${window.scrollX + rect.left}px`;
    p.classList.add("is-visible");
  };
  const hideElementPopover = () => dom.elempop.classList.remove("is-visible");

  const onElementClick = (e) => {
    if (!state.elementMode) return;
    const target = e.target.closest("[data-ih-pickable]");
    if (!target) return;
    if (elementInsideOurUI(target)) return;
    e.preventDefault();
    e.stopPropagation();
    if (!e.shiftKey) clearPicked();
    if (!state.picked.includes(target)) {
      state.picked.push(target);
      target.setAttribute("data-ih-picked", "true");
    }
    const rect = target.getBoundingClientRect();
    showElementPopover(rect);
  };

  // -- region-draw mode -----------------------------------------------------
  // For "I can't describe where, but it's THIS area": drag a rectangle and
  // we resolve it to the underlying elements, so the agent gets precise
  // anchors even when the user's words are vague.
  const setRegionMode = (on) => {
    state.regionMode = on;
    state.regionDrag = null;
    dom.regionOverlay.hidden = !on;
    dom.regionRect.hidden = true;
    $("ih-toggle-region").classList.toggle("ih-primary", on);
    if (on) {
      toast("Drag a box around the area you want to talk about. Esc to cancel.");
    }
  };

  const regionPoint = (e) => ({ x: e.clientX, y: e.clientY });

  const onRegionDown = (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    state.regionDrag = regionPoint(e);
    dom.regionRect.hidden = false;
    drawRegionRect(state.regionDrag, state.regionDrag);
  };

  const onRegionMove = (e) => {
    if (!state.regionDrag) return;
    drawRegionRect(state.regionDrag, regionPoint(e));
  };

  const drawRegionRect = (a, b) => {
    const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
    const w = Math.abs(a.x - b.x), h = Math.abs(a.y - b.y);
    Object.assign(dom.regionRect.style, { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px` });
  };

  const onRegionUp = (e) => {
    if (!state.regionDrag) return;
    const a = state.regionDrag, b = regionPoint(e);
    state.regionDrag = null;
    const rect = {
      x: Math.min(a.x, b.x), y: Math.min(a.y, b.y),
      width: Math.abs(a.x - b.x), height: Math.abs(a.y - b.y),
    };
    if (rect.width < 8 || rect.height < 8) { dom.regionRect.hidden = true; return; }
    const targets = elementsInRect(rect);
    if (!targets.length) {
      dom.regionRect.hidden = true;
      toast("Nothing recognizable in that area — try a bigger box");
      return;
    }
    const anchors = targets.map(captureAnchor);
    const primary = anchors[0];
    openEditor({
      kind: "region",
      anchor: {
        ...primary,
        multi: anchors.map((x) => x.selector),
        region: {
          // page coordinates so the agent (and future sessions) can reason
          // about it independent of current scroll position
          x: rect.x + window.scrollX,
          y: rect.y + window.scrollY,
          width: rect.width,
          height: rect.height,
        },
      },
      near: { left: rect.x, bottom: rect.y + rect.height, top: rect.y },
      prefillQuote: primary.quote,
    });
  };

  // Resolve a viewport rect to the most specific pickable elements inside it:
  // keep elements that overlap the rect, then drop any element whose
  // descendant is also selected (prefer the paragraph over its section).
  const elementsInRect = (rect) => {
    const hits = [];
    document.querySelectorAll("body *").forEach((el) => {
      if (elementInsideOurUI(el)) return;
      if (!PICKABLE_TAGS.has(el.tagName)) return;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return;
      const ox = Math.max(0, Math.min(rect.x + rect.width, r.right) - Math.max(rect.x, r.left));
      const oy = Math.max(0, Math.min(rect.y + rect.height, r.bottom) - Math.max(rect.y, r.top));
      const overlap = ox * oy;
      if (overlap <= 0) return;
      // Require meaningful overlap: ≥40% of the element OR ≥40% of the rect.
      const elArea = r.width * r.height;
      const rectArea = rect.width * rect.height;
      if (overlap >= 0.4 * elArea || overlap >= 0.4 * rectArea) hits.push(el);
    });
    const leafmost = hits.filter((el) => !hits.some((other) => other !== el && el.contains(other)));
    return leafmost.slice(0, 8);
  };

  // -- editor -------------------------------------------------------------
  const setEditorIntent = (intent) => {
    if (!state.activeEditor) return;
    state.activeEditor.intent = intent;
    dom.intentGroup.querySelectorAll("button").forEach((b) => {
      const on = b.dataset.intent === intent;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-pressed", String(on));
    });
    dom.editorText.placeholder = intent === "question"
      ? "What do you want to know about this?"
      : "What should change?";
  };

  const openEditor = ({ kind, anchor, near, prefillQuote, intent }) => {
    closeEditor();
    state.activeEditor = { kind, anchor, intent: "change" };
    const kindLabel = { general: "General", element: "Element", region: "Region", text: "Text" }[kind] || "Text";
    dom.editorKind.textContent = kindLabel;
    dom.editorWhere.textContent = anchor && anchor.tag ? `· ${anchor.tag.toLowerCase()}` : "";
    if (prefillQuote) {
      dom.editorQuote.hidden = false;
      dom.editorQuote.textContent = `"${prefillQuote}"`;
    } else {
      dom.editorQuote.hidden = true;
      dom.editorQuote.textContent = "";
    }
    dom.editorText.value = "";
    setEditorIntent(intent || "change");
    positionEditor(near);
    dom.editor.classList.add("is-visible");
    setTimeout(() => dom.editorText.focus(), 30);
  };

  const positionEditor = (rect) => {
    const W = 320, pad = 12;
    const vw = window.innerWidth, vh = window.innerHeight;
    let top, left;
    if (rect) {
      top = window.scrollY + Math.min(vh - 220, Math.max(rect.bottom + 8, 20));
      left = window.scrollX + Math.max(pad, Math.min(rect.left, vw - W - pad));
    } else {
      top = window.scrollY + vh / 2 - 80;
      left = window.scrollX + vw / 2 - W / 2;
    }
    dom.editor.style.top = `${top}px`;
    dom.editor.style.left = `${left}px`;
  };

  const closeEditor = () => {
    dom.editor.classList.remove("is-visible");
    state.activeEditor = null;
  };

  const saveEditor = () => {
    const ed = state.activeEditor;
    if (!ed) return;
    const body = dom.editorText.value.trim();
    if (!body) { dom.editorText.focus(); return; }
    const comment = {
      id: newId("c"),
      kind: ed.kind,
      intent: ed.intent || "change",
      anchor: ed.anchor,
      body,
      created_at: new Date().toISOString(),
    };
    state.pending.push(comment);
    persistPending();
    renderPending();
    updateBadge();
    closeEditor();
    hideSelectionPopover();
    if (state.elementMode) {
      hideElementPopover();
      clearPicked();
    }
    if (state.regionMode) setRegionMode(false);
    window.getSelection().removeAllRanges();
    setPanelOpen(true);
    setActiveTab("queue");
    toast(comment.intent === "question" ? "Question queued" : "Added to queue");
  };

  // -- pending list rendering --------------------------------------------
  const persistPending = () => saveState({ pending: state.pending });

  const renderPending = () => {
    const list = dom.pendingList;
    list.innerHTML = "";
    if (!state.pending.length) {
      list.appendChild(make("div", { class: "ih-empty" }, "Nothing queued. Highlight text or pick an element to add a comment."));
    } else {
      state.pending.forEach((c) => list.appendChild(renderPendingItem(c)));
    }
    dom.pendingCount.textContent = `${state.pending.length} in queue`;
  };

  const renderPendingItem = (c) => {
    const item = make("div", { class: "ih-item" });
    item.innerHTML = `
      <div class="ih-item-head">
        <span class="ih-item-kind">${c.intent === "question" ? "? " : ""}${escapeHTML(c.kind)}</span>
        <span class="ih-item-time">${escapeHTML(relTime(c.created_at))}</span>
      </div>
      ${c.anchor && c.anchor.quote ? `<span class="ih-item-quote">"${escapeHTML(c.anchor.quote)}"</span>` : ""}
      <div class="ih-item-body">${escapeHTML(c.body)}</div>
      <div class="ih-item-actions">
        <button data-act="remove">Remove</button>
        ${c.anchor && c.anchor.selector ? `<button data-act="locate">Find</button>` : ""}
      </div>`;
    item.querySelector('[data-act="remove"]').addEventListener("click", () => {
      state.pending = state.pending.filter((x) => x.id !== c.id);
      persistPending();
      renderPending();
      updateBadge();
    });
    const findBtn = item.querySelector('[data-act="locate"]');
    if (findBtn) findBtn.addEventListener("click", () => locateAnchor(c.anchor));
    return item;
  };

  const locateAnchor = (anchor) => {
    if (!anchor || !anchor.selector) return;
    let target = null;
    try { target = document.querySelector(anchor.selector); } catch {}
    if (!target) { toast("Couldn't find that element on this page"); return; }
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.add("is-tour-target");
    setTimeout(() => target.classList.remove("is-tour-target"), 1200);
  };

  const updateBadge = () => {
    const n = state.pending.length;
    dom.badge.hidden = n === 0;
    dom.badge.textContent = String(n);
  };

  // -- submit batch -------------------------------------------------------
  const setBusy = (on, msg) => {
    dom.busy.hidden = !on;
    if (msg) dom.busyText.textContent = msg;
    if (on) {
      state.busySince = Date.now();
      document.title = document.title.startsWith("● ") ? document.title : `● ${document.title}`;
    } else {
      state.busySince = null;
      document.title = document.title.replace(/^●\s/, "");
    }
  };

  const submitBatch = async () => {
    if (!state.pending.length) return;
    const batch = {
      batch_id: newId("b"),
      client_url: location.pathname + location.search + location.hash,
      submitted_at: new Date().toISOString(),
      comments: state.pending.map((c) => ({ ...c })),
    };
    setBusy(true, "Agent is working…");
    state.submitted = {
      batch_id: batch.batch_id,
      comment_ids: batch.comments.map((c) => c.id),
      sent_at: Date.now(),
    };
    // Remember question text locally — answers reference comment ids only.
    const asked = batch.comments
      .filter((c) => c.intent === "question")
      .map((c) => ({ id: c.id, body: c.body, anchor: c.anchor, asked_at: c.created_at }));
    if (asked.length) {
      state.questions = [...state.questions, ...asked].slice(-100);
    }
    saveState({ submitted: state.submitted, questions: state.questions });
    try {
      const res = await fetch(COMMENTS_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(batch),
      });
      if (!res.ok) throw new Error(`server returned ${res.status}`);
      state.pending = [];
      persistPending();
      renderPending();
      updateBadge();
      toast("Sent — waiting for changes");
    } catch (err) {
      setBusy(false);
      state.submitted = null;
      saveState({ submitted: null });
      toast(`Submit failed: ${err.message}`);
    }
  };

  // -- live updates: SSE w/ slow-poll fallback ----------------------------
  const startEvents = () => {
    fetchUpdates(); // immediate priming fetch on page load
    try {
      const es = new EventSource(EVENTS_URL);
      state.eventSource = es;
      es.addEventListener("ready", () => {
        state.sseConnected = true;
        stopFallbackPolling();
      });
      es.addEventListener("updates", () => fetchUpdates());
      es.addEventListener("progress", (e) => onProgressEvent(e.data));
      es.onerror = () => {
        // EventSource will auto-reconnect; meanwhile, run a slow poll so the
        // user isn't blind to updates if the SSE channel is wedged.
        state.sseConnected = false;
        startFallbackPolling();
      };
    } catch {
      // Browser without EventSource (very rare) — fall back to polling.
      startFallbackPolling();
    }
  };

  const startFallbackPolling = () => {
    if (state.pollTimer) return;
    state.pollTimer = setInterval(fetchUpdates, FALLBACK_POLL_MS);
  };

  const stopFallbackPolling = () => {
    if (!state.pollTimer) return;
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  };

  // Live status from the agent (via .ih/progress.json → 'progress' SSE event).
  // Only reflect it while we're actually waiting on a submitted batch, so a
  // stray progress file from another tab's work doesn't hijack the banner.
  const onProgressEvent = (raw) => {
    let info = null;
    try { info = JSON.parse(raw); } catch { return; }
    if (!info || !info.status) return;
    if (!state.submitted) return;
    if (info.phase === "done" || info.phase === "error") return;
    setBusy(true, info.status);
  };

  const fetchUpdates = async () => {
    try {
      const res = await fetch(`${UPDATES_URL}?t=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) return;
      const body = await res.json();
      const arr = Array.isArray(body) ? body : [];
      handleUpdates(arr);
    } catch {
      // network blips are expected when the agent is mid-write
    }
  };

  const handleUpdates = (updates) => {
    state.history = updates;
    renderHistory();
    renderQA();

    const firstPass = !state.sawFirstHistory;
    const newIds = [];
    updates.forEach((u) => {
      const id = u.batch_id || u.id;
      if (!id) return;
      if (!state.seenUpdateIds.has(id)) {
        state.seenUpdateIds.add(id);
        if (!firstPass) newIds.push(id);
      }
    });
    state.sawFirstHistory = true;

    // Stale-submit guard: if a batch was sent and updates.json hasn't changed
    // in SUBMIT_STALE_MS, clear the busy banner so the user isn't stuck.
    if (state.submitted && state.busySince && Date.now() - state.busySince > SUBMIT_STALE_MS) {
      const matched = updates.some((u) => responsesMatch(u, state.submitted.comment_ids));
      if (!matched) {
        setBusy(false);
        toast("Agent seems quiet — try again or check the inbox file");
      }
    }

    // If any new update responds to the submitted batch, react. Updates with
    // HTML changes reload the page (the file on disk changed); answer-only
    // updates surface in place — there's nothing to reload.
    if (state.submitted) {
      const matchedNow = updates.find((u) => responsesMatch(u, state.submitted.comment_ids));
      if (matchedNow && newIds.includes(matchedNow.batch_id || matchedNow.id)) {
        saveState({ submitted: null });
        state.submitted = null;
        if ((matchedNow.changes || []).length) {
          reloadWithTour(matchedNow);
          return;
        }
        setBusy(false);
        const n = (matchedNow.answers || []).length;
        toast(`${n} question${n === 1 ? "" : "s"} answered`);
        setPanelOpen(true);
        setActiveTab("qa");
      }
    } else if (newIds.length) {
      toast(`${newIds.length} new update${newIds.length > 1 ? "s" : ""}`);
    }
  };

  const responsesMatch = (update, commentIds) => {
    if (!update) return false;
    const replied = new Set();
    (update.changes || []).forEach((ch) => (ch.in_response_to || []).forEach((id) => replied.add(id)));
    (update.answers || []).forEach((a) => (a.in_response_to || []).forEach((id) => replied.add(id)));
    return commentIds.some((id) => replied.has(id));
  };

  // -- reload with tour ---------------------------------------------------
  const reloadWithTour = (update) => {
    try {
      sessionStorage.setItem(SCROLL_RESTORE_KEY, String(window.scrollY));
      sessionStorage.setItem(POST_RELOAD_TOUR_KEY, JSON.stringify({
        update_id: update.batch_id || update.id,
        anchors: (update.changes || []).map((c) => c.anchor || c.id),
      }));
    } catch {}
    setTimeout(() => location.reload(), 220);
  };

  const restoreScroll = () => {
    try {
      const y = sessionStorage.getItem(SCROLL_RESTORE_KEY);
      if (y != null) {
        window.scrollTo({ top: parseInt(y, 10), behavior: "instant" });
        sessionStorage.removeItem(SCROLL_RESTORE_KEY);
      }
    } catch {}
  };

  // -- tour ---------------------------------------------------------------
  let tour = { active: false, index: 0, anchors: [], update: null };
  const startTour = (update) => {
    const changes = update.changes || [];
    if (!changes.length) return;
    tour = { active: true, index: 0, anchors: changes, update };
    showTourStep();
  };
  const exitTour = () => {
    tour.active = false;
    dom.tour.classList.remove("is-visible");
    document.querySelectorAll(".is-tour-target").forEach((el) => el.classList.remove("is-tour-target"));
  };
  const tourStep = (delta) => {
    if (!tour.active) return;
    tour.index = (tour.index + delta + tour.anchors.length) % tour.anchors.length;
    showTourStep();
  };
  const showTourStep = () => {
    const ch = tour.anchors[tour.index];
    if (!ch) return;
    const anchorId = ch.anchor || ch.id;
    const node = anchorId && document.querySelector(`[data-ih-change="${CSS.escape(anchorId)}"]`);
    document.querySelectorAll(".is-tour-target").forEach((el) => el.classList.remove("is-tour-target"));
    if (node) {
      node.classList.add("is-tour-target");
      node.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    dom.tourTitle.textContent = ch.title || `Change ${tour.index + 1}`;
    dom.tourStep.textContent = `${tour.index + 1}/${tour.anchors.length}`;
    const rect = node ? node.getBoundingClientRect() : { top: window.innerHeight / 3, left: window.innerWidth / 2 - 160 };
    dom.tour.style.top = `${Math.max(12, rect.top - 12 - 110)}px`;
    dom.tour.style.left = `${Math.max(12, Math.min(window.innerWidth - 340, rect.left))}px`;
    dom.tour.classList.add("is-visible");
  };

  // -- Q&A rendering --------------------------------------------------------
  // Joins answers in history (which reference comment ids) with the question
  // text we stored locally at submit time.
  const collectQA = () => {
    const answersById = new Map(); // comment id -> {text, timestamp}
    state.history.forEach((u) => {
      (u.answers || []).forEach((a) => {
        (a.in_response_to || []).forEach((cid) => {
          answersById.set(cid, { text: a.text || "", timestamp: u.timestamp || "" });
        });
      });
    });
    return state.questions.map((q) => ({
      question: q,
      answer: answersById.get(q.id) || null,
    }));
  };

  const renderQA = () => {
    const list = dom.qaList;
    if (!list) return;
    list.innerHTML = "";
    const items = collectQA();
    if (!items.length) {
      list.appendChild(make("div", { class: "ih-empty" },
        "No questions yet. Open the comment editor on any text or element and switch to “? Question”."));
      return;
    }
    for (let i = items.length - 1; i >= 0; i--) {
      const { question, answer } = items[i];
      const node = make("div", { class: "ih-item ih-qa-item" });
      node.innerHTML = `
        <div class="ih-item-head">
          <span class="ih-item-kind">? question</span>
          <span class="ih-item-time">${escapeHTML(relTime(question.asked_at))}</span>
        </div>
        ${question.anchor && question.anchor.quote ? `<span class="ih-item-quote">"${escapeHTML(question.anchor.quote)}"</span>` : ""}
        <div class="ih-item-body">${escapeHTML(question.body)}</div>
        ${answer
          ? `<div class="ih-qa-answer">${escapeHTML(answer.text)}</div>`
          : `<div class="ih-qa-answer ih-qa-waiting">Waiting for an answer…</div>`}
        <div class="ih-item-actions">
          ${question.anchor && question.anchor.selector ? `<button data-act="locate">Find</button>` : ""}
          <button data-act="dismiss">Dismiss</button>
        </div>`;
      const findBtn = node.querySelector('[data-act="locate"]');
      if (findBtn) findBtn.addEventListener("click", () => locateAnchor(question.anchor));
      node.querySelector('[data-act="dismiss"]').addEventListener("click", () => {
        state.questions = state.questions.filter((q) => q.id !== question.id);
        saveState({ questions: state.questions });
        renderQA();
      });
      list.appendChild(node);
    }
  };

  // -- history rendering --------------------------------------------------
  const renderHistory = () => {
    const list = dom.historyList;
    list.innerHTML = "";
    if (!state.history.length) {
      list.appendChild(make("div", { class: "ih-empty" }, "No agent updates yet."));
      return;
    }
    for (let i = state.history.length - 1; i >= 0; i--) {
      const u = state.history[i];
      const node = make("div", { class: "ih-item" });
      const changes = u.changes || [];
      node.innerHTML = `
        <div class="ih-item-head">
          <span class="ih-item-kind">update</span>
          <span class="ih-item-time">${escapeHTML(relTime(u.timestamp || ""))}</span>
        </div>
        <div class="ih-item-body">${changes.map((c) =>
          `<div><strong>${escapeHTML(c.title || c.id || "")}</strong>${
            c.description ? `<div style="color:var(--ih-fg-muted);font-size:.83rem">${escapeHTML(c.description)}</div>` : ""
          }</div>`).join("")}</div>
        <div class="ih-item-actions">
          <button data-act="tour">Walk through</button>
        </div>`;
      node.querySelector('[data-act="tour"]').addEventListener("click", () => startTour(u));
      list.appendChild(node);
    }
  };

  // -- keyboard -----------------------------------------------------------
  const isTyping = (el) =>
    el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);

  const onKey = (e) => {
    if (isTyping(document.activeElement)) {
      if (e.key === "Escape" && state.activeEditor) closeEditor();
      return;
    }
    if (e.key === "Escape") {
      closeEditor();
      hideSelectionPopover();
      if (tour.active) exitTour();
      if (state.elementMode) setElementMode(false);
      if (state.regionMode) setRegionMode(false);
      return;
    }
    if (e.key === "?" || (e.key === "/" && e.shiftKey)) {
      toast("Highlight text or press E to pick an element. Esc to close. R to dismiss the tour.");
      return;
    }
    if (e.key === "e" || e.key === "E") {
      setElementMode(!state.elementMode);
      return;
    }
    if (e.key === "r" || e.key === "R") {
      if (tour.active) exitTour();
      return;
    }
    if (tour.active) {
      if (e.key === "ArrowRight") tourStep(1);
      if (e.key === "ArrowLeft") tourStep(-1);
    }
  };

  // -- event wiring -------------------------------------------------------
  const bindEvents = () => {
    dom.launcher.addEventListener("click", togglePanel);
    $("ih-panel-close").addEventListener("click", () => setPanelOpen(false));

    dom.tabs.queue.addEventListener("click", () => setActiveTab("queue"));
    dom.tabs.qa.addEventListener("click", () => setActiveTab("qa"));
    dom.tabs.history.addEventListener("click", () => setActiveTab("history"));

    $("ih-toggle-elem").addEventListener("click", () => setElementMode(!state.elementMode));
    $("ih-toggle-region").addEventListener("click", () => {
      if (state.elementMode) setElementMode(false);
      setRegionMode(!state.regionMode);
    });
    $("ih-add-general").addEventListener("click", () => {
      openEditor({ kind: "general", anchor: null, near: null });
    });

    dom.regionOverlay.addEventListener("mousedown", onRegionDown);
    dom.regionOverlay.addEventListener("mousemove", onRegionMove);
    dom.regionOverlay.addEventListener("mouseup", onRegionUp);

    dom.intentGroup.querySelectorAll("button").forEach((b) => {
      b.addEventListener("click", () => setEditorIntent(b.dataset.intent));
    });

    $("ih-submit").addEventListener("click", submitBatch);
    $("ih-clear-pending").addEventListener("click", () => {
      if (!state.pending.length) return;
      if (!confirm(`Drop ${state.pending.length} queued comment(s)?`)) return;
      state.pending = [];
      persistPending();
      renderPending();
      updateBadge();
    });

    dom.selpop.addEventListener("mousedown", (e) => e.preventDefault());
    dom.selpop.addEventListener("click", () => {
      if (!state.savedTextRange) return;
      const r = state.savedTextRange.range.getBoundingClientRect();
      openEditor({
        kind: "text",
        anchor: {
          ...captureAnchor(state.savedTextRange.anchorEl),
          quote: state.savedTextRange.quote,
        },
        near: r,
        prefillQuote: state.savedTextRange.quote,
      });
    });

    $("ih-elem-comment").addEventListener("click", () => {
      if (!state.picked.length) return;
      const targets = state.picked.slice();
      const anchors = targets.map(captureAnchor);
      const primary = anchors[0];
      const rect = targets[0].getBoundingClientRect();
      const aggregate = anchors.length === 1
        ? primary
        : { ...primary, multi: anchors.map((a) => a.selector) };
      openEditor({
        kind: "element",
        anchor: aggregate,
        near: rect,
        prefillQuote: primary.quote,
      });
    });
    $("ih-elem-clear").addEventListener("click", () => {
      clearPicked();
      hideElementPopover();
    });

    $("ih-editor-cancel").addEventListener("click", closeEditor);
    $("ih-editor-save").addEventListener("click", saveEditor);
    dom.editorText.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        saveEditor();
      }
    });

    $("ih-tour-prev").addEventListener("click", () => tourStep(-1));
    $("ih-tour-next").addEventListener("click", () => tourStep(1));
    $("ih-tour-exit").addEventListener("click", exitTour);

    document.addEventListener("selectionchange", debounce(onDocSelectionChange, 80));
    document.addEventListener("click", onElementClick, true);
    document.addEventListener("keydown", onKey);

    // Hide selection popover if user clicks anywhere not on it
    document.addEventListener("mousedown", (e) => {
      if (!e.target.closest("#ih-selpop")) hideSelectionPopover();
      if (!e.target.closest(".ih-elempop") && !state.elementMode) hideElementPopover();
    });
  };

  const debounce = (fn, ms) => {
    let t = null;
    return (...args) => { if (t) clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  };

  // -- post-reload tour pickup --------------------------------------------
  const consumePostReloadTour = () => {
    let info = null;
    try {
      const raw = sessionStorage.getItem(POST_RELOAD_TOUR_KEY);
      if (raw) {
        info = JSON.parse(raw);
        sessionStorage.removeItem(POST_RELOAD_TOUR_KEY);
      }
    } catch {}
    if (!info) return;
    // Wait for history fetch to populate; then start tour with the
    // matching update if present.
    const tryStart = (attempts) => {
      const match = state.history.find((u) => (u.batch_id || u.id) === info.update_id);
      if (match) { startTour(match); return; }
      if (attempts <= 0) return;
      setTimeout(() => tryStart(attempts - 1), 250);
    };
    tryStart(20);
  };

  // -- bootstrap ----------------------------------------------------------
  const init = () => {
    buildUI();
    bindEvents();
    restoreScroll();
    renderPending();
    updateBadge();
    renderHistory();
    renderQA();
    startEvents();
    consumePostReloadTour();

    // Resume "agent working" indicator if we reloaded mid-flight
    if (state.submitted && Date.now() - (state.submitted.sent_at || 0) < SUBMIT_STALE_MS) {
      setBusy(true, "Agent is working…");
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
