/* ============================================================================
   Telmekon AI Assistant — application wiring
   ---------------------------------------------------------------------------
   Zero-build ES module: the browser loads this file directly (`type="module"`),
   there is no bundler, no framework and no CDN. It is split so that every part
   can be tested on its own:

     api.js       the only module that talks to the network
     store.js     conversations + settings (localStorage, reducer-style)
     theme.js     light / dark / system
     markdown.js  the answer renderer (escaping, tables, [n] citation markers)
     render.js    payload -> HTML strings (pure, no DOM)
     icons.js     inline SVG icon set
     app.js       this file: DOM, events, focus, and nothing else

   HONESTY RULES this file follows (the brief forbids simulating a backend):
     * every visible control issues a real request or changes real local state;
     * features the API does not have (streaming, server-side history, a
       feedback endpoint, a regenerate endpoint) are labelled as such in the
       settings dialog, driven by `CAPABILITIES` in api.js — never faked;
     * conversations restored from localStorage re-render exactly what the API
       returned before; their raw-`/search` pane starts empty and says so until
       the user presses "Re-run live";
     * an answer that could not be produced renders the API's own error text and,
       for a 503, the API's own `fix` command.

   The paths below are the display strings used in the raw-JSON drawer; the
   requests themselves are built in api.js from the same endpoints.
   ========================================================================== */

import { brandMark, icon } from "./icons.js";
import {
  CAPABILITIES,
  MAX_QUESTION_CHARS,
  ask,
  health,
  mediaUrl,
  products,
  search,
  searchUrlFor,
} from "./api.js";
import {
  activeConversation,
  addMessage,
  askOptions,
  createStore,
  deleteConversation,
  newConversation,
  searchConversations,
  selectConversation,
  setFeedback,
  titleFrom,
  updateMessage,
  updateSettings,
} from "./store.js";
import { initTheme } from "./theme.js";
import {
  assistantCardHTML,
  assistantShellHTML,
  citationModalHTML,
  conversationListHTML,
  drawerBodyHTML,
  errorCardHTML,
  statusChipHTML,
  thinkingHTML,
  userMessageHTML,
} from "./render.js";
import { esc, highlightJson, plainText } from "./markdown.js";

/** Displayed in the drawer's request line; api.js does the actual fetching. */
const ASK_PATH = "/ask";
const SEARCH_PATH = "/search";
const HEALTH_PATH = "/health";

const HEALTH_REFRESH_MS = 60_000;
const MOBILE_QUERY = "(max-width: 900px)";

// ---------------------------------------------------------------------------
// DOM
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);

const els = {
  app: $("app"),
  sidebar: $("sidebar"),
  sidebarBackdrop: $("sidebar-backdrop"),
  newChat: $("new-chat"),
  convSearch: $("conv-search"),
  convList: $("conv-list"),
  convCount: $("conv-count"),
  sideStatus: $("side-status"),
  openSidebar: $("open-sidebar"),
  toggleSidebar: $("toggle-sidebar"),
  chatTitle: $("chat-title"),
  chatSub: $("chat-sub"),
  status: $("status"),
  statusLabel: $("status-label"),
  docsLink: $("docs-link"),
  scroller: $("scroller"),
  capabilities: $("capabilities"),
  welcome: $("welcome"),
  chat: $("chat"),
  toBottom: $("to-bottom"),
  healthBanner: $("health-banner"),
  form: $("ask-form"),
  input: $("input"),
  send: $("send"),
  charCount: $("char-count"),
  scopeRow: $("scope-row"),
  live: $("live"),
  citationOverlay: $("citation-overlay"),
  citationBody: $("citation-body"),
  drawer: $("drawer"),
  drawerBackdrop: $("drawer-backdrop"),
  drawerUrl: $("drawer-url"),
  drawerTabs: $("drawer-tabs"),
  drawerMeta: $("drawer-meta"),
  drawerJson: $("drawer-json"),
  drawerRerun: $("drawer-rerun"),
  lightbox: $("lightbox"),
  lightboxImg: $("lightbox-img"),
  lightboxCaption: $("lightbox-caption"),
  lightboxLabels: $("lightbox-labels"),
  toast: $("toast"),
  settingsOverlay: $("settings-overlay"),
  settingsBody: $("settings-body"),
};

// ---------------------------------------------------------------------------
// state
// ---------------------------------------------------------------------------
const store = createStore(typeof localStorage === "undefined" ? null : localStorage, {
  onWarn: (message) => notify(message, "warn"),
});

/** UI-only state — never persisted, never presented as server state. */
const ui = {
  busy: false,
  health: null,
  healthTimer: null,
  tickTimer: null,
  startedAt: 0,
  pendingId: null,
  filter: "",
  drawer: { messageId: null, tab: "search" },
  overlayStack: [],
  lastFocus: null,
  productCache: null,
  deletedSnapshot: null,
};

const settings = () => store.state.settings;
const conversation = () => activeConversation(store.state);
const findMessage = (id) => {
  for (const c of store.state.conversations) {
    const found = c.messages.find((m) => m.id === id);
    if (found) return { conversation: c, message: found };
  }
  return null;
};

// ---------------------------------------------------------------------------
// small helpers
// ---------------------------------------------------------------------------
function announce(text) {
  if (els.live) els.live.textContent = text || "";
}

let toastTimer = null;
/** A toast is a real message about a real outcome — never a fake success. */
function notify(message, kind = "info", action = null) {
  if (!els.toast) return;
  clearTimeout(toastTimer);
  els.toast.className = `toast${kind === "warn" ? " toast--warn" : kind === "error" ? " toast--error" : ""}`;
  els.toast.innerHTML = `<span>${esc(message)}</span>`;
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "toast-action";
    button.textContent = action.label;
    button.addEventListener("click", () => {
      hideToast();
      action.onClick();
    });
    els.toast.appendChild(button);
  }
  els.toast.hidden = false;
  toastTimer = setTimeout(hideToast, action ? 7000 : 3600);
}

function hideToast() {
  clearTimeout(toastTimer);
  if (els.toast) els.toast.hidden = true;
}

/** Keep only what survives JSON: an `Error`'s `message` is not enumerable. */
function serializeError(error) {
  return {
    kind: error?.kind || "http",
    message: error?.message || String(error || "Unknown error"),
    status: error?.status ?? null,
    reason: error?.reason || null,
    fix: error?.fix || null,
  };
}

function scrollToBottom(force = false) {
  if (!els.scroller) return;
  const near = els.scroller.scrollHeight - els.scroller.scrollTop - els.scroller.clientHeight < 140;
  if (force || near || settings().autoScroll) {
    els.scroller.scrollTo({ top: els.scroller.scrollHeight, behavior: force ? "auto" : "smooth" });
  }
}

// ---------------------------------------------------------------------------
// overlays: citation modal, drawer, lightbox, settings — one focus discipline
// ---------------------------------------------------------------------------
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function openOverlay(panel, { onClose = null, labelledBy = null } = {}) {
  if (!panel) return;
  ui.lastFocus = document.activeElement;
  panel.hidden = false;
  ui.overlayStack.push({ panel, onClose });
  if (labelledBy) panel.setAttribute("aria-labelledby", labelledBy);
  const first = panel.querySelector(FOCUSABLE);
  (first || panel).focus?.();
}

function closeOverlay(panel) {
  const index = ui.overlayStack.findIndex((entry) => entry.panel === panel);
  if (index === -1) return;
  const [entry] = ui.overlayStack.splice(index, 1);
  entry.panel.hidden = true;
  if (!ui.overlayStack.length) {
    ui.lastFocus?.focus?.();
    ui.lastFocus = null;
  }
  entry.onClose?.();
}

function closeTopOverlay() {
  const top = ui.overlayStack[ui.overlayStack.length - 1];
  if (top) closeOverlay(top.panel);
}

function trapFocus(panel, event) {
  const nodes = [...panel.querySelectorAll(FOCUSABLE)].filter((n) => n.getClientRects().length > 0);
  if (!nodes.length) return;
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

// -- citation modal ---------------------------------------------------------
let currentCitation = null;

function openCitation(messageId, n) {
  const found = findMessage(messageId);
  const citations = found?.message?.payload?.citations || [];
  currentCitation = citations.find((c) => String(c.n) === String(n)) || null;
  if (currentCitation) {
    els.citationBody.innerHTML = citationModalHTML(currentCitation, { mediaUrl, icons: icon });
  } else {
    // A `[n]` marker with no citation behind it is a payload defect: say so
    // instead of showing an empty dialog that looks like a source.
    els.citationBody.innerHTML = `
      <div class="cit-head"><span class="cit-n">${esc(n)}</span>
        <h3 class="cit-title" id="citation-title">No source ${esc(n)} in this response</h3></div>
      <p class="cit-missing">The answer cites [${esc(n)}] but <code>POST /ask</code> returned
        ${citations.length} citation(s)${citations.length ? `: ${esc(citations.map((c) => c.n).join(", "))}` : ""}.
        Nothing is shown here rather than inventing a source.</p>`;
  }
  openOverlay(els.citationOverlay, { labelledBy: "citation-title" });
}

// -- lightbox ---------------------------------------------------------------
/**
 * Full-size figure. The `Labels` legend is rebuilt here (not in render.js) so
 * the lightbox carries the same numbered callouts the manual page has.
 */
function openLightbox(src, { caption = "", labels = [], alt = "" } = {}) {
  const url = mediaUrl(src);
  if (!url) {
    notify("This figure has no image path in the API response.", "warn");
    return;
  }
  els.lightboxImg.src = url;
  els.lightboxImg.alt = alt || caption || "Manual figure";
  els.lightboxCaption.textContent = caption || "";
  els.lightboxCaption.hidden = !caption;
  const list = (labels || []).filter(Boolean);
  els.lightboxLabels.innerHTML = list.length
    ? `<p class="labels-title">Labels</p><ul class="figure-labels">${list
        .map((label) => {
          const match = String(label).match(/^(\d+)\s+(.*)$/);
          return match
            ? `<li class="label-chip"><b>${esc(match[1])}</b>${esc(match[2])}</li>`
            : `<li class="label-chip">${esc(label)}</li>`;
        })
        .join("")}</ul>`
    : "";
  els.lightboxLabels.hidden = !list.length;
  openOverlay(els.lightbox);
}

// -- the raw-JSON drawer (Bonus 01) ----------------------------------------
/**
 * Open the drawer for one answer. Two tabs, both backed by real requests:
 *
 *   GET  /search   the retrieval layer on its own, re-run live on demand
 *   POST /ask      the exact request this UI sent and the exact response it got
 *
 * @param {string} messageId
 * @param {"search"|"ask"} tab
 */
export function openDrawer(messageId, tab = "search") {
  const found = findMessage(messageId);
  if (!found) {
    notify("That answer is no longer in this browser's history.", "warn");
    return;
  }
  ui.drawer = { messageId, tab };
  els.drawerBackdrop.hidden = false;
  paintDrawer();
  openOverlay(els.drawer, {
    labelledBy: "drawer-title",
    onClose: () => {
      els.drawerBackdrop.hidden = true;
    },
  });
}

function closeDrawer() {
  els.drawerBackdrop.hidden = true;
  closeOverlay(els.drawer);
}

function paintDrawer() {
  const found = findMessage(ui.drawer.messageId);
  if (!found) return;
  const message = found.message;
  const tab = ui.drawer.tab;
  const opts = askOptions(settings());

  for (const button of els.drawerTabs.querySelectorAll("[data-tab]")) {
    const active = button.dataset.tab === tab;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  }

  if (tab === "ask") {
    els.drawerUrl.textContent = `POST ${ASK_PATH}`;
    els.drawerMeta.innerHTML = [
      ["question", message.question],
      ["route", message.payload?.route],
      ["language", message.payload?.language],
      ["provider", message.payload?.provider],
      ["citations", (message.payload?.citations || []).length],
      ["latency_ms", message.payload?.latency_ms],
    ]
      .filter(([, value]) => value != null && value !== "")
      .map(([key, value]) => `<span class="dm-item">${esc(key)} <b>${esc(value)}</b></span>`)
      .join("");
    els.drawerJson.innerHTML = message.payload
      ? highlightJson({ request: message.request || { question: message.question }, response: message.payload })
      : `<span class="j-null">// This answer has no stored response.\n// Press “Re-run live” to call POST ${ASK_PATH} again.</span>`;
    els.drawerRerun.textContent = "Re-run POST /ask";
    return;
  }

  els.drawerUrl.textContent = searchUrlFor(message.question, opts);
  const body = drawerBodyHTML(message);
  els.drawerMeta.innerHTML = body.meta;
  els.drawerJson.innerHTML = body.json;
  els.drawerRerun.textContent = "Re-run live";
}

/** Re-run the active drawer tab. Both branches are real network calls. */
async function rerunDrawer() {
  const found = findMessage(ui.drawer.messageId);
  if (!found || ui.busy) return;
  const message = found.message;
  const opts = askOptions(settings());
  els.drawerRerun.disabled = true;
  const started = performance.now();
  try {
    if (ui.drawer.tab === "ask") {
      const payload = await ask(message.question, { ...opts, ...(message.request || {}) });
      store.set((state) => updateMessage(state, found.conversation.id, message.id, { payload, error: null, pending: false }));
      paintMessage({ ...message, payload, error: null, pending: false });
      notify(`POST ${ASK_PATH} answered again in ${Math.round(performance.now() - started)} ms.`);
    } else {
      const payload = await search(message.question, opts);
      store.set((state) => updateMessage(state, found.conversation.id, message.id, { search: payload, searchError: null }));
      notify(`GET ${SEARCH_PATH} returned ${(payload?.hits || []).length} hit(s).`);
    }
    paintDrawer();
  } catch (error) {
    const detail = serializeError(error);
    if (ui.drawer.tab === "search") {
      store.set((state) => updateMessage(state, found.conversation.id, message.id, { searchError: detail.message }));
    }
    paintDrawer();
    notify(detail.message, "error");
  } finally {
    els.drawerRerun.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// painting
// ---------------------------------------------------------------------------
function messageHTML(message) {
  if (message.role === "user") return userMessageHTML(message);
  if (message.pending) return assistantShellHTML(message.id, thinkingHTML());
  if (message.error) {
    return assistantShellHTML(message.id, errorCardHTML(message.error, message.question, { icons: icon }));
  }
  return assistantShellHTML(message.id, assistantCardHTML(message, { mediaUrl, icons: icon }));
}

/** Replace (or append) exactly one message element — no full re-render. */
function paintMessage(message) {
  const existing = els.chat.querySelector(`[data-msg-id="${CSS.escape(message.id)}"]`);
  const html = messageHTML(message);
  if (existing) {
    const template = document.createElement("template");
    template.innerHTML = html.trim();
    existing.replaceWith(template.content.firstChild);
  } else {
    els.chat.insertAdjacentHTML("beforeend", html);
  }
}

/** Titles, welcome/chat visibility and the scope chips — no message rebuild. */
function syncHeader() {
  const conv = conversation();
  const messages = conv?.messages || [];
  els.chat.hidden = messages.length === 0;
  els.welcome.hidden = messages.length > 0;
  els.chatTitle.textContent = conv && conv.messages.length ? conv.title : "Telmekon AI Assistant";
  els.chatSub.textContent = messages.length
    ? `${messages.filter((m) => m.role === "user").length} question(s) · stored in this browser`
    : "Grounded answers from the Pudu Robotics manuals — every claim cited";
  els.toBottom.hidden = messages.length === 0;
  updateScopeRow();
}

function paintConversation() {
  const conv = conversation();
  els.chat.innerHTML = (conv?.messages || []).map(messageHTML).join("");
  syncHeader();
  scrollToBottom(true);
}

function paintSidebar() {
  const state = store.state;
  const list = searchConversations(state, ui.filter);
  els.convList.innerHTML = conversationListHTML(list, state.activeId);
  els.convCount.textContent = String(list.length);
  els.convSearch.value = ui.filter;
}

function updateScopeRow() {
  const s = settings();
  const chips = [];
  if (s.product) chips.push(["product", s.product, "product"]);
  if (s.language) chips.push(["answer in", s.language.toUpperCase(), "language"]);
  if (s.provider) chips.push(["generator", s.provider, "provider"]);
  if (s.crossLingualFallback === false) chips.push(["cross-lingual fallback", "off", "crossLingualFallback"]);
  if (s.followUserLanguage === false) chips.push(["follow question language", "off", "followUserLanguage"]);
  if (!chips.length) {
    els.scopeRow.hidden = true;
    els.scopeRow.innerHTML = "";
    return;
  }
  els.scopeRow.hidden = false;
  els.scopeRow.innerHTML = chips
    .map(
      ([label, value, key]) =>
        `<span class="scope-chip" title="Change this in Settings">${esc(label)} <b>${esc(value)}</b>
           <button type="button" data-action="clear-scope" data-key="${key}" aria-label="Remove ${esc(label)} filter">×</button></span>`
    )
    .join("");
}

function paintStatus() {
  const chip = statusChipHTML(ui.health, { icons: icon });
  els.status.className = `status ${chip.className}${ui.health ? "" : " status--loading"}`;
  els.status.title = chip.title || "";
  els.statusLabel.textContent = chip.label;
  els.status.setAttribute("aria-label", `API status: ${chip.label}. Activate to check ${HEALTH_PATH} again.`);
  els.sideStatus.className = `side-status ${chip.className === "status--ready" ? "is-ready" : "is-down"}`;
  els.sideStatus.title = chip.title || "";
  els.sideStatus.querySelector(".label").textContent = chip.label;

  const banner = els.healthBanner;
  if (ui.health && ui.health.reachable !== false && !ui.health.ready) {
    banner.hidden = false;
    banner.innerHTML = `${icon("alert", { size: 16 })}
      <p><b>The retrieval index is not built.</b> ${esc(ui.health.detail || "")}
      Run <code>python -m ingest.run --data data</code> once, then press Retry — questions will fail with
      <code>503</code> until then.</p>`;
  } else {
    banner.hidden = true;
  }
}

async function refreshHealth({ quiet = false } = {}) {
  try {
    ui.health = await health();
  } catch (error) {
    ui.health = { reachable: false, ready: false, detail: serializeError(error).message };
  }
  paintStatus();
  if (!quiet) {
    const chip = statusChipHTML(ui.health, { icons: icon });
    notify(`${HEALTH_PATH}: ${chip.label}`, ui.health?.ready ? "info" : "warn");
  }
  return ui.health;
}

// ---------------------------------------------------------------------------
// asking
// ---------------------------------------------------------------------------
function validate(question) {
  if (!question) return "Type a question first.";
  if (question.length > MAX_QUESTION_CHARS) {
    return `Questions are limited to ${MAX_QUESTION_CHARS} characters (the API rejects longer ones with 422).`;
  }
  return null;
}

/**
 * One question = two real calls in parallel:
 *   POST /ask    the grounded answer (the message)
 *   GET  /search the retrieval layer behind it (the raw drawer, Bonus 01)
 * `allSettled` so a `/search` failure never hides a good answer.
 */
async function submitQuestion(rawQuestion, { regenerateOf = null } = {}) {
  const question = String(rawQuestion || "").replace(/\s+/g, " ").trim();
  const problem = validate(question);
  if (problem) {
    notify(problem, "warn");
    return;
  }
  if (ui.busy) {
    notify("Still working on the previous question.", "warn");
    return;
  }

  const opts = askOptions(settings());
  const active = conversation();
  const messages = active?.messages || [];
  const targetIndex = regenerateOf
    ? messages.findIndex((message) => message.id === regenerateOf)
    : messages.length;
  const history = messages
    .slice(0, targetIndex < 0 ? messages.length : targetIndex)
    .filter((message) => message.role === "user")
    .map((message) => message.question || message.text || "")
    .filter(Boolean)
    .slice(-6);
  const request = { question, ...opts, ...(history.length ? { history } : {}) };
  ui.busy = true;
  ui.startedAt = performance.now();
  els.send.disabled = true;
  els.input.disabled = Boolean(regenerateOf);

  let messageId;
  if (regenerateOf) {
    messageId = regenerateOf;
    const found = findMessage(messageId);
    store.set((state) =>
      updateMessage(state, found.conversation.id, messageId, { pending: true, error: null, request })
    );
    paintMessage({ ...found.message, pending: true, error: null, request });
  } else {
    const userMessage = { id: `u-${Date.now().toString(36)}`, role: "user", question, text: question, createdAt: new Date().toISOString() };
    messageId = `a-${Date.now().toString(36)}`;
    const botMessage = { id: messageId, role: "assistant", question, request, pending: true, createdAt: new Date().toISOString() };
    store.set((state) => addMessage(addMessage(state, userMessage), botMessage));
    els.input.value = "";
    autosize();
    updateCharCount();
    paintSidebar();
    paintMessage(userMessage);
    paintMessage(botMessage);
  }

  syncHeader();
  announce("Asking the manuals…");
  startTicker(messageId);

  const [askResult, searchResult] = await Promise.allSettled([ask(question, request), search(question, opts)]);

  stopTicker();
  ui.busy = false;
  els.send.disabled = !els.input.value.trim();
  els.input.disabled = false;

  const found = findMessage(messageId);
  if (!found) return;

  if (askResult.status === "fulfilled") {
    const payload = askResult.value;
    const patch = {
      payload,
      pending: false,
      error: null,
      search: searchResult.status === "fulfilled" ? searchResult.value : null,
      searchError: searchResult.status === "rejected" ? serializeError(searchResult.reason).message : null,
    };
    store.set((state) => updateMessage(state, found.conversation.id, messageId, patch));
    paintMessage({ ...found.message, ...patch });
    paintSidebar();
    scrollToBottom();
    announceCrossLingual(messageId);
    const citations = (payload.citations || []).length;
    announce(`Answer ready in ${payload.language || "the question's language"} with ${citations} source(s).`);
    // A successful answer proves the index exists: clear a stale "not built"
    // banner instead of waiting for the next 60 s poll.
    if (ui.health && !ui.health.ready) refreshHealth({ quiet: true });
    // Say why the answer is quoted passages, with the server's own reason. It
    // used to always blame a missing key, which is wrong when the key is set
    // and the Gemini call or the evidence check failed.
    if (payload.provider === "offline" && payload.fallback_reason) {
      notify(`Quoted from the manuals instead of written by the model: ${payload.fallback_reason}.`, "warn");
      refreshHealth({ quiet: true });
    }
  } else {
    const error = serializeError(askResult.reason);
    const patch = { pending: false, error, payload: null };
    store.set((state) => updateMessage(state, found.conversation.id, messageId, patch));
    paintMessage({ ...found.message, ...patch });
    paintSidebar();
    scrollToBottom();
    announce(`The question failed: ${error.message}`);
    // Any failure is worth a status re-check: a 503 means the index is missing,
    // a network error means the API itself is gone. The pill must say which.
    refreshHealth({ quiet: true });
  }
  els.input.focus();
}

/**
 * The cross-lingual badge is visual-only; screen readers get the same fact from
 * the live region, and the drawer keeps the raw `tiers_used` evidence.
 */
function announceCrossLingual(messageId) {
  const badge = els.chat.querySelector(`[data-msg-id="${CSS.escape(messageId)}"] .cross-badge`);
  if (badge) announce(badge.textContent.trim());
}

function startTicker(messageId) {
  stopTicker();
  ui.tickTimer = setInterval(() => {
    const timer = els.chat.querySelector(`[data-msg-id="${CSS.escape(messageId)}"] [data-role="timer"]`);
    if (timer) timer.textContent = `${((performance.now() - ui.startedAt) / 1000).toFixed(1)}s`;
  }, 100);
}

function stopTicker() {
  clearInterval(ui.tickTimer);
  ui.tickTimer = null;
}

// ---------------------------------------------------------------------------
// composer
// ---------------------------------------------------------------------------
function autosize() {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 180)}px`;
}

function updateCharCount() {
  const length = els.input.value.length;
  els.charCount.textContent = `${length} / ${MAX_QUESTION_CHARS}`;
  els.charCount.classList.toggle("is-near", length > MAX_QUESTION_CHARS * 0.85);
  els.send.disabled = ui.busy || !els.input.value.trim() || length > MAX_QUESTION_CHARS;
}

// ---------------------------------------------------------------------------
// settings dialog — every control maps onto a real `AskRequest` field
// ---------------------------------------------------------------------------
const UNSUPPORTED = [
  ["streaming", "Token-by-token streaming", "POST /ask returns one JSON body; no SSE or WebSocket route exists."],
  ["conversationHistory", "Server-side conversation history", "POST /ask is stateless; conversations live in this browser's localStorage."],
  ["conversationSearch", "Search across conversations on the server", "The filter in the sidebar runs over local data only."],
  ["feedback", "Send thumbs up/down to the backend", "No feedback endpoint exists; the vote is stored locally and labelled as such."],
  ["regenerateEndpoint", "A dedicated regenerate endpoint", "“Regenerate” re-issues the identical POST /ask — a real second request."],
];

async function openSettings() {
  els.settingsBody.innerHTML = settingsHTML();
  openOverlay(els.settingsOverlay, { labelledBy: "settings-title", onClose: () => loadProducts(true) });
  await loadProducts();
}

function settingsHTML() {
  const s = settings();
  const seg = (name, options, current) => `
    <div class="segmented" role="group" aria-label="${name}">
      ${options
        .map(
          ([value, label]) =>
            `<button type="button" data-setting="${name}" data-value="${value}" aria-pressed="${String(current === value)}">${label}</button>`
        )
        .join("")}
    </div>`;
  const toggle = (key, label, help, checked) => `
    <label class="switch">
      <input type="checkbox" data-toggle="${key}" ${checked ? "checked" : ""} />
      <span class="track" aria-hidden="true"></span>
      <span class="text"><b>${label}</b><span>${help}</span></span>
    </label>`;

  return `
    <div class="settings-grid">
      <section class="settings-section">
        <h3>Answer</h3>
        <div class="field">
          <div class="field-head"><label for="set-language">Answer language</label>
            <span class="value">${s.language ? s.language.toUpperCase() : "auto"}</span></div>
          <select class="select" id="set-language" data-setting-select="language">
            <option value="">Detect from my question (Bonus 02)</option>
            <option value="en" ${s.language === "en" ? "selected" : ""}>English</option>
            <option value="de" ${s.language === "de" ? "selected" : ""}>Deutsch</option>
            <option value="it" ${s.language === "it" ? "selected" : ""}>Italiano</option>
          </select>
          <p class="field-help">Sent as <code>AskRequest.language</code>. “Detect” leaves it unset so the
            pipeline answers in the language of the question.</p>
        </div>
        <div class="field">
          <div class="field-head"><label for="set-k">Evidence depth</label><span class="value" id="set-k-value">${s.k}</span></div>
          <input type="range" id="set-k" min="1" max="20" step="1" value="${s.k}" data-setting-range="k" />
          <p class="field-help">Sent as <code>AskRequest.k</code> (the API accepts 1–50). More passages means
            more citations and a slower answer.</p>
        </div>
        <div class="field">
          <div class="field-head"><label for="set-product">Restrict to one product</label></div>
          <select class="select" id="set-product" data-setting-select="product">
            <option value="">All 13 robots and accessories</option>
            ${(ui.productCache || [])
              .map((p) => `<option value="${esc(p)}" ${s.product === p ? "selected" : ""}>${esc(p)}</option>`)
              .join("")}
          </select>
          <p class="field-help" id="set-product-help">Sent as <code>AskRequest.product</code>. The list comes
            from <code>GET /products</code>.</p>
        </div>
        <div class="field">
          <div class="field-head"><label for="set-provider">Answer generator</label></div>
          <select class="select" id="set-provider" data-setting-select="provider">
            <option value="">Server default</option>
            <option value="gemini" ${s.provider === "gemini" ? "selected" : ""}>Gemini (needs a key on the server)</option>
            <option value="offline" ${s.provider === "offline" ? "selected" : ""}>Offline formatter (no key)</option>
          </select>
          <p class="field-help">Sent as <code>AskRequest.provider</code>. The API key stays on the server —
            it is never sent to the browser.</p>
        </div>
      </section>

      <section class="settings-section">
        <h3>Retrieval policy</h3>
        ${toggle("followUserLanguage", "Follow the question's language", "Off is the naive all-languages baseline (<code>follow_user_language=false</code>).", s.followUserLanguage !== false)}
        ${toggle("crossLingualFallback", "Allow cross-lingual fallback", "Answer in your language from manuals written in another one (<code>cross_lingual_fallback</code>).", s.crossLingualFallback !== false)}
      </section>

      <section class="settings-section">
        <h3>Interface</h3>
        <div class="field">
          <div class="field-head"><label>Colour theme</label></div>
          ${seg("theme", [["light", "Light"], ["dark", "Dark"], ["system", "System"]], s.theme)}
          <p class="field-help">Stored in this browser. “System” follows your OS setting and updates live.</p>
        </div>
        ${toggle("autoScroll", "Scroll to new answers", "Turn off to read a long answer while the next one loads.", s.autoScroll !== false)}
      </section>

      <section class="settings-section">
        <h3>Not supported by the API yet</h3>
        <ul class="unsupported">
          ${UNSUPPORTED.map(([key, label, why]) => `
            <li class="unsupported-item">
              <span class="unsupported-label">${label}</span>
              <span class="unsupported-why">${why}</span>
              <code class="unsupported-flag">${CAPABILITIES[key] ? "available" : "no endpoint"}</code>
            </li>`).join("")}
        </ul>
        <p class="field-help">The exact request each one needs is written up in
          <code>docs/ui-backend-gaps.md</code>. Nothing here is simulated in the UI.</p>
      </section>

      <section class="settings-section">
        <h3>Local data</h3>
        <div class="danger-row">
          <p class="field-help">Conversations, settings and feedback live in <code>localStorage</code> under
            <code>telmekon.ui.v1</code> — never on the server.</p>
          <button class="btn-danger" type="button" data-action="clear-local">Delete all local data</button>
        </div>
      </section>
    </div>`;
}

async function loadProducts(quiet = false) {
  if (ui.productCache) {
    if (!quiet) paintProductOptions();
    return ui.productCache;
  }
  const help = $("set-product-help");
  try {
    const payload = await products({ include_params: false, params_per_product: 0, language: "en" });
    const names = [...new Set((payload?.products || []).map((p) => p?.product).filter(Boolean))].sort();
    ui.productCache = names;
    if (!quiet) paintProductOptions();
  } catch (error) {
    ui.productCache = null;
    if (help) help.textContent = `Could not load the catalog (${serializeError(error).message}). The scope stays “all products”.`;
  }
  return ui.productCache;
}

function paintProductOptions() {
  const select = $("set-product");
  if (!select || !ui.productCache) return;
  const current = settings().product;
  select.innerHTML =
    `<option value="">All ${ui.productCache.length} products</option>` +
    ui.productCache.map((p) => `<option value="${esc(p)}" ${current === p ? "selected" : ""}>${esc(p)}</option>`).join("");
}

function applySetting(key, value) {
  store.set((state) => updateSettings(state, { [key]: value }));
  paintSidebar();
  updateScopeRow();
  if (key === "theme") {
    applyTheme();          // without this the switch would only change the setting
    syncThemeButtons();
  }
}

// ---------------------------------------------------------------------------
// theme
// ---------------------------------------------------------------------------
function syncThemeButtons() {
  const current = settings().theme;
  for (const button of document.querySelectorAll("[data-theme-set]")) {
    button.setAttribute("aria-pressed", String(button.dataset.themeSet === current));
  }
}

/** `applyTheme` writes `<html data-theme>` + `color-scheme`; keep it to re-run. */
const applyTheme = initTheme(
  () => settings().theme,
  () => {
    /* the resolved theme is on <html data-theme>; the CSS does the rest */
  }
);

// ---------------------------------------------------------------------------
// events
// ---------------------------------------------------------------------------
function bind() {
  // -- composer -------------------------------------------------------------
  els.form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitQuestion(els.input.value);
  });
  els.input.addEventListener("input", () => {
    autosize();
    updateCharCount();
  });
  els.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitQuestion(els.input.value);
    }
  });

  // -- sidebar --------------------------------------------------------------
  els.newChat.addEventListener("click", () => {
    store.set(newConversation);
    paintConversation();
    paintSidebar();
    els.input.value = "";
    autosize();
    updateCharCount();
    closeMobileSidebar();
    els.input.focus();
  });
  els.convSearch.addEventListener("input", () => {
    ui.filter = els.convSearch.value;
    paintSidebar();
  });
  els.openSidebar?.addEventListener("click", () => setSidebar(true));
  els.toggleSidebar?.addEventListener("click", () => setSidebar(els.app.dataset.sidebar !== "open"));
  els.sidebarBackdrop.addEventListener("click", () => setSidebar(false));

  for (const button of document.querySelectorAll("[data-theme-set]")) {
    button.addEventListener("click", () => applySetting("theme", button.dataset.themeSet));
  }

  // -- topbar ---------------------------------------------------------------
  els.status.addEventListener("click", () => refreshHealth());

  // -- welcome chips --------------------------------------------------------
  els.welcome.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-prompt]");
    if (!chip) return;
    submitQuestion(chip.dataset.prompt);
  });

  // -- a figure the server could not deliver --------------------------------
  // `image_url` is only non-null when api/media.py resolved the file, so this is
  // the rare case (file moved, disk error, proxy rewrite). Either way the user
  // gets a sentence, never a broken-image icon.
  for (const scope of [els.chat, els.citationOverlay, els.lightbox]) {
    scope.addEventListener(
      "error",
      (event) => {
        const img = event.target;
        if (img?.tagName !== "IMG") return;
        const src = img.getAttribute("src") || "";
        const button = img.closest(".figure-img-btn, .profile-thumb");
        if (button) {
          const note = document.createElement("p");
          note.className = "figure-missing";
          note.innerHTML = `${icon("alert", { size: 14 })}<span>Figure not available — <code>${esc(src)}</code> did not load.</span>`;
          button.replaceWith(note);
        } else {
          img.replaceWith(Object.assign(document.createElement("p"), {
            className: "figure-missing",
            textContent: `Image not available (${src || "no path in the response"}).`,
          }));
        }
        notify("A figure could not be loaded from the API.", "warn");
      },
      true                                        // image errors do not bubble
    );
  }

  // -- scroll affordance ----------------------------------------------------
  els.scroller.addEventListener("scroll", () => {
    const distance = els.scroller.scrollHeight - els.scroller.scrollTop - els.scroller.clientHeight;
    els.toBottom.hidden = distance < 160 || els.chat.hidden;
  });
  els.toBottom.addEventListener("click", () => scrollToBottom(true));

  // -- everything inside the message list (delegated) -----------------------
  els.chat.addEventListener("click", (event) => {
    const messageEl = event.target.closest("[data-msg-id]");
    const messageId = messageEl?.dataset.msgId;

    // One handler for both citation entry points: the `[n]` marker inside the
    // answer (`.cite`) and the source card under it — both carry `data-ref`.
    const ref = event.target.closest("[data-ref]");
    if (ref && messageId) {
      openCitation(messageId, ref.dataset.ref);
      return;
    }

    const figureButton = event.target.closest("[data-figure]");
    if (figureButton && messageId) {
      const found = findMessage(messageId);
      const figure = found?.message?.payload?.figures?.[Number(figureButton.dataset.figure)];
      openLightbox(figure?.image_url, {
        caption: figure?.caption || figure?.product || "",
        labels: figure?.labels || [],
        alt: figure?.caption || "",
      });
      return;
    }

    const zoom = event.target.closest("[data-lightbox]");
    if (zoom) {
      openLightbox(zoom.dataset.lightbox, { caption: zoom.dataset.caption || "" });
      return;
    }

    const verify = event.target.closest(".verify-badge");
    if (verify) {
      const panel = document.getElementById(verify.getAttribute("aria-controls"));
      const open = verify.getAttribute("aria-expanded") === "true";
      verify.setAttribute("aria-expanded", String(!open));
      if (panel) panel.hidden = open;
      return;
    }

    const action = event.target.closest("[data-action]");
    if (!action || !messageId) return;
    handleAction(action.dataset.action, messageId, action);
  });

  // -- sidebar conversation clicks (delegated) ------------------------------
  els.convList.addEventListener("click", (event) => {
    const remove = event.target.closest('[data-action="delete-conv"]');
    if (remove) {
      removeConversation(remove.dataset.convId);
      return;
    }
    const open = event.target.closest("[data-conv]");
    if (!open) return;
    store.set((state) => selectConversation(state, open.dataset.conv));
    paintConversation();
    paintSidebar();
    closeMobileSidebar();
  });

  // -- overlays -------------------------------------------------------------
  for (const overlay of [els.citationOverlay, els.lightbox, els.settingsOverlay]) {
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay || event.target.classList.contains("overlay-backdrop") || event.target.closest("[data-close]")) {
        closeOverlay(overlay);
      }
    });
  }
  els.drawerBackdrop.addEventListener("click", closeDrawer);
  els.drawer.addEventListener("click", (event) => {
    if (event.target.closest("[data-close]")) closeDrawer();
    const tab = event.target.closest("[data-tab]");
    if (tab) {
      ui.drawer.tab = tab.dataset.tab;
      paintDrawer();
    }
    if (event.target.closest("[data-action='copy-json']")) copyDrawerJson();
  });
  els.drawerRerun.addEventListener("click", rerunDrawer);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (ui.overlayStack.length) {
        if (ui.overlayStack[ui.overlayStack.length - 1].panel === els.drawer) closeDrawer();
        else closeTopOverlay();
        return;
      }
      if (isMobile() && els.app.dataset.sidebar === "open") setSidebar(false);
      return;
    }
    if (event.key === "Tab" && ui.overlayStack.length) {
      trapFocus(ui.overlayStack[ui.overlayStack.length - 1].panel, event);
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      els.input.focus();
      els.input.select();
    }
  });

  // -- settings -------------------------------------------------------------
  els.settingsBody.addEventListener("click", (event) => {
    const seg = event.target.closest("[data-setting]");
    if (seg) {
      applySetting(seg.dataset.setting, seg.dataset.value);
      for (const sibling of seg.parentElement.querySelectorAll("[data-setting]")) {
        sibling.setAttribute("aria-pressed", String(sibling === seg));
      }
      return;
    }
    if (event.target.closest('[data-action="clear-local"]')) clearLocalData();
  });
  els.settingsBody.addEventListener("change", (event) => {
    const select = event.target.closest("[data-setting-select]");
    if (select) {
      applySetting(select.dataset.settingSelect, select.value);
      // update the value chip only — re-rendering the dialog would drop focus
      const chip = select.closest(".field")?.querySelector(".value");
      if (chip) chip.textContent = select.value ? select.value.toUpperCase() : "auto";
      return;
    }
    const toggle = event.target.closest("[data-toggle]");
    if (toggle) applySetting(toggle.dataset.toggle, toggle.checked);
  });
  els.settingsBody.addEventListener("input", (event) => {
    const range = event.target.closest("[data-setting-range]");
    if (!range) return;
    $("set-k-value").textContent = range.value;
    applySetting(range.dataset.settingRange, Number(range.value));
  });

  for (const id of ["open-settings", "open-settings-top"]) {
    $(id)?.addEventListener("click", openSettings);
  }

  // -- scope chips ----------------------------------------------------------
  els.scopeRow.addEventListener("click", (event) => {
    const button = event.target.closest('[data-action="clear-scope"]');
    if (!button) return;
    const key = button.dataset.key;
    const reset = { product: "", language: "", provider: "" };
    if (key in reset) applySetting(key, reset[key]);
    else if (key === "crossLingualFallback") applySetting(key, true);
    else if (key === "followUserLanguage") applySetting(key, true);
  });

  // -- health refresh -------------------------------------------------------
  window.addEventListener("focus", () => {
    if (!document.hidden) refreshHealth({ quiet: true });
  });
  ui.healthTimer = setInterval(() => refreshHealth({ quiet: true }), HEALTH_REFRESH_MS);
}

async function handleAction(action, messageId, button) {
  const found = findMessage(messageId);
  if (!found) return;
  const message = found.message;

  switch (action) {
    case "copy": {
      const text = plainText(message.payload?.answer || "");
      if (!text) {
        notify("There is no answer text to copy.", "warn");
        return;
      }
      const ok = await copyText(text);
      notify(ok ? `Answer copied (${text.length} characters).` : "Copying is blocked in this browser — select the text instead.", ok ? "info" : "error");
      return;
    }
    case "regenerate": {
      if (ui.busy) {
        notify("Wait for the current question to finish.", "warn");
        return;
      }
      button.disabled = true;
      // A real second POST /ask — the API has no regenerate endpoint, and the
      // button's tooltip says exactly that.
      await submitQuestion(message.question, { regenerateOf: messageId });
      return;
    }
    case "feedback": {
      const value = message.feedback === button.dataset.value ? null : button.dataset.value;
      store.set((state) => setFeedback(state, found.conversation.id, messageId, value));
      paintMessage({ ...message, feedback: value });
      if (value) {
        notify(
          value === "up"
            ? "Thanks — saved in this browser only (the API has no feedback endpoint)."
            : "Noted — saved in this browser only (the API has no feedback endpoint).",
          "info"
        );
      }
      return;
    }
    case "raw":
      openDrawer(messageId, "search");
      return;
    case "retry":
      // Same question, same message slot: the failed card is replaced, not stacked.
      await submitQuestion(button.dataset.question || message.question, { regenerateOf: messageId });
      return;
    default:
      return;
  }
}

async function copyText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

async function copyDrawerJson() {
  const text = els.drawerJson.textContent || "";
  const ok = await copyText(text);
  notify(ok ? "JSON copied to the clipboard." : "Copying is blocked in this browser.", ok ? "info" : "error");
}

function removeConversation(id) {
  const state = store.state;
  const target = state.conversations.find((c) => c.id === id);
  if (!target) return;
  ui.deletedSnapshot = state;
  store.set((next) => deleteConversation(next, id));
  paintConversation();
  paintSidebar();
  notify(`Deleted “${titleFrom(target.title, 32)}” from this browser.`, "info", {
    label: "Undo",
    onClick: () => {
      if (!ui.deletedSnapshot) return;
      store.set(() => ui.deletedSnapshot);
      ui.deletedSnapshot = null;
      paintConversation();
      paintSidebar();
      notify("Conversation restored.");
    },
  });
}

function clearLocalData() {
  store.reset();
  ui.productCache = null;
  closeOverlay(els.settingsOverlay);
  paintConversation();
  paintSidebar();
  updateScopeRow();
  notify("All locally saved conversations and settings were deleted.", "info");
}

// ---------------------------------------------------------------------------
// responsive sidebar
// ---------------------------------------------------------------------------
const isMobile = () => window.matchMedia(MOBILE_QUERY).matches;

function setSidebar(open) {
  els.app.dataset.sidebar = open ? "open" : "collapsed";
  els.sidebarBackdrop.hidden = !(open && isMobile());
  els.openSidebar?.setAttribute("aria-expanded", String(open));
  if (open && isMobile()) els.sidebar.focus?.();
}

function closeMobileSidebar() {
  if (isMobile()) setSidebar(false);
}

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------
/**
 * What this assistant really does, on the welcome screen. Built from
 * `CAPABILITIES` in api.js so the list cannot drift from what the API offers —
 * an absent feature is stated, never imitated.
 */
function paintCapabilities() {
  const pills = [
    ["check", "Grounded answers"],
    ["quote", "Every claim cited"],
    ["image", "Manual figures + Labels"],
    ["code", "Raw /search drawer"],
    ["globe", "DE · EN · IT"],
  ];
  if (!CAPABILITIES.streaming) pills.push(["info", "No streaming — one JSON response"]);
  if (!CAPABILITIES.conversationHistory) pills.push(["clock", "History kept in this browser"]);
  els.capabilities.innerHTML = pills
    .map(([name, label]) => `<span class="cap-pill">${icon(name, { size: 13 })}${esc(label)}</span>`)
    .join("");
}
/**
 * Icons are inline SVG injected from `icons.js`, so the served HTML contains no
 * external URLs at all (no icon font, no CDN, no sprite sheet to fetch).
 */
function paintIcons() {
  for (const host of document.querySelectorAll("[data-icon]")) {
    host.innerHTML = icon(host.dataset.icon, {
      size: Number(host.dataset.size || 16),
      label: host.dataset.label || "",
    });
  }
  const brand = $("brand-mark");
  if (brand) brand.outerHTML = brandMark(19).replace('class="brand-mark"', 'class="brand-mark" id="brand-mark"');
}

function boot() {
  paintIcons();

  paintCapabilities();
  setSidebar(!isMobile());
  syncThemeButtons();
  paintConversation();
  paintSidebar();
  updateCharCount();
  autosize();
  bind();
  refreshHealth({ quiet: true });

  window.matchMedia(MOBILE_QUERY).addEventListener("change", () => setSidebar(!isMobile()));

  // A restored conversation re-renders what the API returned before; its raw
  // /search pane stays empty until the user re-runs it (nothing is invented).
  const restored = conversation();
  if (restored?.messages?.length) {
    notify(`Restored ${restored.messages.length} message(s) from this browser.`, "info");
  }
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();

/* Exported for the browser console and for `ui/tests/app.contract.test.js`. */
export const __internals = { submitQuestion, openCitation, openLightbox, serializeError, messageHTML, validate };
