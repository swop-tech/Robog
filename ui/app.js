/* Robog web chat — zero-build vanilla ES module.
 *
 * The chat is only the first client of the retrieval service (Bonus 01):
 *   POST /ask     -> the grounded, validated answer (citations, figures, params, verification)
 *   GET  /search  -> the raw retrieval JSON, shown live in the drawer
 *   GET  /health  -> index + provider status
 *   GET  /media/… -> the figures (URLs are normalised to same-origin paths below)
 *
 * Everything the answer claims is already checked server-side (G5); this file
 * renders it and makes each claim one click away from its source.
 */

// --------------------------------------------------------------------------
// tiny helpers
// --------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);

/** Escape for HTML text *and* attribute contexts. */
function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Escape only & < > — for the JSON <pre>, where quotes must stay literal. */
function escText(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** The API returns absolute image_urls (http://host/media/…). Behind a preview
 *  proxy the browser must use its own origin, so keep only the path. */
function mediaUrl(url) {
  if (!url) return null;
  try {
    const u = new URL(url, location.origin);
    return u.pathname + u.search;
  } catch {
    return url;
  }
}

const LANG_NAME = { en: "English", de: "German", it: "Italian" };
const langName = (code) => LANG_NAME[code] || (code || "").toUpperCase() || "—";
const ROUTE_LABEL = { lookup: "Exact lookup", advisory: "Advisory", figure: "Figure", default: "General" };
const routeLabel = (route) => ROUTE_LABEL[route] || route || "General";
const KIND_LABEL = { param: "Parameter row", profile: "Product profile", chunk: "Manual passage" };
const kindLabel = (kind) => KIND_LABEL[kind] || kind || "Source";

// --------------------------------------------------------------------------
// API
// --------------------------------------------------------------------------
const searchUrlFor = (question) => `/search?q=${encodeURIComponent(question)}&k=8`;

async function apiAsk(question) {
  const res = await fetch("/ask", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) {
    let body = null;
    try { body = await res.json(); } catch { /* non-JSON error */ }
    const err = new Error(body?.detail || `HTTP ${res.status}`);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return res.json();
}

async function apiSearch(question) {
  const res = await fetch(searchUrlFor(question));
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// --------------------------------------------------------------------------
// markdown-lite + JSON highlighting
// --------------------------------------------------------------------------
/** Render the formatter's markdown subset: **bold**, "- " bullets, paragraphs,
 *  and [n] / [n, m] citation markers as clickable buttons. Input is escaped
 *  first, so no source text can inject markup. */
function renderAnswer(text) {
  const lines = esc(text).split("\n");
  let html = "";
  let inList = false;
  const closeList = () => { if (inList) { html += "</ul>"; inList = false; } };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (!line.trim()) { closeList(); continue; }
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${inline(bullet[1])}</li>`;
    } else {
      closeList();
      html += `<p>${inline(line.trim())}</p>`;
    }
  }
  closeList();
  return html;
}

function inline(s) {
  return s
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[(\d+(?:\s*[,;]\s*\d+)*)\]/g, (_, nums) =>
      nums
        .split(/[,;]/)
        .map((n) => n.trim())
        .filter(Boolean)
        .map((n) => `<button class="cite" type="button" data-ref="${n}" aria-label="Open source ${n}">[${n}]</button>`)
        .join("")
    );
}

/** Syntax-highlight a JSON value for the drawer (escaped, then token-wrapped). */
function highlightJson(value) {
  const json = escText(JSON.stringify(value, null, 2));
  return json.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
    (match) => {
      let cls = "j-num";
      if (/^"/.test(match)) cls = /:$/.test(match) ? "j-key" : "j-str";
      else if (/^(true|false)$/.test(match)) cls = "j-bool";
      else if (/^null$/.test(match)) cls = "j-null";
      return `<span class="${cls}">${match}</span>`;
    }
  );
}

// --------------------------------------------------------------------------
// state
// --------------------------------------------------------------------------
const messages = new Map(); // id -> { role, question, payload, search, searchUrl, searchError }
let nextId = 1;
let busy = false;
let drawerMsgId = null;
let lastFocus = null;

const messagesEl = $("messages");
const welcomeEl = $("welcome");
const chatEl = $("chat");
const inputEl = $("input");
const sendEl = $("send");
const newChatBtn = $("new-chat");
const drawerEl = $("drawer");
const drawerBackdrop = $("drawer-backdrop");
const citationOverlay = $("citation-overlay");
const lightboxEl = $("lightbox");

// --------------------------------------------------------------------------
// rendering: a bot answer
// --------------------------------------------------------------------------
function botCardHTML(p) {
  const provider = p.provider === "offline"
    ? "offline formatter"
    : esc(p.provider + (p.model ? ` · ${p.model}` : ""));
  const meta = `
    <div class="msg-meta">
      <span class="meta-badge meta-badge--route">${esc(routeLabel(p.route))}</span>
      <span class="meta-badge meta-badge--lang">${esc(langName(p.language))}</span>
      <span class="meta-badge meta-badge--provider">${provider}</span>
      <span class="meta-latency">${esc(p.latency_ms ?? 0)} ms</span>
    </div>`;

  const specs = renderSpecs(p.params);
  const figures = renderFigures(p.figures);
  const cross = p.cross_lingual
    ? `<div class="cross-badge">🌐 Answer in ${esc(langName(p.language))} · sources ${esc((p.source_languages || []).map((l) => l.toUpperCase()).join(", ") || "other languages")}</div>`
    : "";

  return `
    <div class="bot-card">
      ${meta}
      <div class="answer">${renderAnswer(p.answer || "")}</div>
      ${specs}
      ${figures}
      ${cross}
      ${renderVerification(p.verification)}
      ${renderSources(p.citations)}
      <div class="msg-actions">
        <button class="action-btn" type="button" data-action="raw">▤ Raw <code>/search</code> JSON</button>
        <button class="action-btn" type="button" data-action="copy">⧉ Copy answer</button>
      </div>
    </div>`;
}

function renderSpecs(params) {
  if (!params || !params.length) return "";
  return params.map((p) => {
    const sec = p.section_number || p.section || "";
    const locator = [
      p.product ? esc(p.product) : "",
      sec ? `§${esc(sec)}` : "",
      p.page != null ? `p. ${esc(p.page)}` : "",
      p.language ? esc(p.language.toUpperCase()) : "",
    ].filter(Boolean).join(" · ");
    return `
      <div class="spec-card">
        <div class="spec-name">${esc(p.parameter || "Value")}</div>
        <div class="spec-value">${esc(p.value || "")}</div>
        <div class="spec-source">${locator}</div>
      </div>`;
  }).join("");
}

function renderFigures(figures) {
  if (!figures || !figures.length) return "";
  const cards = figures.map((f, i) => {
    const url = mediaUrl(f.image_url);
    const img = url
      ? `<div class="figure-img-wrap"><img class="figure-img" src="${esc(url)}" alt="${esc(f.product || "Figure")}" loading="lazy" data-figure="${i}" /></div>`
      : "";
    const labels = (f.labels || []).map((l) => {
      const m = String(l).match(/^(\d+)\s+(.*)$/);
      return m
        ? `<li class="label-chip"><b>${esc(m[1])}</b>${esc(m[2])}</li>`
        : `<li class="label-chip">${esc(l)}</li>`;
    }).join("");
    const legend = labels
      ? `<p class="labels-title">Labels</p><ul class="figure-labels">${labels}</ul>`
      : "";
    return `
      <div class="figure-card">
        ${img}
        <div class="figure-body">
          <p class="figure-caption">${esc(f.caption || "")}</p>
          ${legend}
        </div>
      </div>`;
  }).join("");
  return `<div class="figures">${cards}</div>`;
}

function renderVerification(v) {
  if (!v) return "";
  const state = v.verified ? "good" : v.grounded ? "warn" : "bad";
  const icon = v.verified ? "✓" : v.grounded ? "!" : "✕";
  const label = v.verified ? "Verified" : v.grounded ? "Grounded" : "Check failed";
  const counts = `${v.sentences_cited ?? 0}/${v.sentences_checked ?? 0} cited · ${v.numbers_verified ?? 0}/${v.numbers_checked ?? 0} numbers`;
  return `
    <div class="verify">
      <button class="verify-badge verify-badge--${state}" type="button" data-action="verify" aria-expanded="false">
        <span class="v-icon">${icon}</span><span>${label}</span>
        <span class="v-counts">· ${esc(counts)}</span><span class="v-caret">▾</span>
      </button>
      <div class="verify-details" hidden>${verifyDetailsHTML(v)}</div>
    </div>`;
}

function verifyDetailsHTML(v) {
  const rate = (name, verified, checked, value) => {
    const pct = Math.round((value ?? (checked ? verified / checked : 1)) * 100);
    const cls = pct === 100 ? "" : pct >= 50 ? "rate-fill--warn" : "rate-fill--bad";
    return `
      <div class="rate">
        <div class="rate-label"><span>${name}</span><b>${verified}/${checked}</b></div>
        <div class="rate-bar"><div class="rate-fill ${cls}" style="width:${pct}%"></div></div>
      </div>`;
  };
  const issues = (v.issues || []).length
    ? `<ul class="issues">${v.issues.map((it) =>
        `<li class="issue ${it.severity === "error" ? "issue--error" : ""}"><b>${esc(it.check)} · ${esc(it.action || it.severity)}</b> — ${esc(it.message)}</li>`
      ).join("")}</ul>`
    : `<p class="verify-summary" style="margin:12px 0 0;color:var(--good)">No issues — every claim is cited and every number matched the source.</p>`;
  return `
    <p class="verify-summary">${esc(v.summary || "")}</p>
    <div class="verify-grid">
      ${rate("Citations", v.sentences_cited ?? 0, v.sentences_checked ?? 0, v.citation_rate)}
      ${rate("Numbers", v.numbers_verified ?? 0, v.numbers_checked ?? 0, v.numeric_rate)}
      ${rate("Quotes", v.quotes_verified ?? 0, v.quotes_checked ?? 0, v.quote_rate)}
    </div>
    ${issues}`;
}

function renderSources(citations) {
  if (!citations || !citations.length) return "";
  const items = citations.map((c) =>
    `<button class="source-chip" type="button" data-ref="${esc(c.n)}"><span class="s-n">[${esc(c.n)}]</span><span class="s-cit">${esc(c.citation || "")}</span></button>`
  ).join("");
  return `
    <div class="sources">
      <p class="sources-title">Sources (${citations.length})</p>
      <div class="source-list">${items}</div>
    </div>`;
}

function errorHTML(err, question) {
  const is503 = err.status === 503;
  const title = is503 ? "The search index isn’t built yet" : "Couldn’t reach the answer engine";
  const msg = is503
    ? esc(err.body?.reason || "The retrieval index is unavailable.")
    : esc(err.message || "Network error — is the API running?");
  const fix = err.body?.fix
    ? `<p>Build it once, then retry:</p><code>${esc(err.body.fix)}</code>`
    : "";
  return `
    <div class="error-card">
      <h3>${title}</h3>
      <p>${msg}</p>
      ${fix}
      <button class="action-btn" type="button" data-action="retry" data-question="${esc(question)}">↻ Retry</button>
    </div>`;
}

// --------------------------------------------------------------------------
// rendering: messages into the DOM
// --------------------------------------------------------------------------
function showChat() {
  welcomeEl.hidden = true;
  chatEl.hidden = false;
  newChatBtn.hidden = false;
}

function addUserMessage(question) {
  const id = String(nextId++);
  messages.set(id, { role: "user", question });
  const el = document.createElement("div");
  el.className = "msg msg--user";
  el.dataset.msgId = id;
  el.innerHTML = `<div class="msg-avatar" aria-hidden="true">🧑</div><div class="msg-main"><div class="bubble">${esc(question)}</div></div>`;
  messagesEl.appendChild(el);
  return id;
}

function addBotPlaceholder() {
  const id = String(nextId++);
  messages.set(id, { role: "bot" });
  const el = document.createElement("div");
  el.className = "msg msg--bot";
  el.dataset.msgId = id;
  el.innerHTML = `
    <div class="msg-avatar" aria-hidden="true">🤖</div>
    <div class="msg-main"><div class="bot-card">
      <div class="thinking"><span class="dots"><span></span><span></span><span></span></span>
      <span>Retrieving evidence and verifying claims…</span></div>
    </div></div>`;
  messagesEl.appendChild(el);
  return { id, el };
}

function scrollToLatest() {
  requestAnimationFrame(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" }));
}

// --------------------------------------------------------------------------
// the ask flow
// --------------------------------------------------------------------------
async function sendQuestion(raw) {
  const question = (raw || "").trim();
  if (!question || busy) return;
  busy = true;
  updateSend();
  showChat();
  addUserMessage(question);
  const { id, el } = addBotPlaceholder();
  scrollToLatest();

  const searchUrl = searchUrlFor(question);
  // /ask and /search run in parallel: the answer renders fast and the drawer
  // is instant, while /search remains the honest, standalone Bonus 01 call.
  const [askRes, searchRes] = await Promise.allSettled([apiAsk(question), apiSearch(question)]);
  const main = el.querySelector(".msg-main");

  if (askRes.status === "fulfilled") {
    const payload = askRes.value;
    messages.set(id, {
      role: "bot",
      question,
      payload,
      search: searchRes.status === "fulfilled" ? searchRes.value : null,
      searchUrl,
      searchError: searchRes.status === "rejected" ? String(searchRes.reason?.message || searchRes.reason) : null,
    });
    main.innerHTML = botCardHTML(payload);
  } else {
    messages.set(id, { role: "bot", question, searchUrl });
    main.innerHTML = errorHTML(askRes.reason, question);
  }
  busy = false;
  updateSend();
  scrollToLatest();
}

// --------------------------------------------------------------------------
// citation modal
// --------------------------------------------------------------------------
function openCitation(msgId, ref) {
  const m = messages.get(msgId);
  const c = m?.payload?.citations?.find((x) => String(x.n) === String(ref));
  const content = $("citation-content");
  if (!c) {
    content.innerHTML = `<div class="cit-head"><span class="cit-n">${esc(ref)}</span><h3 class="cit-title" id="citation-title">Source</h3></div><p>No detail available for this reference.</p>`;
  } else {
    const url = mediaUrl(c.image_url);
    const badges = [
      `<span class="cit-badge cit-badge--kind">${esc(kindLabel(c.kind))}</span>`,
      c.language ? `<span class="cit-badge cit-badge--lang">${esc(c.language.toUpperCase())}</span>` : "",
      c.product ? `<span class="cit-badge">${esc(c.product)}</span>` : "",
    ].join("");
    const loc = (k, v) => `<div><span class="k">${k}</span><span class="v">${esc(v ?? "—")}</span></div>`;
    const locator = [
      loc("Manual", c.title),
      loc("Section", c.section ? `§${c.section}` : "—"),
      loc("PDF page", c.page != null ? `p. ${c.page}` : "—"),
    ].join("");
    const snippet = c.excerpt
      ? `<p class="cit-snippet-label">Source snippet</p><blockquote class="cit-snippet">${esc(c.excerpt)}</blockquote>`
      : "";
    const image = url
      ? `<div class="cit-image"><img src="${esc(url)}" alt="${esc(c.product || "figure")}" data-lightbox="${esc(url)}" data-caption="${esc(c.citation || "")}" /></div>`
      : "";
    content.innerHTML = `
      <div class="cit-head"><span class="cit-n">${esc(c.n)}</span><h3 class="cit-title" id="citation-title">${esc(c.title || "Source")}</h3></div>
      <div class="cit-badges">${badges}</div>
      <div class="cit-locator">${locator}</div>
      ${snippet}
      ${image}
      <p class="cit-full">${esc(c.citation || "")}</p>`;
  }
  showOverlay("citation");
}

// --------------------------------------------------------------------------
// raw /search drawer (Bonus 01, live)
// --------------------------------------------------------------------------
function openDrawer(msgId) {
  drawerMsgId = msgId;
  const m = messages.get(msgId);
  $("drawer-url").textContent = location.origin + (m?.searchUrl || "");
  renderDrawer(m);
  drawerEl.hidden = false;
  drawerBackdrop.hidden = false;
  focusFirst(drawerEl);
}

function renderDrawer(m) {
  const meta = $("drawer-meta");
  const pre = $("drawer-json");
  if (m?.search) {
    const s = m.search;
    const items = [
      ["route", s.route], ["language", (s.language || "").toUpperCase()],
      ["confidence", s.confidence], ["hits", (s.hits || []).length],
      ["params", (s.params || []).length], ["profiles", (s.profiles || []).length],
    ];
    if (s.cross_lingual) items.push(["cross-lingual", "yes"]);
    meta.innerHTML = items.map(([k, v]) => `<span class="dm-item">${esc(k)} <b>${esc(v ?? "—")}</b></span>`).join("");
    pre.innerHTML = highlightJson(s);
  } else if (m?.searchError) {
    meta.innerHTML = "";
    pre.innerHTML = `<span class="j-null">// /search failed: ${escText(m.searchError)}\n// Press “Re-run live” to try again.</span>`;
  } else {
    meta.innerHTML = "";
    pre.innerHTML = `<span class="j-null">// loading…</span>`;
  }
}

async function rerunDrawer() {
  const m = messages.get(drawerMsgId);
  if (!m) return;
  const btn = $("drawer-rerun");
  btn.disabled = true;
  $("drawer-json").innerHTML = `<span class="j-null">// calling GET /search live…</span>`;
  try {
    m.search = await apiSearch(m.question);
    m.searchError = null;
  } catch (err) {
    m.searchError = String(err.message || err);
  } finally {
    btn.disabled = false;
    renderDrawer(m);
  }
}

// --------------------------------------------------------------------------
// lightbox
// --------------------------------------------------------------------------
function openLightbox(url, caption) {
  if (!url) return;
  $("lightbox-img").src = url;
  $("lightbox-img").alt = caption || "Figure";
  $("lightbox-caption").textContent = caption || "";
  showOverlay("lightbox");
}

// --------------------------------------------------------------------------
// overlay plumbing
// --------------------------------------------------------------------------
function showOverlay(name) {
  lastFocus = document.activeElement;
  const el = name === "citation" ? citationOverlay : name === "lightbox" ? lightboxEl : drawerEl;
  el.hidden = false;
  if (name === "drawer") drawerBackdrop.hidden = false;
  focusFirst(el);
}

function closeOverlay(name) {
  if (name === "citation") citationOverlay.hidden = true;
  else if (name === "lightbox") lightboxEl.hidden = true;
  else if (name === "drawer") { drawerEl.hidden = true; drawerBackdrop.hidden = true; }
  if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
}

function focusFirst(el) {
  const target = el.querySelector(".modal-x, button, [tabindex]");
  if (target) requestAnimationFrame(() => target.focus());
}

// --------------------------------------------------------------------------
// status + misc UI
// --------------------------------------------------------------------------
async function checkHealth() {
  const status = $("status");
  const label = status.querySelector(".status-label");
  try {
    const res = await fetch("/health");
    const data = await res.json();
    const provider = data.provider?.provider === "gemini" ? "Gemini" : "offline";
    if (data.ready) {
      status.className = "status status--ready";
      label.textContent = `ready · ${provider}`;
      status.title = `${data.chunks} chunks · ${data.params} params · ${data.products} products · ${data.media_files} figures · provider: ${provider}`;
    } else {
      status.className = "status status--down";
      label.textContent = "index not built";
      status.title = data.detail || "Run: python -m ingest.run --data data";
    }
  } catch {
    status.className = "status status--down";
    label.textContent = "offline";
    status.title = "Cannot reach /health — is the API running?";
  }
}

let toastTimer = null;
function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 1800);
}

async function copyText(text, okMsg) {
  try {
    await navigator.clipboard.writeText(text);
    toast(okMsg);
  } catch {
    toast("Copy failed — select the text manually.");
  }
}

function updateSend() {
  sendEl.disabled = busy || !inputEl.value.trim();
}

function autoGrow() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 180) + "px";
}

function resetChat() {
  messages.clear();
  messagesEl.innerHTML = "";
  chatEl.hidden = true;
  welcomeEl.hidden = false;
  newChatBtn.hidden = true;
  inputEl.focus();
}

// --------------------------------------------------------------------------
// events
// --------------------------------------------------------------------------
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = inputEl.value;
  inputEl.value = "";
  autoGrow();
  updateSend();
  sendQuestion(q);
});

inputEl.addEventListener("input", () => { autoGrow(); updateSend(); });
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("composer").requestSubmit();
  }
});

$("chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) sendQuestion(chip.dataset.question);
});

newChatBtn.addEventListener("click", resetChat);

// delegated actions inside the conversation
messagesEl.addEventListener("click", (e) => {
  const msgEl = e.target.closest("[data-msg-id]");
  if (!msgEl) return;
  const id = msgEl.dataset.msgId;

  const cite = e.target.closest(".cite, .source-chip");
  if (cite) { openCitation(id, cite.dataset.ref); return; }

  const fig = e.target.closest(".figure-img");
  if (fig) {
    const f = messages.get(id)?.payload?.figures?.[Number(fig.dataset.figure)];
    if (f) openLightbox(mediaUrl(f.image_url), f.caption);
    return;
  }

  const action = e.target.closest("[data-action]");
  if (!action) return;
  const act = action.dataset.action;
  if (act === "verify") {
    const details = action.parentElement.querySelector(".verify-details");
    const willOpen = details.hidden;
    details.hidden = !willOpen;
    action.setAttribute("aria-expanded", String(willOpen));
  } else if (act === "raw") {
    openDrawer(id);
  } else if (act === "copy") {
    const p = messages.get(id)?.payload;
    if (p) copyText(p.answer || "", "Answer copied");
  } else if (act === "retry") {
    sendQuestion(action.dataset.question);
  }
});

// global closers + citation-image lightbox
document.addEventListener("click", (e) => {
  const closer = e.target.closest("[data-close]");
  if (closer) { closeOverlay(closer.dataset.close); return; }
  const lb = e.target.closest("[data-lightbox]");
  if (lb) openLightbox(lb.dataset.lightbox, lb.dataset.caption || "");
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!lightboxEl.hidden) closeOverlay("lightbox");
  else if (!drawerEl.hidden) closeOverlay("drawer");
  else if (!citationOverlay.hidden) closeOverlay("citation");
});

$("drawer-rerun").addEventListener("click", rerunDrawer);
$("drawer-copy").addEventListener("click", () => {
  const m = messages.get(drawerMsgId);
  if (m?.search) copyText(JSON.stringify(m.search, null, 2), "JSON copied");
});

// --------------------------------------------------------------------------
// init
// --------------------------------------------------------------------------
checkHealth();
updateSend();
inputEl.focus();
