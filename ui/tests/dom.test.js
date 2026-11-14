/* DOM smoke test — the closest thing to a browser this repo can run headless.
 *
 * `ui/` is zero-build, so nothing else verifies that the modules actually work
 * together in a document: that `boot()` finds every element, that clicking a
 * starter chip issues `POST /ask` and renders the answer, that the citation
 * modal, the raw-JSON drawer, the lightbox and the theme switch really toggle.
 * This file loads `index.html` into jsdom, injects the real `app.js` and drives
 * it with a stubbed `fetch` whose payload has the exact shape `api/service.py`
 * returns.
 *
 * jsdom is an OPTIONAL dev-only tool, not a project dependency (the UI itself
 * ships with none). Without it, every test here is skipped and the rest of the
 * suite still runs:
 *
 *     npm install --no-save jsdom        # once, in the repository root
 *     node --test ui/tests/dom.test.js
 */

import assert from "node:assert/strict";
import test, { after, before, describe } from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

let JSDOM = null;
try {
  ({ JSDOM } = await import("jsdom"));
} catch {
  JSDOM = null;                     // not installed -> the suite below is skipped
}

const UI = dirname(dirname(fileURLToPath(import.meta.url)));
const SKIP_REASON = "jsdom is not installed (npm install --no-save jsdom)";

let GLOBAL_KEYS = [];

/** Assign a browser global even where Node defines it getter-only (`navigator`). */
function setGlobal(name, value) {
  try {
    globalThis[name] = value;
  } catch {
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  }
}

/** A `POST /ask` response shaped exactly like `api/service.py::_ask_payload`. */
const ASK_PAYLOAD = {
  question: "Wieviel Zuladung hat der FlashBot?",
  answer: "Der FlashBot trägt **20 kg** Zuladung, verteilt auf vier Ablagen mit je 10 kg [1].",
  language: "de",
  route: "lookup",
  citations: [
    {
      n: 1, kind: "param", citation: "FlashBot User Manual §2.3 p.5 (DE)",
      language: "de", product: "FlashBot", page: 5, section: "2.3",
      title: "FlashBot User Manual", excerpt: "Zuladung: 20 kg (10 kg pro Ablage).",
      image: null, image_url: null,
    },
  ],
  figures: [
    {
      image: "images/de-p006R-fig01.png", language: "de", product: "FlashBot",
      caption: "FlashBot Komponenten", labels: ["1 Ablage", "2 Not-Aus"],
      image_url: "http://127.0.0.1:8000/media/flashbot_user_manual/images/de-p006R-fig01.png",
    },
  ],
  params: [
    {
      parameter: "Zuladung", value: "20", unit: "kg", value_number: 20, column: "Zuladung",
      product: "FlashBot", language: "de", page: 5, section: "2.3 Technische Daten",
      section_number: "2.3", chunk_id: "flashbot-de-0042", citation: "FlashBot User Manual §2.3 p.5 (DE)",
    },
  ],
  profiles: [],
  verification: {
    grounded: true, verified: true, checked: true, state: "verified",
    summary: "Alle 2 Aussagen belegt; alle 2 Zahlen geprüft.",
    citation_rate: 1, numeric_rate: 1, quote_rate: 1,
    sentences_checked: 2, sentences_cited: 2, numbers_checked: 2, numbers_verified: 2,
    numbers_unverified: 0, quotes_checked: 0, quotes_verified: 0, issues: [],
  },
  cross_lingual: false,
  provider: "offline",
  model: null,
  latency_ms: 137,
  source_languages: ["de"],
};

const SEARCH_PAYLOAD = {
  route: "lookup", language: "de", confidence: 0.91,
  hits: [{ chunk_id: "flashbot-de-0042", product: "FlashBot", score: 0.87 }],
  params: ASK_PAYLOAD.params, profiles: [], cross_lingual: false, tiers_used: ["de"],
};

const HEALTH_PAYLOAD = {
  ready: true, chunks: 1351, params: 2039, products: 13, media_files: 476,
  provider: { provider: "offline" }, vector_active: false, vector_detail: "no vector index",
  detail: "", uptime_s: 42,
};

/** Mutable fetch stub: `mode` decides what the "server" answers. */
const net = { mode: "ok", calls: [], delayMs: 0 };

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function stubFetch(url, init = {}) {
  net.calls.push({ url: String(url), method: init.method || "GET", body: init.body });
  const respond = async (body, status = 200) => {
    if (net.delayMs) await sleep(net.delayMs);
    return { ok: status < 300, status, text: async () => JSON.stringify(body) };
  };

  if (net.mode === "offline") return Promise.reject(new TypeError("fetch failed"));
  if (net.mode === "index-missing") {
    return respond({ reason: "search index not built", fix: "python -m ingest.run --data data" }, 503);
  }
  if (net.mode === "no-evidence") {
    return respond({
      ...ASK_PAYLOAD,
      answer: "Die Handbücher enthalten dazu keine Angabe.",
      citations: [], params: [], figures: [],
      verification: { ...ASK_PAYLOAD.verification, grounded: false, verified: false, checked: false, state: "no-evidence", summary: "no evidence" },
    });
  }

  const path = String(url).split("?")[0];
  if (init.method === "POST" && path === "/ask") return respond(ASK_PAYLOAD);
  if (path === "/search") return respond(SEARCH_PAYLOAD);
  if (path === "/health") return respond(HEALTH_PAYLOAD);
  if (path === "/products") return respond({ count: 2, products: [{ product: "BellaBot" }, { product: "FlashBot" }] });
  return respond({ error: "unexpected" }, 404);
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
/** Let queued microtasks and the app's own timers settle. */
async function settle(times = 6) {
  for (let i = 0; i < times; i += 1) await tick();
}

describe("DOM: the app boots and answers a question", { skip: !JSDOM && SKIP_REASON }, () => {
  let window, document;

  before(async () => {
    const html = readFileSync(join(UI, "index.html"), "utf8");
    const dom = new JSDOM(html, {
      url: "http://localhost:8000/",
      runScripts: "outside-only",
      pretendToBeVisual: true,
      storageQuota: 5_000_000,
    });
    window = dom.window;
    document = window.document;

    // -- what jsdom does not implement, stubbed the way a browser behaves ----
    window.matchMedia = (query) => ({
      matches: false, media: query, onchange: null,
      addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false,
    });
    window.scrollTo = () => {};
    window.Element.prototype.scrollTo = function scrollTo() {};
    window.Element.prototype.scrollIntoView = function scrollIntoView() {};
    if (!window.CSS) window.CSS = {};
    if (!window.CSS.escape) window.CSS.escape = (value) => String(value).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`);
    window.HTMLElement.prototype.getClientRects = function getClientRects() { return [{}]; };

    // Keep the process exitable: the app runs a 60 s health poll and a 100 ms
    // elapsed-time ticker, and jsdom keeps a rAF loop going.
    const realSetInterval = globalThis.setInterval;
    globalThis.setInterval = (fn, ms, ...args) => {
      const timer = realSetInterval(fn, ms, ...args);
      timer.unref?.();
      return timer;
    };

    // -- expose the browser globals the modules expect -----------------------
    // (`navigator` is getter-only on Node's globalThis, hence defineProperty.)
    for (const [name, value] of Object.entries({
      window, document, navigator: window.navigator, localStorage: window.localStorage,
      matchMedia: window.matchMedia, CSS: window.CSS, HTMLElement: window.HTMLElement,
      Element: window.Element, Node: window.Node, Event: window.Event,
      MouseEvent: window.MouseEvent, KeyboardEvent: window.KeyboardEvent, fetch: stubFetch,
    })) {
      setGlobal(name, value);
    }
    GLOBAL_KEYS = Object.keys({ window: 1, document: 1, navigator: 1, localStorage: 1, matchMedia: 1, CSS: 1, HTMLElement: 1, Element: 1, Node: 1, Event: 1, MouseEvent: 1, KeyboardEvent: 1, fetch: 1 });

    if (document.readyState === "loading") {
      await new Promise((resolve) => window.addEventListener("DOMContentLoaded", resolve, { once: true }));
    }
    await import("../app.js");        // runs boot()
    await settle();
  });

  after(() => {
    window?.close();
    for (const key of GLOBAL_KEYS) setGlobal(key, undefined);
  });

  const click = (element) => {
    assert.ok(element, "element to click must exist");
    element.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  };
  const key = (element, keyName, opts = {}) => {
    element.dispatchEvent(new window.KeyboardEvent("keydown", { key: keyName, bubbles: true, cancelable: true, ...opts }));
  };
  const byId = (id) => document.getElementById(id);

  test("boot painted the icons, the sidebar and the health status", async () => {
    assert.equal(document.querySelectorAll("[data-icon] svg").length, document.querySelectorAll("[data-icon]").length);
    assert.ok(byId("brand-mark").querySelector("svg"), "the brand mark is injected");
    await settle();                                    // let GET /health resolve
    assert.match(byId("status-label").textContent, /ready/);
    assert.ok(byId("status").className.includes("status--ready"), byId("status").className);
    assert.ok(byId("side-status").title.includes("1351 chunks"), byId("side-status").title);
    assert.ok(byId("health-banner").hidden, "no warning banner while the index is ready");
  });

  test("the welcome screen shows the four scenarios and no answer yet", () => {
    assert.equal(byId("welcome").hidden, false);
    assert.equal(byId("chat").hidden, true);
    assert.equal(document.querySelectorAll(".chip").length, 4);
    assert.ok(document.querySelectorAll(".cap-pill").length >= 5);
    assert.match(byId("capabilities").textContent, /No streaming/);
  });

  test("the send button is disabled until there is a question", () => {
    assert.equal(byId("send").disabled, true);
    byId("input").value = "Wieviel Zuladung hat der FlashBot?";
    byId("input").dispatchEvent(new window.Event("input", { bubbles: true }));
    assert.equal(byId("send").disabled, false);
    assert.equal(byId("char-count").textContent, `${byId("input").value.length} / 2000`);
  });

  test("over the API limit the composer refuses to send", () => {
    byId("input").value = "x".repeat(2001);
    byId("input").dispatchEvent(new window.Event("input", { bubbles: true }));
    assert.equal(byId("send").disabled, true);
    assert.ok(byId("char-count").className.includes("is-near"));
    byId("input").value = "Wieviel Zuladung hat der FlashBot?";
    byId("input").dispatchEvent(new window.Event("input", { bubbles: true }));
  });

  test("submitting calls POST /ask and GET /search, then renders the answer", async () => {
    net.calls.length = 0;
    net.delayMs = 60;
    byId("ask-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
    // the pending state is painted synchronously, before the response arrives
    assert.ok(document.querySelector(".thinking"), "a thinking indicator is shown");
    assert.ok(document.querySelector(".skeleton"), "a skeleton is shown");
    assert.equal(byId("send").disabled, true, "double-submit is blocked while in flight");

    await sleep(140);
    await settle(6);
    net.delayMs = 0;

    const methods = net.calls.map((call) => `${call.method} ${call.url.split("?")[0]}`);
    assert.ok(methods.includes("POST /ask"), methods.join(", "));
    assert.ok(methods.includes("GET /search"), methods.join(", "));
    // Only AskRequest fields that differ from the server's defaults are sent:
    // `follow_user_language` / `cross_lingual_fallback` default to true in
    // api/app.py, so the UI omits them rather than echoing them (see api.js).
    assert.deepEqual(JSON.parse(net.calls.find((c) => c.method === "POST").body), {
      question: "Wieviel Zuladung hat der FlashBot?", k: 8,
    });

    assert.equal(byId("chat").hidden, false);
    assert.equal(byId("welcome").hidden, true);
    assert.equal(document.querySelectorAll(".msg--user .bubble--user").length, 1);
    assert.equal(document.querySelector(".bubble--user").textContent, "Wieviel Zuladung hat der FlashBot?");
    assert.equal(document.querySelectorAll(".bot-card").length, 1);
    assert.equal(document.querySelector(".thinking"), null, "the spinner is gone");
    assert.equal(document.querySelector("[data-role='timer']"), null, "the ticker is cleaned up");

    const card = document.querySelector(".bot-card");
    assert.ok(card.textContent.includes("20 kg"), card.textContent.slice(0, 200));
    assert.equal(card.querySelectorAll(".spec-card").length, 1);
    assert.ok(card.querySelector(".spec-value").textContent.includes("20"));
    assert.equal(card.querySelectorAll(".figure-card").length, 1);
    assert.equal(card.querySelectorAll(".label-chip").length, 2);
    assert.ok(card.querySelector(".labels-title").textContent.includes("Labels"));
    assert.equal(card.querySelectorAll(".source-card").length, 1);
    assert.equal(card.querySelectorAll(".cite").length, 1);
    assert.ok(card.querySelector(".verify-badge").className.includes("verify-badge--good"));
    assert.ok(card.textContent.includes("Exact lookup"), "the route is disclosed");
    assert.ok(card.textContent.includes("Deutsch"), "the answer language is disclosed");
    assert.ok(card.textContent.includes("offline formatter"), "the generator is disclosed");
    assert.ok(card.textContent.includes("137 ms"), "the latency is disclosed");

    // figure URLs are normalised to this origin (preview-proxy safe)
    const img = card.querySelector(".figure-img");
    assert.equal(img.getAttribute("src"), "/media/flashbot_user_manual/images/de-p006R-fig01.png");
  });

  test("the conversation was saved to localStorage and listed in the sidebar", () => {
    const stored = JSON.parse(window.localStorage.getItem("telmekon.ui.v1"));
    assert.equal(stored.conversations.length, 1);
    assert.equal(stored.conversations[0].title, "Wieviel Zuladung hat der FlashBot?");
    assert.equal(stored.conversations[0].messages.length, 2);
    assert.equal(stored.conversations[0].messages[1].search, undefined, "the raw /search body is not persisted");
    assert.equal(byId("conv-count").textContent, "1");
    assert.ok(byId("conv-list").textContent.includes("Wieviel Zuladung"), byId("conv-list").textContent);
  });

  test("the verification badge expands its evidence", () => {
    const badge = document.querySelector(".verify-badge");
    const panel = document.getElementById(badge.getAttribute("aria-controls"));
    assert.equal(badge.getAttribute("aria-expanded"), "false");
    assert.equal(panel.hidden, true);
    click(badge);
    assert.equal(badge.getAttribute("aria-expanded"), "true");
    assert.equal(document.getElementById(badge.getAttribute("aria-controls")).hidden, false);
    assert.ok(panel.textContent.includes("2/2"), panel.textContent);
    click(document.querySelector(".verify-badge"));
    assert.equal(document.querySelector(".verify-badge").getAttribute("aria-expanded"), "false");
  });

  test("a [1] marker opens the citation modal, Esc closes it and restores focus", async () => {
    const marker = document.querySelector(".cite");
    marker.focus();
    click(marker);
    assert.equal(byId("citation-overlay").hidden, false);
    const body = byId("citation-body");
    assert.ok(body.textContent.includes("FlashBot User Manual"), body.textContent);
    assert.ok(body.textContent.includes("§2.3"), body.textContent);
    assert.ok(body.textContent.includes("p. 5"), body.textContent);
    assert.ok(body.textContent.includes("Zuladung: 20 kg"), body.textContent);
    assert.match(body.querySelector(".cit-badge--kind").textContent, /Parameter row/);

    key(document, "Escape");
    await settle();
    assert.equal(byId("citation-overlay").hidden, true);
    assert.equal(document.activeElement, marker, "focus returns to the marker that opened it");
  });

  test("a source card opens the same modal", () => {
    click(document.querySelector(".source-card"));
    assert.equal(byId("citation-overlay").hidden, false);
    click(byId("citation-overlay").querySelector("[data-close]"));
    assert.equal(byId("citation-overlay").hidden, true);
  });

  test("a citation number the payload does not contain says so", () => {
    const fake = document.createElement("button");
    fake.className = "cite";
    fake.dataset.ref = "9";
    document.querySelector(".bot-card").appendChild(fake);
    click(fake);
    assert.equal(byId("citation-overlay").hidden, false);
    const text = byId("citation-body").textContent;
    assert.match(text, /No source 9 in this response/);
    assert.match(text, /cites \[9\]/);
    assert.match(text, /1 citation\(s\): 1/);
    assert.equal(byId("citation-body").querySelectorAll(".cit-row").length, 0, "no fake locator rows");
    key(document, "Escape");
    fake.remove();
  });

  test("the figure opens in the lightbox with its Labels legend", () => {
    click(document.querySelector(".figure-img-btn"));
    assert.equal(byId("lightbox").hidden, false);
    assert.equal(byId("lightbox-img").getAttribute("src"), "/media/flashbot_user_manual/images/de-p006R-fig01.png");
    assert.equal(byId("lightbox-caption").textContent, "FlashBot Komponenten");
    assert.equal(byId("lightbox-labels").hidden, false);
    assert.equal(byId("lightbox-labels").querySelectorAll(".label-chip").length, 2);
    assert.ok(byId("lightbox-labels").textContent.includes("Labels"));
    key(document, "Escape");
    assert.equal(byId("lightbox").hidden, true);
  });

  test("the raw drawer shows GET /search, then the POST /ask record", async () => {
    click(document.querySelector('[data-action="raw"]'));
    assert.equal(byId("drawer").hidden, false);
    assert.equal(byId("drawer-backdrop").hidden, false);
    assert.ok(byId("drawer-url").textContent.startsWith("/search?q="), byId("drawer-url").textContent);
    assert.ok(byId("drawer-json").textContent.includes('"route"'), byId("drawer-json").textContent.slice(0, 120));
    assert.ok(byId("drawer-meta").textContent.includes("hits"), byId("drawer-meta").textContent);
    assert.ok(byId("drawer-json").querySelector(".j-key"), "the JSON is syntax-highlighted");

    click(byId("tab-ask"));
    await settle();
    assert.equal(byId("drawer-url").textContent, "POST /ask");
    const text = byId("drawer-json").textContent;
    assert.ok(text.includes('"request"'), text.slice(0, 120));
    assert.ok(text.includes('"response"'), text.slice(0, 200));
    assert.ok(text.includes("Wieviel Zuladung"), "the exact question that was sent");

    click(byId("tab-search"));
    click(byId("drawer-backdrop"));
    await settle();
    assert.equal(byId("drawer").hidden, true);
    assert.equal(byId("drawer-backdrop").hidden, true);
  });

  test("“Re-run live” really calls GET /search again", async () => {
    click(document.querySelector('[data-action="raw"]'));
    net.calls.length = 0;
    click(byId("drawer-rerun"));
    await settle(8);
    assert.ok(net.calls.some((call) => call.url.startsWith("/search?q=")), JSON.stringify(net.calls));
    key(document, "Escape");
    await settle();
  });

  test("feedback is recorded locally and labelled as local-only", () => {
    click(document.querySelector('[data-action="feedback"][data-value="up"]'));
    const pressed = document.querySelector('[data-action="feedback"][data-value="up"]');
    assert.equal(pressed.getAttribute("aria-pressed"), "true");
    assert.ok(pressed.className.includes("is-active"));
    assert.match(byId("toast").textContent, /no feedback endpoint/);
    const stored = JSON.parse(window.localStorage.getItem("telmekon.ui.v1"));
    assert.equal(stored.conversations[0].messages[1].feedback, "up");
    // toggling the same vote again clears it
    click(document.querySelector('[data-action="feedback"][data-value="up"]'));
    assert.equal(document.querySelector('[data-action="feedback"][data-value="up"]').getAttribute("aria-pressed"), "false");
  });

  test("the theme switch changes <html data-theme> and is remembered", () => {
    assert.equal(document.documentElement.getAttribute("data-theme"), "light");
    click(document.querySelector('[data-theme-set="dark"]'));
    assert.equal(document.documentElement.getAttribute("data-theme"), "dark");
    assert.equal(document.querySelector('[data-theme-set="dark"]').getAttribute("aria-pressed"), "true");
    assert.equal(document.querySelector('[data-theme-set="light"]').getAttribute("aria-pressed"), "false");
    assert.equal(JSON.parse(window.localStorage.getItem("telmekon.ui.v1")).settings.theme, "dark");
    click(document.querySelector('[data-theme-set="light"]'));
    assert.equal(document.documentElement.getAttribute("data-theme"), "light");
  });

  test("settings exposes only fields the API accepts, and applies them", async () => {
    click(byId("open-settings"));
    assert.equal(byId("settings-overlay").hidden, false);
    const body = byId("settings-body");
    assert.ok(body.querySelector('[data-setting-select="language"]'));
    assert.ok(body.querySelector('[data-setting-range="k"]'));
    assert.ok(body.querySelector('[data-toggle="followUserLanguage"]'));
    assert.ok(body.querySelector('[data-toggle="crossLingualFallback"]'));
    assert.match(body.textContent, /Not supported by the API yet/);
    assert.match(body.textContent, /no endpoint/);
    assert.ok(!body.innerHTML.includes("apiKey"), "no key field anywhere");

    // the catalog select is filled from GET /products
    await settle(6);
    const options = [...body.querySelectorAll('[data-setting-select="product"] option')].map((o) => o.value);
    assert.deepEqual(options, ["", "BellaBot", "FlashBot"]);

    const range = body.querySelector('[data-setting-range="k"]');
    range.value = "12";
    range.dispatchEvent(new window.Event("input", { bubbles: true }));
    assert.equal(JSON.parse(window.localStorage.getItem("telmekon.ui.v1")).settings.k, 12);
    assert.equal(byId("set-k-value").textContent, "12");

    const select = body.querySelector('[data-setting-select="product"]');
    select.value = "FlashBot";
    select.dispatchEvent(new window.Event("change", { bubbles: true }));
    assert.equal(JSON.parse(window.localStorage.getItem("telmekon.ui.v1")).settings.product, "FlashBot");

    key(document, "Escape");
    await settle();
    assert.equal(byId("settings-overlay").hidden, true);

    // the active scope is visible above the composer, and removable
    assert.equal(byId("scope-row").hidden, false);
    assert.ok(byId("scope-row").textContent.includes("FlashBot"), byId("scope-row").textContent);
    click(byId("scope-row").querySelector('[data-key="product"]'));
    assert.equal(byId("scope-row").hidden, true);
    assert.equal(JSON.parse(window.localStorage.getItem("telmekon.ui.v1")).settings.product, "");
    assert.equal(JSON.parse(window.localStorage.getItem("telmekon.ui.v1")).settings.k, 12);
  });

  test("the next question carries the changed evidence depth", async () => {
    net.calls.length = 0;
    byId("input").value = "How long does it take to charge the BellaBot?";
    byId("input").dispatchEvent(new window.Event("input", { bubbles: true }));
    key(byId("input"), "Enter");
    await settle(10);
    const body = JSON.parse(net.calls.find((call) => call.method === "POST").body);
    assert.equal(body.k, 12);
    assert.equal(document.querySelectorAll(".bot-card").length, 2);
    assert.equal(byId("conv-count").textContent, "1", "still one conversation, two questions");
  });

  test("Shift+Enter inserts a newline instead of sending", () => {
    const before = document.querySelectorAll(".bot-card").length;
    byId("input").value = "line one";
    byId("input").dispatchEvent(new window.Event("input", { bubbles: true }));
    key(byId("input"), "Enter", { shiftKey: true });
    assert.equal(document.querySelectorAll(".bot-card").length, before);
    byId("input").value = "";
    byId("input").dispatchEvent(new window.Event("input", { bubbles: true }));
  });

  test("“New question” clears the thread back to the welcome screen", () => {
    click(byId("new-chat"));
    assert.equal(byId("chat").hidden, true);
    assert.equal(byId("welcome").hidden, false);
    assert.equal(byId("conv-count").textContent, "2");
    assert.equal(byId("chat-title").textContent, "Telmekon AI Assistant");
  });

  test("selecting a saved conversation restores its answers from localStorage", () => {
    const items = [...document.querySelectorAll(".conv-item")];
    assert.equal(items.length, 2);
    click(items[items.length - 1].querySelector(".conv-open"));      // the older one
    assert.equal(byId("chat").hidden, false);
    assert.equal(document.querySelectorAll(".bot-card").length, 2);
    assert.ok(document.querySelector(".bot-card").textContent.includes("20 kg"));
    assert.equal(byId("chat-title").textContent, "Wieviel Zuladung hat der FlashBot?");
  });

  test("the sidebar filter narrows the list locally", () => {
    byId("conv-search").value = "BellaBot";
    byId("conv-search").dispatchEvent(new window.Event("input", { bubbles: true }));
    assert.equal(byId("conv-count").textContent, "1");
    byId("conv-search").value = "zzz-no-match";
    byId("conv-search").dispatchEvent(new window.Event("input", { bubbles: true }));
    assert.equal(byId("conv-count").textContent, "0");
    assert.match(byId("conv-list").textContent, /No saved conversations/);
    byId("conv-search").value = "";
    byId("conv-search").dispatchEvent(new window.Event("input", { bubbles: true }));
    assert.equal(byId("conv-count").textContent, "2");
  });

  test("deleting a conversation offers an undo that really restores it", () => {
    const countBefore = Number(byId("conv-count").textContent);
    click(document.querySelector('[data-action="delete-conv"]'));
    assert.equal(Number(byId("conv-count").textContent), countBefore - 1);
    assert.match(byId("toast").textContent, /Deleted/);
    click(byId("toast").querySelector(".toast-action"));
    assert.equal(Number(byId("conv-count").textContent), countBefore);
  });

  test("a 503 renders the server's own reason and fix, with a working retry", async () => {
    net.mode = "index-missing";
    click(byId("new-chat"));
    byId("input").value = "Wieviel Zuladung hat der FlashBot?";
    byId("input").dispatchEvent(new window.Event("input", { bubbles: true }));
    click(byId("send"));
    await settle(12);

    const card = document.querySelector(".error-card");
    assert.ok(card, "an error card is rendered");
    assert.equal(card.getAttribute("role"), "alert");
    assert.ok(card.textContent.includes("search index not built"), card.textContent);
    assert.ok(card.textContent.includes("python -m ingest.run --data data"), card.textContent);
    assert.ok(card.querySelector('[data-action="retry"]'));
    // the health banner now explains the degraded index
    assert.equal(byId("health-banner").hidden, false);
    assert.match(byId("health-banner").textContent, /not built/);
    assert.match(byId("status-label").textContent, /index not built/);

    net.mode = "ok";
    click(card.querySelector('[data-action="retry"]'));
    await settle(12);
    assert.equal(document.querySelectorAll(".error-card").length, 0, "the failed card is replaced, not stacked");
    assert.ok(document.querySelector(".bot-card").textContent.includes("20 kg"));
    assert.equal(document.querySelectorAll(".msg--user").length, 1, "retry did not duplicate the question");
    assert.equal(byId("health-banner").hidden, true);
  });

  test("an unreachable API says how to start it", async () => {
    net.mode = "offline";
    click(byId("new-chat"));
    byId("input").value = "payload?";
    byId("input").dispatchEvent(new window.Event("input", { bubbles: true }));
    click(byId("send"));
    await settle(12);
    const card = document.querySelector(".error-card");
    assert.match(card.textContent, /Cannot reach the answer engine/);
    assert.match(card.textContent, /python -m api/);
    await settle(4);
    assert.match(byId("status-label").textContent, /API offline/);
    net.mode = "ok";
  });

  test("an answer with no evidence shows the neutral badge, never a green one", async () => {
    net.mode = "no-evidence";
    click(byId("new-chat"));
    byId("input").value = "What is the value of this parameter?";
    byId("input").dispatchEvent(new window.Event("input", { bubbles: true }));
    click(byId("send"));
    await settle(12);
    const badge = document.querySelector(".verify-badge");
    assert.ok(badge.className.includes("verify-badge--none"), badge.className);
    assert.ok(!badge.className.includes("verify-badge--good"), badge.className);
    assert.match(badge.textContent, /Not answered from the manuals/);
    click(badge);
    assert.match(document.getElementById(badge.getAttribute("aria-controls")).textContent, /nothing to certify/);
    assert.equal(document.querySelectorAll(".source-card").length, 0, "no sources are invented");
    net.mode = "ok";
  });

  test("a figure that fails to load is replaced by a sentence, not a broken icon", async () => {
    // The previous scenarios left a refusal on screen, so ask once more (the
    // stub answers every question with the same figure-bearing payload).
    net.mode = "ok";
    click(byId("new-chat"));
    byId("input").value = "Show me a figure";
    byId("input").dispatchEvent(new window.Event("input", { bubbles: true }));
    click(byId("send"));
    await settle(12);
    const card = document.querySelector(".figure-card");
    assert.ok(card, "there is a figure card from the answer that just arrived");
    const img = card.querySelector(".figure-img");
    const src = img.getAttribute("src");
    img.dispatchEvent(new window.Event("error", { bubbles: false }));
    assert.equal(card.querySelector("img"), null, "the broken image is gone");
    const note = card.querySelector(".figure-missing");
    assert.ok(note, "a note takes its place");
    assert.match(note.textContent, /Figure not available/);
    assert.ok(note.textContent.includes(src), note.textContent);
    assert.match(byId("toast").textContent, /could not be loaded/);
    assert.equal(card.querySelectorAll(".label-chip").length, 2, "the Labels legend survives");
  });

  test("Ctrl+K jumps to the question box", () => {
    byId("new-chat").focus();
    key(document, "k", { ctrlKey: true });
    assert.equal(document.activeElement, byId("input"));
  });

  test("nothing in the rendered DOM leaked unescaped markup", () => {
    assert.equal(document.querySelectorAll(".bot-card script").length, 0);
    assert.equal(document.querySelectorAll("iframe").length, 0);
    for (const attribute of document.querySelectorAll("[onclick]")) assert.fail(`inline handler: ${attribute.outerHTML}`);
  });
});
