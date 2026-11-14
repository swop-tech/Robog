/* Contract tests for the payload -> HTML layer.
 *
 * The fixtures below are shaped exactly like the responses of `POST /ask` and
 * `GET /health` (`api/service.py`: `_ask_payload`, `_param_row`, `_verification`,
 * `answer/composer.py`: `_citations`, `_figures`). A field the API does not send
 * is not invented here, and the tests assert that a missing field renders as
 * *nothing* rather than as a placeholder that could be mistaken for data.
 *
 *   node --test ui/tests/render.test.js
 */

import assert from "node:assert/strict";
import test, { describe } from "node:test";

import {
  assistantCardHTML,
  citationModalHTML,
  conversationListHTML,
  crossLingualBadge,
  drawerBodyHTML,
  errorCardHTML,
  fallbackNoteHTML,
  latencyTitle,
  renderFigures,
  renderProfiles,
  renderSources,
  renderSpecs,
  renderVerification,
  statusChipHTML,
  userMessageHTML,
} from "../render.js";

/** `mediaUrl` as api.js implements it: keep only path + query (proxy-safe). */
const mediaUrl = (url) => {
  if (!url) return null;
  const parsed = new URL(url, "http://local");
  return parsed.pathname + parsed.search;
};

const verification = {
  grounded: true,
  verified: true,
  checked: true,
  state: "verified",
  summary: "All 8 claims cited; all 72 numbers verified.",
  citation_rate: 1.0,
  numeric_rate: 1.0,
  quote_rate: 1.0,
  sentences_checked: 8,
  sentences_cited: 8,
  numbers_checked: 72,
  numbers_verified: 72,
  numbers_unverified: 0,
  quotes_checked: 0,
  quotes_verified: 0,
  issues: [],
};

const paramRow = {
  parameter: "Zuladung",
  value: "20",
  unit: "kg",
  value_number: 20.0,
  column: "Zuladung",
  product: "FlashBot",
  language: "de",
  page: 5,
  section: "2.3 Technische Daten",
  section_number: "2.3",
  chunk_id: "flashbot-de-0042",
  citation: "FlashBot User Manual §2.3 p.5 (DE)",
};

const citation = {
  n: 1,
  kind: "param",
  citation: "FlashBot User Manual §2.3 p.5 (DE)",
  language: "de",
  product: "FlashBot",
  page: 5,
  section: "2.3",
  title: "FlashBot User Manual",
  excerpt: "Zuladung: 20 kg (10 kg pro Ablage).",
  image: null,
  image_url: null,
};

const figure = {
  image: "images/en-p018L-fig01.png",
  language: "en",
  product: "BellaBot",
  caption: "BellaBot components",
  labels: ["1 Head", "2 Tray", "3 Emergency stop"],
  image_url: "http://127.0.0.1:8000/media/bellabot_user_manual_multilanguage/images/en-p018L-fig01.png",
};

const profile = {
  id: "profile-bellabot-en",
  product: "BellaBot",
  category: "delivery",
  language: "en",
  dimensions: "565 mm×537 mm×1290 mm",
  weight: "55 kg",
  payload: "10 kg/tray (4)",
  trays_or_compartments: "4",
  battery_runtime: "25.6 Ah / 12–24 h",
  charging_time: "4.5 h",
  speed: "0.2–1.2 m/s (adjustable)",
  min_passage_width: "70 cm",
  navigation: "Laser and optical SLAM",
  cleaning_efficiency: null,
  tank_or_dustbin: null,
  special_features: [],
  environments: ["indoor_delivery", "restaurant"],
  key_use_case: "BellaBot is an indoor delivery robot.",
  image: "images/en-p018L-fig01.png",
  image_url: "http://127.0.0.1:8000/media/bellabot/images/en-p018L-fig01.png",
  citation: "BellaBot User Manual p.18 (EN)",
  score: 0.8123,
  cross_lingual: false,
};

const message = {
  id: "a-1",
  role: "assistant",
  question: "Wieviel Zuladung hat der FlashBot?",
  createdAt: "2026-11-14T09:12:00.000Z",
  feedback: null,
  request: { question: "Wieviel Zuladung hat der FlashBot?", k: 8 },
  payload: {
    question: "Wieviel Zuladung hat der FlashBot?",
    answer: "Der FlashBot trägt **20 kg** Zuladung, 10 kg pro Ablage [1].",
    language: "de",
    route: "lookup",
    citations: [citation],
    figures: [],
    params: [paramRow],
    profiles: [],
    verification,
    cross_lingual: false,
    provider: "offline",
    model: null,
    latency_ms: 143,
    source_languages: ["de"],
  },
};

describe("renderSpecs — the exact value a technician reads off", () => {
  test("value, unit and the full locator are rendered", () => {
    const html = renderSpecs([paramRow]);
    assert.match(html, /class="spec-name">Zuladung</);
    assert.match(html, /class="spec-value">20<span class="spec-unit">kg</);
    assert.ok(html.includes("FlashBot"), html);
    assert.ok(html.includes("§2.3"), html);
    assert.ok(html.includes("p. 5"), html);
    assert.ok(html.includes("DE"), html);
  });

  test("a row without a unit renders no empty unit element", () => {
    assert.ok(!renderSpecs([{ ...paramRow, unit: null }]).includes("spec-unit"));
  });

  test("a value that already contains its unit is not doubled", () => {
    // Exactly what the live parameter index returns for the German manuals.
    const html = renderSpecs([
      { ...paramRow, parameter: "Ladekapazität", value: "20 kg (10 kg pro Tablett)", unit: "kg" },
    ]);
    assert.ok(html.includes("20 kg (10 kg pro Tablett)</p>"), html);
    assert.ok(!html.includes("spec-unit"), html);
    assert.ok(!html.includes("Tablett) kg"), html);
    const speed = renderSpecs([{ ...paramRow, parameter: "Max. moving speed", value: "1.2 m/s", unit: "m/s" }]);
    assert.ok(!speed.includes("m/s m/s"), speed);
    assert.ok(!speed.includes("spec-unit"), speed);
  });

  test("a plain number still gets its unit", () => {
    const html = renderSpecs([{ ...paramRow, value: "4.5", unit: "h" }]);
    assert.match(html, /class="spec-value">4\.5<span class="spec-unit">h</);
  });

  test("no params -> no block at all (never an empty grid)", () => {
    assert.equal(renderSpecs([]), "");
    assert.equal(renderSpecs(undefined), "");
    assert.equal(renderSpecs(null), "");
  });

  test("a row missing every locator part renders no source line", () => {
    const html = renderSpecs([{ parameter: "Payload", value: "20" }]);
    assert.ok(!html.includes("spec-source"), html);
  });
});

describe("renderFigures — the manual image plus its numbered legend", () => {
  test("the absolute image_url is normalised to a same-origin path", () => {
    const html = renderFigures([figure], { mediaUrl });
    assert.ok(html.includes('src="/media/bellabot_user_manual_multilanguage/images/en-p018L-fig01.png"'), html);
    assert.ok(!html.includes("127.0.0.1"), html);
  });

  test("the legend is titled Labels and numbers each callout", () => {
    const html = renderFigures([figure], { mediaUrl });
    assert.match(html, /class="labels-title">Labels</);
    assert.match(html, /class="figure-labels"/);
    assert.equal((html.match(/class="label-chip"/g) || []).length, 3);
    assert.match(html, /<b>1<\/b>Head/);
    assert.match(html, /<b>3<\/b>Emergency stop/);
  });

  test("a label without a leading number is still shown, unnumbered", () => {
    const html = renderFigures([{ ...figure, labels: ["Tray sensor"] }], { mediaUrl });
    assert.match(html, /<li class="label-chip">Tray sensor<\/li>/);
  });

  test("image_url: null (the resolver could not find the document) renders no <img>", () => {
    const html = renderFigures([{ ...figure, image_url: null }], { mediaUrl });
    assert.ok(!html.includes("<img"), html);
    assert.ok(html.includes("BellaBot components"), html);   // the caption survives
  });

  test("no figures -> nothing", () => {
    assert.equal(renderFigures([]), "");
    assert.equal(renderFigures(undefined), "");
  });
});

describe("renderProfiles — the advisory persona's product cards", () => {
  test("only the specs the profile actually carries are rendered", () => {
    const html = renderProfiles([profile], { mediaUrl });
    assert.match(html, /class="profile-name">BellaBot</);
    assert.ok(html.includes("10 kg/tray (4)"), html);
    assert.ok(html.includes("70 cm"), html);
    assert.ok(html.includes("4.5 h"), html);
    // cleaning_efficiency and tank_or_dustbin are null in this profile
    assert.ok(!html.includes("Cleaning efficiency"), html);
    assert.ok(!html.includes("Tank / dustbin"), html);
    assert.ok(!html.includes(">null<"), html);
  });

  test("a 0..1 retrieval score is shown as a percentage", () => {
    assert.match(renderProfiles([profile], { mediaUrl }), /81% match/);
  });

  test("a fused score outside 0..1 is left out instead of becoming “856% match”", () => {
    // The live advisory route returns scores like 8.5608 (BM25 + RRF fusion).
    const html = renderProfiles([{ ...profile, score: 8.5608 }], { mediaUrl });
    assert.ok(!html.includes("profile-match"), html);
    assert.ok(!html.includes("856%"), html);
    assert.ok(!html.includes("NaN"), html);
  });

  test("environment tags are de-snake-cased, features stay empty when absent", () => {
    const html = renderProfiles([profile], { mediaUrl });
    assert.ok(html.includes("indoor delivery"), html);
    assert.ok(!html.includes("profile-features"), html);
  });

  test("the product photo goes through mediaUrl like every other figure", () => {
    const html = renderProfiles([profile], { mediaUrl });
    assert.ok(html.includes('data-lightbox="/media/bellabot/images/en-p018L-fig01.png"'), html);
    assert.ok(!html.includes("127.0.0.1"), html);
  });

  test("no profiles -> no section", () => {
    assert.equal(renderProfiles([]), "");
    assert.equal(renderProfiles(undefined), "");
  });
});

describe("renderVerification — the badge must never overclaim", () => {
  test("verified -> green, with the real counts", () => {
    const html = renderVerification(verification);
    assert.match(html, /verify-badge--good/);
    assert.match(html, />Verified</);
    assert.ok(html.includes("8/8 cited"), html);
    assert.ok(html.includes("72/72 numbers"), html);
  });

  test("checked:false is neutral, never green (0/0 passes vacuously)", () => {
    const html = renderVerification({ ...verification, checked: false, state: "no-evidence", verified: true });
    assert.match(html, /verify-badge--none/);
    assert.ok(!html.includes("verify-badge--good"), html);
    assert.match(html, />Not answered from the manuals</);
  });

  test("state:no-evidence alone is enough to refuse the green badge", () => {
    const html = renderVerification({ grounded: false, verified: false, checked: true, state: "no-evidence", summary: "" });
    assert.match(html, /verify-badge--none/);
  });

  test("grounded but not verified -> warning, failed -> error", () => {
    assert.match(renderVerification({ ...verification, verified: false, state: "repaired" }), /verify-badge--warn/);
    assert.match(renderVerification({ ...verification, grounded: false, verified: false, state: "failed" }), /verify-badge--bad/);
  });

  test("issues are listed with their check, action and message", () => {
    const html = renderVerification({
      ...verification,
      verified: false,
      issues: [{ check: "numeric", severity: "error", message: "42 not in evidence", detail: "", sentence: 1, action: "dropped" }],
    });
    assert.match(html, /class="issue issue--error"/);
    assert.ok(html.includes("numeric · dropped"), html);
    assert.ok(html.includes("42 not in evidence"), html);
  });

  test("the no-evidence panel explains itself instead of showing 0/0 bars", () => {
    const html = renderVerification({ ...verification, checked: false, state: "no-evidence", sentences_checked: 0, sentences_cited: 0 });
    assert.ok(!html.includes("rate-bar"), html);
    assert.ok(html.includes("nothing to certify"), html);
  });

  test("no verification object -> no badge", () => {
    assert.equal(renderVerification(undefined), "");
    assert.equal(renderVerification(null), "");
  });

  test("each message gets its own details id (no duplicate DOM ids)", () => {
    const a = renderVerification(verification, { id: "verify-a-1" });
    const b = renderVerification(verification, { id: "verify-a-2" });
    assert.match(a, /id="verify-a-1"/);
    assert.match(a, /aria-controls="verify-a-1"/);
    assert.match(b, /id="verify-a-2"/);
  });
});

describe("crossLingualBadge — Bonus 02 disclosed, not hidden", () => {
  test("it names the answer language and the source languages", () => {
    const html = crossLingualBadge({ language: "it", source_languages: ["de", "en"] });
    assert.match(html, /class="cross-badge"/);
    assert.ok(html.includes("Answer in Italiano"), html);
    assert.ok(html.includes("sources DE, EN"), html);
  });

  test("missing source_languages degrades to an honest phrase", () => {
    assert.ok(crossLingualBadge({ language: "it" }).includes("sources other languages"));
  });
});

describe("renderSources", () => {
  test("each citation becomes a button carrying its reference number", () => {
    const html = renderSources([citation]);
    assert.match(html, /data-ref="1"/);
    assert.ok(html.includes("FlashBot User Manual"), html);
    assert.ok(html.includes("Parameter row"), html);          // kind, not the raw slug
    assert.ok(html.includes("§2.3 · p. 5 · DE"), html);
    assert.ok(html.includes("Zuladung: 20 kg"), html);        // the excerpt
  });

  test("three or fewer sources start expanded, more start collapsed", () => {
    assert.match(renderSources([citation]), /<details class="sources" open/);
    const many = [1, 2, 3, 4].map((n) => ({ ...citation, n }));
    assert.ok(!renderSources(many).includes("<details class=\"sources\" open"), "should be collapsed");
  });

  test("an unknown kind falls back to the raw value instead of crashing", () => {
    assert.ok(renderSources([{ ...citation, kind: "weird" }]).includes("weird"));
  });

  test("no citations -> no sources block", () => {
    assert.equal(renderSources([]), "");
    assert.equal(renderSources(undefined), "");
  });
});

describe("citationModalHTML", () => {
  test("locator rows and the source snippet are shown", () => {
    const html = citationModalHTML(citation, { mediaUrl });
    assert.ok(html.includes("FlashBot User Manual"), html);
    assert.ok(html.includes("§2.3"), html);
    assert.ok(html.includes("p. 5"), html);
    assert.ok(html.includes("Zuladung: 20 kg (10 kg pro Ablage)."), html);
    assert.match(html, /class="cit-badge cit-badge--kind"/);
  });

  test("a citation with an image offers it for the lightbox", () => {
    const html = citationModalHTML({ ...citation, image: figure.image, image_url: figure.image_url }, { mediaUrl });
    assert.ok(html.includes('data-lightbox="/media/bellabot_user_manual_multilanguage/images/en-p018L-fig01.png"'), html);
  });

  test("a reference with no matching citation says so instead of showing blanks", () => {
    const html = citationModalHTML(null, { mediaUrl });
    assert.match(html, /No detail available for this reference/);
  });

  test("an issue shows the sentence it is about", () => {
    const html = renderVerification({
      ...verification,
      verified: false,
      issues: [{ check: "citation", severity: "warning", message: "no source cited for this sentence",
                 detail: "It only takes <b>a few</b> minutes.", sentence: 3, action: "kept" }],
    });
    assert.ok(html.includes("citation · kept"), html);
    assert.ok(html.includes("It only takes &lt;b&gt;a few&lt;/b&gt; minutes."), html);   // escaped
  });

  test("missing metadata renders an explicit dash inside the labelled grid", () => {
    const html = citationModalHTML({ ...citation, section: null, page: null }, { mediaUrl });
    assert.ok(html.includes("—"), html);
  });
});

describe("fallbackNoteHTML — why the answer is quoted passages", () => {
  test("the server's reason is shown on the card, escaped", () => {
    const reason = "the LLM call failed: the model 'gemini-x' is not available <for this key>";
    const html = assistantCardHTML(
      { ...message, payload: { ...message.payload, provider: "offline", fallback_reason: reason } },
      { mediaUrl },
    );
    assert.match(html, /class="fallback-note"/);
    assert.ok(html.includes("not written by the model"), html);
    assert.ok(html.includes("gemini-x"), html);
    assert.ok(html.includes("&lt;for this key&gt;"), html);
  });

  test("no note when the model wrote the answer or no reason was given", () => {
    assert.equal(fallbackNoteHTML({ provider: "gemini", fallback_reason: null }), "");
    assert.equal(fallbackNoteHTML({ provider: "gemini", fallback_reason: "stale" }), "");
    assert.equal(fallbackNoteHTML({ provider: "offline" }), "");       // asked back: deterministic by design
    assert.equal(fallbackNoteHTML(null), "");
  });
});

describe("assistantCardHTML", () => {
  test("route, language, provider and latency are disclosed as metadata", () => {
    const html = assistantCardHTML(message, { mediaUrl });
    assert.ok(html.includes("Exact lookup"), html);           // route label, not the raw slug
    assert.ok(html.includes("Deutsch"), html);
    assert.ok(html.includes("offline formatter"), html);
    assert.ok(html.includes("143 ms"), html);
  });

  test("the latency tooltip says where the time went", () => {
    const slow = { latency_ms: 4100, timings: { retrieval_ms: 12, generation_ms: 4060, llm_calls: 2 } };
    assert.equal(latencyTitle(slow), "Time to answer · retrieval 12 ms · model 4060 ms in 2 calls");
    assert.ok(latencyTitle({ timings: { retrieval_ms: 9, generation_ms: 0, llm_calls: 0 } }).includes("no model call"));
    assert.equal(latencyTitle({ latency_ms: 5 }), "Time to answer");        // an older server
    const html = assistantCardHTML({ ...message, payload: { ...message.payload, ...slow } }, { mediaUrl });
    assert.ok(html.includes("model 4060 ms in 2 calls"), html);
  });

  test("the answer is rendered as markdown with a clickable citation", () => {
    const html = assistantCardHTML(message, { mediaUrl });
    assert.match(html, /<strong>20 kg<\/strong>/);
    assert.match(html, /class="cite"[^>]*data-ref="1"/);
  });

  test("actions expose copy, regenerate, feedback and the raw drawer", () => {
    const html = assistantCardHTML(message, { mediaUrl });
    for (const action of ["copy", "regenerate", "feedback", "raw", "verify"]) {
      assert.ok(html.includes(`data-action="${action}"`), action);
    }
    assert.ok(html.includes("Raw <code>/search</code> JSON"), html);
    // feedback is local-only and the tooltip says so
    assert.ok(html.includes("the API has no feedback endpoint"), html);
  });

  test("a recorded vote is reflected in aria-pressed", () => {
    const html = assistantCardHTML({ ...message, feedback: "up" }, { mediaUrl });
    assert.match(html, /data-value="up"[^>]*aria-pressed="true"/);
    assert.match(html, /data-value="down"[^>]*aria-pressed="false"/);
  });

  test("profiles, params and figures all appear when the payload has them", () => {
    const html = assistantCardHTML(
      { ...message, payload: { ...message.payload, profiles: [profile], figures: [figure] } },
      { mediaUrl }
    );
    assert.ok(html.includes("profile-card"), html);
    assert.ok(html.includes("spec-card"), html);
    assert.ok(html.includes("figure-card"), html);
  });

  test("a payload that is missing whole sections still renders the answer", () => {
    const bare = { id: "a-2", createdAt: "", payload: { answer: "No evidence found.", route: "default" } };
    const html = assistantCardHTML(bare, { mediaUrl });
    assert.ok(html.includes("No evidence found."), html);
    assert.ok(!html.includes("spec-card"), html);
    assert.ok(!html.includes("figure-card"), html);
    assert.ok(!html.includes("verify-badge"), html);
  });

  test("nothing in the payload can inject markup", () => {
    const evil = {
      id: "a-3",
      createdAt: "",
      payload: {
        answer: `<img src=x onerror=alert(1)>`,
        route: `<script>`,
        language: "de",
        provider: `<svg onload=alert(2)>`,
        citations: [{ ...citation, title: `"><script>alert(3)</script>`, excerpt: `<iframe>` }],
        params: [{ ...paramRow, parameter: `<b>`, value: `<script>` }],
        figures: [{ ...figure, caption: `<script>`, labels: [`1 <script>`] }],
        profiles: [{ ...profile, product: `<script>`, key_use_case: `<script>` }],
      },
    };
    const html = assistantCardHTML(evil, { mediaUrl });
    // `<b>` and `<span>` are the template's own markup, so check the payload's
    // tags specifically: none of them may arrive unescaped.
    for (const tag of ["<script", "<iframe", "<img src=x", "<svg onload", "<b>&lt;"]) {
      assert.ok(!html.includes(tag), `${tag} must not survive into the markup`);
    }
    assert.ok(html.includes("&lt;b&gt;"), html);              // the parameter name, escaped
    assert.ok(!html.includes('class="spec-value"><script'), html);
    // the payload text is still *shown*, as text — escaping, not deletion
    assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"), html);
    assert.ok(html.includes("&lt;script&gt;alert(3)"), html);
  });
});

describe("userMessageHTML", () => {
  test("the question is shown verbatim and escaped", () => {
    const html = userMessageHTML({ id: "u-1", text: "Wieviel <b>Zuladung</b>?", createdAt: message.createdAt });
    assert.ok(html.includes("Wieviel &lt;b&gt;Zuladung&lt;/b&gt;?"), html);
    assert.match(html, /class="bubble bubble--user"/);
    assert.match(html, /<time[^>]*datetime="2026-11-14T09:12:00.000Z"/);
  });
});

describe("errorCardHTML — the API's own words, never a fake success", () => {
  test("a 503 shows the reason and the exact fix command", () => {
    const html = errorCardHTML(
      { kind: "http", status: 503, message: "search index not built", fix: "python -m ingest.run --data data" },
      "payload?"
    );
    assert.match(html, /role="alert"/);
    assert.ok(html.includes("search index not built"), html);
    assert.ok(html.includes("python -m ingest.run --data data"), html);
    assert.match(html, /data-action="retry"[^>]*data-question="payload\?"/);
  });

  test("a timeout says so, and a network error names the server command", () => {
    assert.match(errorCardHTML({ kind: "timeout", message: "No response within 60s" }, "q"), /took too long/);
    const html = errorCardHTML({ kind: "network", message: "Cannot reach the API" }, "q");
    assert.match(html, /Cannot reach the answer engine/);
    assert.ok(html.includes("python -m api"), html);
  });

  test("a 422 states the real length limit", () => {
    assert.ok(errorCardHTML({ kind: "http", status: 422, message: "too long" }, "q").includes("1–2000 characters"));
  });

  test("error text from the server is escaped", () => {
    const html = errorCardHTML({ kind: "http", status: 500, message: "<script>alert(1)</script>" }, "q");
    assert.ok(!html.includes("<script>alert"), html);
  });
});

describe("statusChipHTML — GET /health, including the degraded states", () => {
  test("ready: provider and retrieval mode are named", () => {
    const chip = statusChipHTML({
      reachable: true, ready: true, chunks: 1351, params: 2039, products: 13, media_files: 476,
      provider: { provider: "offline" }, vector_active: false, vector_detail: "no vector index",
    });
    assert.equal(chip.className, "status--ready");
    assert.ok(chip.label.includes("offline"), chip.label);
    assert.ok(chip.label.includes("keyword"), chip.label);
    assert.ok(chip.title.includes("1351 chunks"), chip.title);
  });

  test("ready with a vector index says hybrid", () => {
    const chip = statusChipHTML({
      reachable: true, ready: true, provider: { provider: "gemini" }, vector_active: true, vector_detail: "chroma ok",
    });
    assert.ok(chip.label.includes("hybrid"), chip.label);
    assert.ok(chip.label.includes("Gemini"), chip.label);
  });

  test("a configured Gemini whose last call failed is not reported as plain Gemini", () => {
    const chip = statusChipHTML({
      reachable: true, ready: true, vector_active: false,
      provider: { provider: "gemini", last_error: "the model 'gemini-x' is not available for this API key" },
    });
    assert.ok(chip.label.includes("Gemini failing"), chip.label);
    assert.ok(chip.title.includes("gemini-x"), chip.title);
  });

  test("a replaced model still reads Gemini, and the tooltip names the replacement", () => {
    const chip = statusChipHTML({
      reachable: true, ready: true, vector_active: false,
      provider: {
        provider: "gemini", model: "gemini-new", requested_model: "gemini-old", last_error: null,
        model_note: "'gemini-old' is not available for this API key; answering with 'gemini-new' instead",
      },
    });
    assert.ok(chip.label.includes("Gemini"), chip.label);
    assert.ok(!chip.label.includes("failing"), chip.label);
    assert.ok(chip.title.includes("gemini-new"), chip.title);
  });

  test("offline: the tooltip says why", () => {
    const chip = statusChipHTML({
      reachable: true, ready: true, provider: { provider: "offline", reason: "GEMINI_API_KEY not set" },
    });
    assert.ok(chip.title.includes("GEMINI_API_KEY not set"), chip.title);
  });

  test("index missing: red, with the server's own detail", () => {
    const chip = statusChipHTML({ reachable: true, ready: false, detail: "run python -m ingest.run --data data" });
    assert.equal(chip.className, "status--down");
    assert.equal(chip.label, "index not built");
    assert.ok(chip.title.includes("ingest.run"), chip.title);
  });

  test("unreachable API and no health at all are both reported, not hidden", () => {
    assert.equal(statusChipHTML({ reachable: false, detail: "fetch failed" }).label, "API offline");
    assert.equal(statusChipHTML(null).className, "status--down");
  });
});

describe("conversationListHTML", () => {
  test("conversations render with title, age and message count", () => {
    const html = conversationListHTML(
      [{ id: "c-1", title: "FlashBot payload", updatedAt: new Date().toISOString(), messages: [{}, {}] }],
      "c-1"
    );
    assert.ok(html.includes("FlashBot payload"), html);
    assert.ok(html.includes("2 messages"), html);
    assert.match(html, /class="conv-item is-active"/);
    // The delete button carries its own attribute: `data-conv` belongs to the
    // list item, and sharing it would make `[data-conv]` match two elements.
    assert.match(html, /data-action="delete-conv"[^>]*data-conv-id="c-1"/);
    assert.equal((html.match(/data-conv="/g) || []).length, 1);
  });

  test("an empty list explains that history is local-only", () => {
    const html = conversationListHTML([], null);
    assert.match(html, /class="side-empty"/);
    assert.ok(html.includes("stored in this browser only"), html);
  });

  test("a stored title cannot inject markup", () => {
    const html = conversationListHTML(
      [{ id: "c-2", title: `<script>alert(1)</script>`, updatedAt: "", messages: [] }],
      null
    );
    assert.ok(!html.includes("<script>alert"), html);
  });
});

describe("drawerBodyHTML — the raw /search pane stays honest", () => {
  const searchPayload = {
    route: "lookup", language: "de", confidence: 0.91,
    hits: [{ id: "h1" }, { id: "h2" }], params: [paramRow], profiles: [],
    cross_lingual: true, tiers_used: ["de", "en"],
  };

  test("a live result is summarised and pretty-printed", () => {
    const body = drawerBodyHTML({ search: searchPayload });
    assert.ok(body.meta.includes("route"), body.meta);
    assert.ok(body.meta.includes("hits <b>2</b>"), body.meta);
    assert.ok(body.meta.includes("cross-lingual"), body.meta);
    assert.ok(body.meta.includes("de, en"), body.meta);
    assert.ok(body.json.includes("j-key"), body.json);
  });

  test("a failed /search shows the failure, not an empty pane", () => {
    const body = drawerBodyHTML({ searchError: "HTTP 503" });
    assert.ok(body.json.includes("GET /search failed: HTTP 503"), body.json);
    assert.ok(body.json.includes("Re-run live"), body.json);
  });

  test("a restored conversation says the pane is not loaded yet", () => {
    const body = drawerBodyHTML({ search: null });
    assert.ok(body.json.includes("Not loaded for this restored conversation"), body.json);
  });
});
