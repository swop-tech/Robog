/* Markdown renderer for assistant answers — small, safe, and honest about scope.
 *
 * The answer composer emits a *subset* of markdown (`**bold**`, `-` bullets,
 * `“quoted source text”`, `[n]` citation markers, occasional pipe tables from
 * `/search` payloads), so this renderer implements that subset properly rather
 * than pulling in a library: the UI is zero-build, offline and CDN-free by
 * design (see ui/README.md).
 *
 * Safety: every input line is HTML-escaped *before* any inline rule runs, so
 * manual text can never inject markup. The only HTML this module produces is its
 * own.
 *
 * Deliberate non-goals, because guessing here damages real answers:
 *  - single-`*` italics: the corpus contains dimensions like `84*60*49 cm`, and
 *    treating those as emphasis corrupts a spec value;
 *  - links: answers contain none, and an `<a href>` built from model output is
 *    an injection surface. `[text](url)` is rendered as its text.
 *
 * Pure function, no DOM: unit-tested with `node --test ui/tests/`.
 */

/** Escape for HTML text *and* attribute contexts. */
export function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Escape only `& < >` — for `<pre>` JSON, where quotes must stay literal. */
export function escText(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const CALLOUT_WORDS = ["warning", "caution", "attention", "danger", "important", "note", "notice"];

/**
 * Inline rules on already-escaped text: code spans, bold, link-syntax stripping
 * and clickable `[n]` / `[n, m]` citation markers.
 */
export function inline(text, { citations = true } = {}) {
  let out = esc(text);

  // `code` first, so its content is never re-processed by the rules below.
  const codeSpans = [];
  out = out.replace(/`([^`]+)`/g, (_, body) => {
    codeSpans.push(body);
    return `\u0000${codeSpans.length - 1}\u0000`;
  });

  // [label](target) -> label (no anchor: see the module note)
  out = out.replace(/\[([^\]]+)\]\(([^)]*)\)/g, (_, label) => label);

  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  if (citations) {
    out = out.replace(/\[(\d+(?:\s*[,;]\s*\d+)*)\]/g, (_, nums) =>
      nums
        .split(/[,;]/)
        .map((n) => n.trim())
        .filter(Boolean)
        .map(
          (n) =>
            `<button class="cite" type="button" data-ref="${n}" ` +
            `aria-label="Open source ${n}" title="Source ${n}">[${n}]</button>`
        )
        .join(""));
  }

  return out.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codeSpans[Number(i)]}</code>`);
}

/** `| a | b |` -> ["a", "b"]; returns null when the line is not a table row. */
function tableCells(line) {
  const trimmed = line.trim();
  if (!trimmed.startsWith("|")) return null;
  return trimmed
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

const isTableSeparator = (line) => /^\s*\|?\s*:?-{2,}.*$/.test(line) && line.includes("-") &&
  (line.trim().startsWith("|") || /^[\s:-]+$/.test(line)) && /-/.test(line) &&
  !/[^\s:|-]/.test(line);

/** A `Warning：`/`**Note:**` lead-in the corpus really uses. */
function calloutKind(line) {
  const head = line.replace(/^\*\s*/, "").replace(/^\*/, "").toLowerCase();
  const word = head.match(/^([a-z]+)/);
  if (!word) return null;
  const kind = word[1];
  if (!CALLOUT_WORDS.includes(kind)) return null;
  const rest = line.replace(/^\**\s*[A-Za-z]+\s*[:：]\s*/, "").replace(/\*\*$/g, "").trim();
  return { kind, rest };
}

/**
 * Render markdown-ish answer text to HTML.
 *
 * @param {string} source
 * @param {{citations?: boolean}} [options]
 * @returns {string} HTML
 */
export function renderMarkdown(source, options = {}) {
  const opts = { citations: true, ...options };
  const lines = String(source ?? "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];

  let list = null;          // { tag: "ul"|"ol", items: string[] }
  let paragraph = [];
  let code = null;          // { lang, body: string[] }
  let quote = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    out.push(`<p>${inline(paragraph.join(" "), opts)}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i, opts)}</li>`).join("")}</${list.tag}>`);
    list = null;
  };
  const flushQuote = () => {
    if (!quote.length) return;
    out.push(`<blockquote>${inline(quote.join(" "), opts)}</blockquote>`);
    quote = [];
  };
  const flushAll = () => { flushParagraph(); flushList(); flushQuote(); };

  for (let i = 0; i < lines.length; i += 1) {
    const raw = lines[i];
    const line = raw.replace(/\s+$/, "");

    // fenced code ---------------------------------------------------------
    const fence = line.match(/^\s*```(\w*)\s*$/);
    if (fence) {
      if (code) {
        out.push(`<pre class="code-block"${code.lang ? ` data-lang="${esc(code.lang)}"` : ""}><code>${escText(code.body.join("\n"))}</code></pre>`);
        code = null;
      } else {
        flushAll();
        code = { lang: fence[1] || "", body: [] };
      }
      continue;
    }
    if (code) { code.body.push(raw); continue; }

    if (!line.trim()) { flushAll(); continue; }

    // tables --------------------------------------------------------------
    const cells = tableCells(line);
    if (cells && isTableSeparator(lines[i + 1] || "")) {
      flushAll();
      const header = cells;
      i += 1;
      const rows = [];
      while (i + 1 < lines.length) {
        const rowCells = tableCells(lines[i + 1]);
        if (!rowCells) break;
        rows.push(rowCells);
        i += 1;
      }
      out.push(
        `<div class="table-wrap"><table><thead><tr>${header.map((h) => `<th>${inline(h, opts)}</th>`).join("")}</tr></thead>` +
        `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c, opts)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
      );
      continue;
    }

    // headings ------------------------------------------------------------
    const heading = line.match(/^\s{0,3}(#{1,4})\s+(.*)$/);
    if (heading) {
      flushAll();
      const level = heading[1].length;
      out.push(`<h${level + 1} class="md-h md-h${level}">${inline(heading[2], opts)}</h${level + 1}>`);
      continue;
    }

    // horizontal rule -----------------------------------------------------
    if (/^\s{0,3}(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flushAll(); out.push("<hr />"); continue; }

    // blockquote ----------------------------------------------------------
    const quoted = line.match(/^\s{0,3}>\s?(.*)$/);
    if (quoted) { flushParagraph(); flushList(); quote.push(quoted[1]); continue; }
    flushQuote();

    // lists ---------------------------------------------------------------
    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    const numbered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    if (bullet || numbered) {
      flushParagraph();
      const tag = bullet ? "ul" : "ol";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(bullet ? bullet[1] : numbered[2]);
      continue;
    }

    // callout (Warning: / **Note:**) --------------------------------------
    const callout = calloutKind(line);
    if (callout && !list) {
      flushAll();
      const tone = ["warning", "caution", "attention", "danger"].includes(callout.kind) ? "warn" : "info";
      out.push(
        `<div class="callout callout--${tone}"><span class="callout-kind">${esc(callout.kind)}</span>` +
        `<div>${inline(callout.rest || line, opts)}</div></div>`
      );
      continue;
    }

    // paragraph text ------------------------------------------------------
    flushList();
    paragraph.push(line.trim());
  }

  if (code) {
    out.push(`<pre class="code-block"><code>${escText(code.body.join("\n"))}</code></pre>`);
  }
  flushAll();
  return out.join("\n");
}

/** Syntax-highlight a JSON value for the raw-`/search` drawer. */
export function highlightJson(value) {
  let text;
  try {
    text = JSON.stringify(value, null, 2);
  } catch (err) {
    // A payload the JSON encoder refuses (circular, BigInt) must not break the
    // drawer: show the reason instead of throwing into the click handler.
    return `<span class="j-null">// This value could not be serialised: ${escText(String(err?.message || err))}</span>`;
  }
  if (text === undefined) text = String(value);
  const json = escText(text);
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

/** Plain text of a markdown string, for `Copy answer` and conversation titles. */
export function plainText(source) {
  return String(source ?? "")
    .replace(/```[\s\S]*?```/g, (block) => block.replace(/^```.*$/gm, "").trim())
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/^\s{0,3}#{1,4}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "• ")
    .replace(/^\s*>\s?/gm, "")
    .replace(/^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/gm, "")
    .replace(/^\s*\|/gm, "")
    .replace(/\s*\|\s*/g, "  ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
