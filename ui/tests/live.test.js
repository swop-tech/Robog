/* Live integration test — the UI against a *running* Robog API.
 *
 * Everything else in `ui/tests/` runs against stubs. This file is the proof that
 * the interface really works end to end: it boots `index.html` in jsdom, points
 * `fetch` at a live server, clicks the four starter prompts and asserts that the
 * rendered DOM contains the evidence that server actually returned.
 *
 * It is opt-in, because it needs both a built index and a running service:
 *
 *     python -m ingest.run --data data          # once
 *     python -m api                             # http://0.0.0.0:8000
 *     npm install --no-save jsdom               # dev-only, not a dependency
 *     ROBOG_LIVE_API=http://localhost:8000 node --test ui/tests/live.test.js
 *
 * Without `ROBOG_LIVE_API` (or without jsdom) the whole suite is skipped, so
 * `node --test ui/tests/*.test.js` stays green in CI and on a fresh clone.
 *
 * Nothing here is asserted about answer *wording* — that is the corpus's and the
 * formatter's business (`answer/evaluate.py`). These tests assert the contract
 * the UI depends on: real sources, real locators, real figures, and a badge that
 * never claims more verification than the response reports.
 */

import assert from "node:assert/strict";
import test, { after, before, describe } from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const BASE = process.env.ROBOG_LIVE_API || "";
let JSDOM = null;
try {
  ({ JSDOM } = await import("jsdom"));
} catch {
  JSDOM = null;
}

const SKIP = !BASE
  ? "set ROBOG_LIVE_API=http://localhost:8000 (and run `python -m api`) to enable"
  : !JSDOM
    ? "jsdom is not installed (npm install --no-save jsdom)"
    : false;

const UI = dirname(dirname(fileURLToPath(import.meta.url)));
/** Captured before the app overrides `globalThis.fetch` (which would recurse). */
const nodeFetch = globalThis.fetch;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let GLOBAL_KEYS = [];

function setGlobal(name, value) {
  try {
    globalThis[name] = value;
  } catch {
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  }
}

/** The four scenarios from the brief, and what each one must put on screen. */
const SCENARIOS = [
  {
    prompt: "Wieviel Zuladung hat der FlashBot?",
    label: "exact value (technician persona, German)",
    expect: (card) => {
      assert.ok(card.querySelectorAll(".spec-card").length >= 1, "a parameter row is rendered");
      const value = card.querySelector(".spec-value").textContent.trim();
      assert.ok(value.length > 0, "the value is not empty");
      assert.match(card.querySelector(".spec-source").textContent, /FlashBot/i);
      assert.match(card.querySelector(".spec-source").textContent, /p\.\s?\d+/, "the page is cited");
      assert.ok(!/\bkg\s+kg\b|\bm\/s\s+m\/s\b/.test(value), `unit duplicated: ${value}`);
    },
  },
  {
    prompt: "Which robot fits a busy restaurant best?",
    label: "comparison (non-technical persona)",
    expect: (card) => {
      assert.ok(card.querySelectorAll(".profile-card").length >= 2, "several product cards are rendered");
      const names = [...card.querySelectorAll(".profile-name")].map((n) => n.textContent.trim());
      assert.ok(names.every(Boolean), names.join("|"));
      assert.ok(card.querySelector(".profile-specs").textContent.length > 10, "the cards carry specs");
      assert.ok(!card.textContent.includes("% match") || /\b\d{1,2}% match|\b100% match/.test(card.textContent),
        "a fused score is never shown as an impossible percentage");
      assert.ok(card.querySelectorAll(".source-card").length >= 3, "the comparison is cited");
    },
  },
  {
    prompt: "Qual è la velocità massima del FlashBot?",
    label: "cross-lingual (Bonus 02, Italian)",
    expect: (card) => {
      assert.ok(card.querySelector(".meta-badge--lang").textContent.includes("Italiano"), "answered in Italian");
      assert.ok(card.querySelectorAll(".spec-card").length >= 1, "the value is rendered");
      assert.match(card.querySelector(".spec-value").textContent, /m\/s|\d/);
    },
  },
  {
    prompt: "Show me the component diagram of BellaBot",
    label: "figure route (image + numbered labels)",
    expect: (card) => {
      assert.ok(card.querySelectorAll(".figure-card").length >= 1, "a figure is rendered");
      const img = card.querySelector(".figure-img");
      assert.ok(img, "the figure has an image");
      assert.ok(img.getAttribute("src").startsWith("/media/"), `same-origin media path: ${img.getAttribute("src")}`);
      assert.ok(card.querySelectorAll(".label-chip").length >= 3, "the numbered Labels legend is rendered");
      assert.match(card.querySelector(".labels-title").textContent, /Labels/);
    },
  },
];

describe("live API: the interface end to end", { skip: SKIP }, () => {
  let window, document, errors;
  const net = { calls: [] };

  before(async () => {
    // The server must be up and its index built, or every scenario would fail
    // for the wrong reason.
    const health = await nodeFetch(new URL("/health", BASE)).then((r) => r.json()).catch(() => null);
    assert.ok(health, `${BASE}/health did not answer — start it with \`python -m api\``);
    assert.equal(health.ready, true, `the index is not built: ${health.vector_detail || health.detail}`);

    const dom = new JSDOM(readFileSync(join(UI, "index.html"), "utf8"), {
      url: BASE.endsWith("/") ? BASE : `${BASE}/`,
      runScripts: "outside-only",
      pretendToBeVisual: true,
    });
    window = dom.window;
    document = window.document;
    errors = [];

    window.matchMedia = (query) => ({
      matches: false, media: query, onchange: null,
      addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false,
    });
    window.scrollTo = () => {};
    window.Element.prototype.scrollTo = function scrollTo() {};
    window.Element.prototype.scrollIntoView = function scrollIntoView() {};
    if (!window.CSS) window.CSS = {};
    if (!window.CSS.escape) window.CSS.escape = (v) => String(v).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`);
    window.HTMLElement.prototype.getClientRects = function getClientRects() { return [{}]; };
    window.addEventListener("error", (event) => errors.push(String(event.error || event.message)));
    const realSetInterval = globalThis.setInterval;
    globalThis.setInterval = (fn, ms, ...args) => {
      const timer = realSetInterval(fn, ms, ...args);
      timer.unref?.();
      return timer;
    };

    // Relative URLs from api.js, resolved against the live server.
    const liveFetch = (url, init) => {
      net.calls.push({ url: String(url), method: init?.method || "GET" });
      return nodeFetch(String(url).startsWith("/") ? new URL(url, BASE) : url, init);
    };

    for (const [name, value] of Object.entries({
      window, document, navigator: window.navigator, localStorage: window.localStorage,
      matchMedia: window.matchMedia, CSS: window.CSS, HTMLElement: window.HTMLElement,
      Element: window.Element, Node: window.Node, Event: window.Event,
      MouseEvent: window.MouseEvent, KeyboardEvent: window.KeyboardEvent, fetch: liveFetch,
    })) {
      setGlobal(name, value);
    }
    GLOBAL_KEYS = ["window", "document", "navigator", "localStorage", "matchMedia", "CSS",
      "HTMLElement", "Element", "Node", "Event", "MouseEvent", "KeyboardEvent", "fetch"];

    if (document.readyState === "loading") {
      await new Promise((resolve) => window.addEventListener("DOMContentLoaded", resolve, { once: true }));
    }
    await import("../app.js");
    await sleep(600);                                  // let GET /health land
  });

  after(() => {
    window?.close();
    for (const key of GLOBAL_KEYS) setGlobal(key, undefined);
  });

  const byId = (id) => document.getElementById(id);
  const click = (element) => {
    assert.ok(element, "element to click must exist");
    element.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  };

  test("the page reports the live index status from GET /health", () => {
    assert.ok(byId("status").className.includes("status--ready"), byId("status").className);
    assert.match(byId("status-label").textContent, /ready/);
    assert.match(byId("side-status").title, /\d+ chunks/, byId("side-status").title);
    assert.equal(byId("health-banner").hidden, true, "no degraded banner against a ready index");
  });

  for (const scenario of SCENARIOS) {
    test(`${scenario.label}: ${scenario.prompt}`, async () => {
      click(byId("new-chat"));
      byId("input").value = scenario.prompt;
      byId("input").dispatchEvent(new window.Event("input", { bubbles: true }));

      net.calls.length = 0;
      click(byId("send"));
      assert.ok(document.querySelector(".thinking"), "a pending state is shown");

      // A real retrieval + generation round trip: give it room, but not forever.
      for (let i = 0; i < 120 && document.querySelector(".thinking"); i += 1) await sleep(250);

      assert.equal(document.querySelector(".error-card"), null,
        document.querySelector(".error-card")?.textContent || "the question failed");
      const card = document.querySelector(".bot-card");
      assert.ok(card, "an answer card is rendered");

      const methods = net.calls.map((call) => `${call.method} ${call.url.split("?")[0]}`);
      assert.ok(methods.some((m) => m.startsWith("POST")), methods.join(", "));
      assert.ok(methods.some((m) => m.includes("/search")), methods.join(", "));

      assert.ok(card.querySelector(".answer").textContent.trim().length > 10, "the answer has text");
      assert.ok(card.querySelectorAll(".source-card").length >= 1, "at least one real source is listed");
      scenario.expect(card);

      // The badge may not claim more than the response reports.
      const badge = card.querySelector(".verify-badge");
      assert.ok(badge, "the verification badge is rendered");
      const expanded = badge.className.includes("verify-badge--good");
      if (expanded) {
        assert.match(badge.textContent, /Verified/);
        assert.match(badge.textContent, /\d+\/\d+ cited/, badge.textContent);
      }

      // Every source card opens a real locator in the modal.
      click(card.querySelector(".source-card"));
      assert.equal(byId("citation-overlay").hidden, false);
      const modal = byId("citation-body").textContent;
      assert.match(modal, /p\.\s?\d+|§/, `the modal shows where it came from: ${modal.slice(0, 120)}`);
      assert.ok(!modal.includes("No detail available"), modal.slice(0, 160));
      click(byId("citation-overlay").querySelector("[data-close]"));

      // The raw drawer shows what the live /search really returned.
      click(card.querySelector('[data-action="raw"]'));
      assert.equal(byId("drawer").hidden, false);
      assert.match(byId("drawer-url").textContent, /^\/search\?q=/);
      assert.match(byId("drawer-json").textContent, /"route"/);
      assert.ok(!byId("drawer-json").textContent.includes("Not loaded"), byId("drawer-json").textContent.slice(0, 120));
      click(byId("drawer").querySelector("[data-close]"));

      // A figure, when there is one, opens in the lightbox from the same origin.
      const figureButton = card.querySelector(".figure-img-btn");
      if (figureButton) {
        click(figureButton);
        assert.equal(byId("lightbox").hidden, false);
        assert.ok(byId("lightbox-img").getAttribute("src").startsWith("/media/"));
        click(byId("lightbox").querySelector("[data-close]"));
      }
    });
  }

  test("a question the manuals cannot answer never shows a green badge", async () => {
    click(byId("new-chat"));
    byId("input").value = "What is the value of this parameter?";
    byId("input").dispatchEvent(new window.Event("input", { bubbles: true }));
    click(byId("send"));
    for (let i = 0; i < 80 && document.querySelector(".thinking"); i += 1) await sleep(250);

    const card = document.querySelector(".bot-card");
    assert.ok(card, "the refusal is still rendered as an answer card");
    const badge = card.querySelector(".verify-badge");
    assert.ok(badge, "the badge is rendered");
    assert.ok(!badge.className.includes("verify-badge--good"),
      `a deictic question must not be badged Verified: ${badge.textContent}`);
    assert.equal(card.querySelectorAll(".spec-card").length, 0, "no value is invented");
  });

  test("no runtime error was thrown while driving the live app", () => {
    assert.deepEqual(errors, []);
  });
});
