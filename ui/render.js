/* Rendering — pure functions from API payloads to HTML strings.
 *
 * Everything here returns markup; nothing here touches the DOM, so the whole
 * presentation layer is unit-testable under `node --test ui/tests/` and there is
 * exactly one place where a payload shape is interpreted.
 *
 * Rules this file lives by:
 *  - every value from the API is escaped (`esc`) before it reaches the DOM —
 *    manual text is untrusted input, even though it is our own corpus;
 *  - a field that is missing renders as nothing, never as a placeholder that
 *    could be mistaken for data ("—" is used only inside a labelled key/value
 *    grid, where the label makes the absence explicit);
 *  - `image_url` is normalised to a same-origin path by `mediaUrl()` so figures
 *    keep working behind a preview proxy.
 */

import { esc, renderMarkdown, highlightJson } from "./markdown.js";

export const LANG_NAME = { en: "English", de: "Deutsch", it: "Italiano" };
export const langName = (code) => LANG_NAME[code] || (code ? String(code).toUpperCase() : "");

export const ROUTE_LABEL = {
  lookup: "Exact lookup",
  advisory: "Comparison",
  figure: "Diagram",
  default: "General",
};
export const routeLabel = (route) => ROUTE_LABEL[route] || route || "General";

export const KIND_LABEL = { param: "Parameter row", profile: "Product profile", chunk: "Manual passage" };
export const kindLabel = (kind) => KIND_LABEL[kind] || kind || "Source";

const PROVIDER_LABEL = { offline: "offline formatter", gemini: "Gemini" };

/** `mediaUrl` is injected so this module stays free of browser globals. */
const sameOrigin = (url, mediaUrl) => (mediaUrl ? mediaUrl(url) : url);

// ---------------------------------------------------------------------------
// messages
// ---------------------------------------------------------------------------
export function userMessageHTML(message) {
  const time = message.createdAt ? `<time class="msg-time" datetime="${esc(message.createdAt)}">${esc(shortTime(message.createdAt))}</time>` : "";
  return `
    <div class="msg msg--user" data-msg-id="${esc(message.id)}">
      <div class="msg-main">
        <div class="bubble bubble--user">${esc(message.text || message.question || "")}</div>
        ${time}
      </div>
      <div class="msg-avatar msg-avatar--user" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7"
             stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="8.4" r="3.6"/><path d="M4.8 20a7.2 7.2 0 0 1 14.4 0"/>
        </svg>
      </div>
    </div>`;
}

export function thinkingHTML(label = "Retrieving evidence and verifying claims") {
  return `
    <div class="thinking" role="status" aria-live="polite">
      <span class="dots" aria-hidden="true"><span></span><span></span><span></span></span>
      <span class="thinking-label">${esc(label)}…</span>
      <span class="thinking-timer" data-role="timer">0.0s</span>
    </div>
    <div class="skeleton" aria-hidden="true"><span></span><span></span><span></span></div>`;
}

export function assistantShellHTML(messageId, inner) {
  return `
    <div class="msg msg--bot" data-msg-id="${esc(messageId)}">
      <div class="msg-avatar msg-avatar--bot" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7"
             stroke-linecap="round" stroke-linejoin="round">
          <rect x="3.6" y="6.6" width="16.8" height="12" rx="3.2"/><path d="M12 3.4v3.2"/>
          <circle cx="9.2" cy="12.4" r="1.3" fill="currentColor" stroke="none"/>
          <circle cx="14.8" cy="12.4" r="1.3" fill="currentColor" stroke="none"/>
          <path d="M9.8 15.8h4.4"/>
        </svg>
      </div>
      <div class="msg-main">${inner}</div>
    </div>`;
}

/**
 * The assistant card: metadata, answer, exact values, figures, the verification
 * verdict, sources and the actions that operate on this message.
 */
export function assistantCardHTML(message, { mediaUrl, icons } = {}) {
  const p = message.payload || {};
  const provider = p.provider === "offline"
    ? PROVIDER_LABEL.offline
    : `${PROVIDER_LABEL[p.provider] || esc(p.provider || "")}${p.model ? ` · ${esc(p.model)}` : ""}`;

  const parts = [
    `<div class="msg-meta">
      <span class="meta-badge meta-badge--route" title="Retrieval route">${esc(routeLabel(p.route))}</span>
      ${p.language ? `<span class="meta-badge meta-badge--lang" title="Answer language">${esc(langName(p.language))}</span>` : ""}
      <span class="meta-badge meta-badge--provider" title="Answer generator">${provider}</span>
      ${p.latency_ms != null ? `<span class="meta-latency">${esc(p.latency_ms)} ms</span>` : ""}
    </div>`,
    fallbackNoteHTML(p),
    `<div class="answer md">${renderMarkdown(p.answer || "")}</div>`,
    renderProfiles(p.profiles, { mediaUrl }),
    renderSpecs(p.params),
    renderFigures(p.figures, { mediaUrl }),
    p.cross_lingual ? crossLingualBadge(p) : "",
    renderVerification(p.verification, { id: `verify-${message.id}` }),
    renderSources(p.citations, { icons }),
    renderActions(message, { icons }),
    `<div class="msg-foot"><time class="msg-time" datetime="${esc(message.createdAt || "")}">${esc(shortTime(message.createdAt))}</time></div>`,
  ];
  return `<article class="bot-card">${parts.filter(Boolean).join("\n")}</article>`;
}

/**
 * Why this answer is quoted passages rather than prose. The offline formatter
 * cannot explain, only excerpt, so when it stands in for the model the card
 * says so and gives the server's reason (`fallback_reason`): no key, a failed
 * Gemini call with its error, or a generated answer that failed the number
 * check. Deterministic replies that never involve the model carry no reason.
 */
export function fallbackNoteHTML(p) {
  if (!p || p.provider !== "offline" || !p.fallback_reason) return "";
  return `
    <p class="fallback-note" role="note">
      <b>Quoted from the manuals, not written by the model.</b>
      ${esc(`Reason: ${p.fallback_reason}.`)}
    </p>`;
}

/** “Answer in Italian · sources DE, EN” — Bonus 02, disclosed on the card. */
export function crossLingualBadge(p) {
  const sources = (p.source_languages || []).map((l) => String(l).toUpperCase()).join(", ");
  return `
    <div class="cross-badge" title="The question was answered in your language from manuals written in another one">
      ${esc(`Answer in ${langName(p.language)} · sources ${sources || "other languages"}`)}
    </div>`;
}

// ---------------------------------------------------------------------------
// evidence blocks
// ---------------------------------------------------------------------------
/** `GET /ask` → `profiles[]`: the 13-robot catalog cards the advisory route matched. */
const PROFILE_ROWS = [
  ["Payload", "payload"],
  ["Trays / compartments", "trays_or_compartments"],
  ["Dimensions", "dimensions"],
  ["Weight", "weight"],
  ["Speed", "speed"],
  ["Battery / runtime", "battery_runtime"],
  ["Charging time", "charging_time"],
  ["Min. passage width", "min_passage_width"],
  ["Cleaning efficiency", "cleaning_efficiency"],
  ["Tank / dustbin", "tank_or_dustbin"],
  ["Navigation", "navigation"],
];

const CATEGORY_LABEL = {
  delivery: "Delivery", cleaning: "Cleaning", humanoid: "Humanoid",
  industrial: "Industrial", escort: "Escorting",
};

/**
 * Product cards for the non-technical persona ("which robot fits my customer?").
 * Only fields the profile actually carries are rendered — a missing spec is
 * omitted rather than shown as an empty row or an invented default.
 */
export function renderProfiles(profiles, { mediaUrl } = {}) {
  if (!profiles?.length) return "";
  const cards = profiles.map((card) => {
    const url = sameOrigin(card.image_url, mediaUrl);
    const thumb = url
      ? `<button class="profile-thumb" type="button" data-lightbox="${esc(url)}"
                 data-caption="${esc(card.product || "")}" aria-label="Open ${esc(card.product || "product")} photo full size">
           <img src="${esc(url)}" alt="" loading="lazy" />
         </button>`
      : "";
    const rows = PROFILE_ROWS
      .filter(([, key]) => card[key] != null && String(card[key]).trim() !== "")
      .map(([label, key]) => `
        <div class="profile-row"><dt>${esc(label)}</dt><dd>${esc(card[key])}</dd></div>`)
      .join("");
    const features = (card.special_features || [])
      .filter(Boolean)
      .map((feature) => `<li class="label-chip">${esc(feature)}</li>`)
      .join("");
    const environments = (card.environments || [])
      .filter(Boolean)
      .slice(0, 6)
      .map((tag) => `<li class="env-chip">${esc(String(tag).replace(/_/g, " "))}</li>`)
      .join("");
    const category = card.category
      ? `<span class="profile-cat">${esc(CATEGORY_LABEL[card.category] || String(card.category).replace(/_/g, " "))}</span>`
      : "";
    // Only a 0..1 score can honestly be called a percentage. The advisory route
    // returns a fused retrieval score (e.g. 8.56), so anything outside 0..1 is
    // left out rather than shown as a made-up "856% match".
    const match = typeof card.score === "number" && card.score >= 0 && card.score <= 1
      ? `<span class="profile-match" title="Retrieval score for this question">${esc(Math.round(card.score * 100))}% match</span>`
      : "";
    return `
      <article class="profile-card">
        <header class="profile-head">
          ${thumb}
          <div class="profile-id">
            <h4 class="profile-name">${esc(card.product || "Product")}</h4>
            <div class="profile-tags">${category}${match}${card.language ? `<span class="profile-lang">${esc(String(card.language).toUpperCase())}</span>` : ""}</div>
          </div>
        </header>
        ${rows ? `<dl class="profile-specs">${rows}</dl>` : ""}
        ${features ? `<ul class="figure-labels profile-features">${features}</ul>` : ""}
        ${environments ? `<ul class="env-list">${environments}</ul>` : ""}
        ${card.key_use_case ? `<p class="profile-use">${esc(truncate(card.key_use_case, 320))}</p>` : ""}
        ${card.citation ? `<footer class="profile-cite">${esc(card.citation)}</footer>` : ""}
      </article>`;
  }).join("");
  return `
    <section class="profiles" aria-label="Matching products">
      <h3 class="block-title">Matching products<span class="block-count">${profiles.length}</span></h3>
      <div class="profile-grid">${cards}</div>
    </section>`;
}

/** The parameter index rows: an exact value a technician can read off. */
export function renderSpecs(params) {
  if (!params?.length) return "";
  const cards = params.map((row) => {
    const locator = [
      row.product ? esc(row.product) : "",
      row.section_number ? `§${esc(row.section_number)}` : (row.section ? `§${esc(row.section)}` : ""),
      row.page != null ? `p. ${esc(row.page)}` : "",
      row.language ? esc(String(row.language).toUpperCase()) : "",
    ].filter(Boolean).join(" · ");
    // `value` often already contains the unit ("20 kg (10 kg pro Tablett)",
    // "1.2 m/s") while `unit` repeats it — appending it would read "1.2 m/s m/s".
    const value = String(row.value ?? "");
    const unit = row.unit && !value.includes(String(row.unit))
      ? `<span class="spec-unit">${esc(row.unit)}</span>`
      : "";
    return `
      <div class="spec-card">
        <p class="spec-name">${esc(row.parameter || "Value")}</p>
        <p class="spec-value">${esc(value)}${unit}</p>
        ${locator ? `<p class="spec-source">${locator}</p>` : ""}
      </div>`;
  }).join("");
  return `<div class="specs">${cards}</div>`;
}

/** Figures with their `Labels:` callout legend; click opens the lightbox. */
export function renderFigures(figures, { mediaUrl } = {}) {
  if (!figures?.length) return "";
  const cards = figures.map((figure, index) => {
    const url = sameOrigin(figure.image_url, mediaUrl);
    const img = url
      ? `<button class="figure-img-btn" type="button" data-figure="${index}" aria-label="Open figure full size">
           <img class="figure-img" src="${esc(url)}" alt="${esc(figure.caption || figure.product || "Manual figure")}" loading="lazy" />
         </button>`
      : "";
    const labels = (figure.labels || []).map((label) => {
      const match = String(label).match(/^(\d+)\s+(.*)$/);
      return match
        ? `<li class="label-chip"><b>${esc(match[1])}</b>${esc(match[2])}</li>`
        : `<li class="label-chip">${esc(label)}</li>`;
    }).join("");
    // "Labels" is the legend title the corpus uses; keep the word so a reader can
    // match it against the manual page.
    const legend = labels ? `<p class="labels-title">Labels</p><ul class="figure-labels">${labels}</ul>` : "";
    return `
      <figure class="figure-card">
        ${img}
        <figcaption class="figure-body">
          ${figure.caption ? `<p class="figure-caption">${esc(figure.caption)}</p>` : ""}
          ${legend}
          ${figure.language ? `<p class="figure-lang">${esc(String(figure.language).toUpperCase())}</p>` : ""}
        </figcaption>
      </figure>`;
  }).join("");
  return `<div class="figures">${cards}</div>`;
}

/**
 * The G5 verdict. `checked === false` means there was no evidence at all, so
 * 0/0 passed every check vacuously — that must never render as a green pass.
 */
export function renderVerification(v, { id = "verify-details" } = {}) {
  if (!v) return "";
  const nothingChecked = v.checked === false || v.state === "no-evidence";
  const state = nothingChecked ? "none" : v.verified ? "good" : v.grounded ? "warn" : "bad";
  const icon = nothingChecked ? "—" : v.verified ? "✓" : v.grounded ? "!" : "✕";
  const label = nothingChecked
    ? "Not answered from the manuals"
    : v.verified ? "Verified" : v.grounded ? "Grounded" : "Check failed";
  const counts = nothingChecked
    ? ""
    : ` · ${v.sentences_cited ?? 0}/${v.sentences_checked ?? 0} cited · ${v.numbers_verified ?? 0}/${v.numbers_checked ?? 0} numbers`;
  const panelId = esc(id);
  return `
    <div class="verify">
      <button class="verify-badge verify-badge--${state}" type="button" data-action="verify"
              aria-expanded="false" aria-controls="${panelId}">
        <span class="v-icon" aria-hidden="true">${icon}</span>
        <span>${esc(label)}</span>
        <span class="v-counts">${esc(counts)}</span>
        <span class="v-caret" aria-hidden="true">▾</span>
      </button>
      <div class="verify-details" id="${panelId}" hidden>${verificationDetailsHTML(v)}</div>
    </div>`;
}

export function verificationDetailsHTML(v) {
  const nothingChecked = v.checked === false || v.state === "no-evidence";
  const rate = (name, verified, checked, value) => {
    const pct = Math.round((value ?? (checked ? verified / checked : 1)) * 100);
    const cls = pct === 100 ? "" : pct >= 50 ? "rate-fill--warn" : "rate-fill--bad";
    return `
      <div class="rate">
        <div class="rate-label"><span>${esc(name)}</span><b>${verified}/${checked}</b></div>
        <div class="rate-bar"><div class="rate-fill ${cls}" style="width:${pct}%"></div></div>
      </div>`;
  };
  const issues = (v.issues || []).length
    ? `<ul class="issues">${v.issues.map((issue) =>
        `<li class="issue ${issue.severity === "error" ? "issue--error" : ""}">
           <b>${esc(issue.check)} · ${esc(issue.action || issue.severity)}</b> — ${esc(issue.message)}${
             issue.detail ? `<q class="issue-detail">${esc(issue.detail)}</q>` : ""}
         </li>`).join("")}</ul>`
    : nothingChecked
      ? `<p class="verify-note">The retrieval engine found no passage that answers this, so the checks had
         nothing to certify. Rephrase, name the robot, or ask for a specific parameter.</p>`
      : `<p class="verify-note verify-note--ok">No issues — every claim is cited and every number matched the source.</p>`;
  return `
    <p class="verify-summary">${esc(v.summary || "")}</p>
    ${nothingChecked ? "" : `<div class="verify-grid">
      ${rate("Citations", v.sentences_cited ?? 0, v.sentences_checked ?? 0, v.citation_rate)}
      ${rate("Numbers", v.numbers_verified ?? 0, v.numbers_checked ?? 0, v.numeric_rate)}
      ${rate("Quotes", v.quotes_verified ?? 0, v.quotes_checked ?? 0, v.quote_rate)}
    </div>`}
    ${issues}`;
}

/**
 * Expandable source cards under the answer: document, section, page, language,
 * kind and the excerpt the claim came from. Clicking opens the citation modal.
 */
export function renderSources(citations, { icons } = {}) {
  if (!citations?.length) return "";
  const chips = citations.map((c) => {
    const locator = [
      c.section ? `§${c.section}` : "",
      c.page != null ? `p. ${c.page}` : "",
      c.language ? String(c.language).toUpperCase() : "",
    ].filter(Boolean).join(" · ");
    const icon = icons?.(c.kind === "param" ? "table" : c.image ? "image" : "doc", { size: 15 }) || "";
    return `
      <button class="source-card" type="button" data-ref="${esc(c.n)}" aria-label="Open source ${esc(c.n)}: ${esc(c.citation || "")}">
        <span class="source-n">[${esc(c.n)}]</span>
        <span class="source-body">
          <span class="source-title">${icon}${esc(c.title || c.product || "Source")}</span>
          <span class="source-meta">${esc([kindLabel(c.kind), locator].filter(Boolean).join(" · "))}</span>
          ${c.excerpt ? `<span class="source-excerpt">${esc(truncate(c.excerpt, 150))}</span>` : ""}
        </span>
        <span class="source-go" aria-hidden="true">›</span>
      </button>`;
  }).join("");
  return `
    <details class="sources" ${citations.length <= 3 ? "open" : ""}>
      <summary class="sources-title">
        <span>Sources</span><span class="sources-count">${citations.length}</span>
      </summary>
      <div class="source-list">${chips}</div>
    </details>`;
}

/** Thumbs up/down, copy, regenerate, raw JSON. Labels say what is local-only. */
export function renderActions(message, { icons } = {}) {
  const ic = (name, size = 15) => icons?.(name, { size }) || "";
  const feedback = message.feedback || null;
  const localNote = "saved in this browser only — the API has no feedback endpoint";
  return `
    <div class="msg-actions" role="group" aria-label="Answer actions">
      <button class="action-btn" type="button" data-action="copy" title="Copy the answer text">${ic("copy")}<span>Copy</span></button>
      <button class="action-btn" type="button" data-action="regenerate" title="Ask the API again">${ic("refresh")}<span>Regenerate</span></button>
      <button class="action-btn action-btn--icon ${feedback === "up" ? "is-active" : ""}" type="button"
              data-action="feedback" data-value="up" aria-pressed="${feedback === "up"}" title="Good answer (${localNote})">${ic("thumbUp")}</button>
      <button class="action-btn action-btn--icon ${feedback === "down" ? "is-active" : ""}" type="button"
              data-action="feedback" data-value="down" aria-pressed="${feedback === "down"}" title="Poor answer (${localNote})">${ic("thumbDown")}</button>
      <button class="action-btn action-btn--raw" type="button" data-action="raw" title="The unedited GET /search response">
        ${ic("code")}<span>Raw <code>/search</code> JSON</span>
      </button>
    </div>`;
}

// ---------------------------------------------------------------------------
// errors & status
// ---------------------------------------------------------------------------
export function errorCardHTML(error, question, { icons } = {}) {
  const ic = (name, size = 16) => icons?.(name, { size }) || "";
  const status = error?.status;
  const title =
    error?.kind === "timeout" ? "The answer engine took too long"
      : error?.kind === "network" ? "Cannot reach the answer engine"
        : status === 503 ? "The search index isn’t built yet"
          : status === 422 ? "The question was rejected"
            : "The answer could not be produced";
  const message = esc(error?.message || "Unknown error");
  const fix = error?.fix
    ? `<p class="error-fix-label">Build the index once, then retry:</p><code class="error-fix">${esc(error.fix)}</code>`
    : "";
  const hint =
    error?.kind === "network"
      ? `<p class="error-hint">Start the API with <code>python -m api</code>, then retry.</p>`
      : status === 422
        ? `<p class="error-hint">Questions must be 1–2000 characters.</p>`
        : "";
  return `
    <div class="error-card" role="alert">
      <div class="error-head">${ic("alert", 18)}<h3>${esc(title)}</h3></div>
      <p class="error-msg">${message}</p>
      ${fix}${hint}
      <div class="msg-actions">
        <button class="action-btn action-btn--primary" type="button" data-action="retry" data-question="${esc(question || "")}">
          ${ic("refresh")}<span>Retry</span>
        </button>
      </div>
    </div>`;
}

export function statusChipHTML(health, { icons } = {}) {
  const ic = (name, size = 14) => icons?.(name, { size }) || "";
  if (!health || health.reachable === false) {
    return { className: "status--down", label: "API offline", title: health?.detail || "Cannot reach GET /health" };
  }
  if (!health.ready) {
    return { className: "status--down", label: "index not built", title: health.detail || "Run: python -m ingest.run --data data" };
  }
  // A configured Gemini whose last call failed is answering through the offline
  // formatter; the pill must not keep saying "Gemini".
  const llmError = health.provider?.provider === "gemini" ? health.provider?.last_error : null;
  const provider = health.provider?.provider === "gemini"
    ? (llmError ? "Gemini failing" : "Gemini")
    : "offline";
  const vector = health.vector_active ? "hybrid" : "keyword";
  // model_note: the configured model was not available and another one answers.
  const why = llmError
    || (health.provider?.provider === "gemini" ? health.provider?.model_note : health.provider?.reason);
  return {
    className: "status--ready",
    label: `ready · ${provider} · ${vector}`,
    title: `${health.chunks} chunks · ${health.params} parameters · ${health.products} products · ` +
      `${health.media_files} figures · vector: ${health.vector_detail || vector}` +
      (why ? ` · answers: ${why}` : ""),
    html: `${ic("database")}`,
  };
}

// ---------------------------------------------------------------------------
// sidebar & citation modal
// ---------------------------------------------------------------------------
export function conversationItemHTML(conversation, { active = false } = {}) {
  const count = conversation.messages?.length ?? 0;
  return `
    <li class="conv-item ${active ? "is-active" : ""}" data-conv="${esc(conversation.id)}">
      <button class="conv-open" type="button" ${active ? 'aria-current="true"' : ""} title="${esc(conversation.title)}">
        <span class="conv-title">${esc(conversation.title)}</span>
        <span class="conv-meta">${esc(relativeDay(conversation.updatedAt))} · ${count} ${count === 1 ? "message" : "messages"}</span>
      </button>
      <button class="conv-delete" type="button" data-action="delete-conv" data-conv-id="${esc(conversation.id)}"
              aria-label="Delete conversation: ${esc(conversation.title)}">×</button>
    </li>`;
}

export function conversationListHTML(conversations, activeId) {
  if (!conversations?.length) {
    return `<p class="side-empty">No saved conversations yet. Conversations are stored in this browser only —
      the API is stateless.</p>`;
  }
  return `<ul class="conv-list">${conversations
    .map((c) => conversationItemHTML(c, { active: c.id === activeId }))
    .join("")}</ul>`;
}

export function citationModalHTML(citation, { mediaUrl, icons } = {}) {
  const ic = (name, size = 15) => icons?.(name, { size }) || "";
  if (!citation) {
    return `<div class="cit-head"><h3 class="cit-title" id="citation-title">Source</h3></div>
      <p class="cit-missing">No detail available for this reference.</p>`;
  }
  const url = sameOrigin(citation.image_url, mediaUrl);
  const badges = [
    `<span class="cit-badge cit-badge--kind">${ic(citation.kind === "param" ? "table" : citation.image ? "image" : "doc")}${esc(kindLabel(citation.kind))}</span>`,
    citation.language ? `<span class="cit-badge cit-badge--lang">${esc(String(citation.language).toUpperCase())}</span>` : "",
    citation.product ? `<span class="cit-badge">${esc(citation.product)}</span>` : "",
  ].filter(Boolean).join("");
  const row = (key, value) =>
    `<div class="cit-row"><span class="k">${esc(key)}</span><span class="v">${value == null || value === "" ? "—" : esc(value)}</span></div>`;
  const locator = [
    row("Manual", citation.title),
    row("Section", citation.section ? `§${citation.section}` : ""),
    row("PDF page", citation.page != null ? `p. ${citation.page}` : ""),
  ].join("");
  const snippet = citation.excerpt
    ? `<p class="cit-snippet-label">${ic("quote")} Source snippet</p><blockquote class="cit-snippet">${esc(citation.excerpt)}</blockquote>`
    : "";
  const image = url
    ? `<div class="cit-image"><img src="${esc(url)}" alt="${esc(citation.product || "Manual figure")}"
         data-lightbox="${esc(url)}" data-caption="${esc(citation.citation || "")}" /></div>`
    : "";
  return `
    <div class="cit-head">
      <span class="cit-n">${esc(citation.n)}</span>
      <h3 class="cit-title" id="citation-title">${esc(citation.title || citation.product || "Source")}</h3>
    </div>
    <div class="cit-badges">${badges}</div>
    <div class="cit-locator">${locator}</div>
    ${snippet}
    ${image}
    <p class="cit-full">${esc(citation.citation || "")}</p>`;
}

export function drawerBodyHTML(message) {
  if (message?.search) {
    const s = message.search;
    const items = [
      ["route", s.route], ["language", s.language ? String(s.language).toUpperCase() : ""],
      ["confidence", s.confidence], ["hits", (s.hits || []).length],
      ["params", (s.params || []).length], ["profiles", (s.profiles || []).length],
    ];
    if (s.cross_lingual) items.push(["cross-lingual", "yes"]);
    if (Array.isArray(s.tiers_used) && s.tiers_used.length) items.push(["tiers", s.tiers_used.join(", ")]);
    const meta = items
      .filter(([, value]) => value !== "" && value != null)
      .map(([key, value]) => `<span class="dm-item">${esc(key)} <b>${esc(value)}</b></span>`)
      .join("");
    return { meta, json: highlightJson(s) };
  }
  if (message?.searchError) {
    return { meta: "", json: `<span class="j-null">// GET /search failed: ${esc(message.searchError)}\n// Press “Re-run live” to try again.</span>` };
  }
  if (message?.search === null) {
    return {
      meta: "",
      json: `<span class="j-null">// Not loaded for this restored conversation.\n// Press “Re-run live” to call GET /search now.</span>`,
    };
  }
  return { meta: "", json: `<span class="j-null">// loading…</span>` };
}

// ---------------------------------------------------------------------------
// small utilities
// ---------------------------------------------------------------------------
export function truncate(value, limit) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

export function shortTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function relativeDay(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const days = Math.floor((Date.now() - date.getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}
