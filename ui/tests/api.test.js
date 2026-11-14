/* Contract tests for the network layer.
 *
 * `fetch` is replaced with a spy, so these tests assert what the UI actually
 * puts on the wire: only fields `AskRequest` declares, only endpoints that
 * `GET /openapi.json` lists, and a normalised `ApiError` for every way a request
 * can fail (HTTP error, non-JSON body, empty answer, timeout, no server).
 *
 *   node --test ui/tests/api.test.js
 */

import assert from "node:assert/strict";
import test, { afterEach, describe } from "node:test";

import {
  CAPABILITIES,
  DEFAULT_TIMEOUT_MS,
  ENDPOINTS,
  MAX_QUESTION_CHARS,
  ApiError,
  ask,
  health,
  mediaUrl,
  products,
  search,
  searchUrlFor,
} from "../api.js";

const realFetch = globalThis.fetch;
let calls = [];

/** Install a fetch spy. `handler(url, init)` returns a Response-ish object. */
function mockFetch(handler) {
  calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init);
  };
}

const json = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
});

afterEach(() => {
  globalThis.fetch = realFetch;
});

const answerPayload = {
  question: "Wieviel Zuladung hat der FlashBot?",
  answer: "20 kg [1].",
  language: "de",
  route: "lookup",
  citations: [{ n: 1, kind: "param" }],
  figures: [],
  params: [],
  profiles: [],
  verification: { checked: true, state: "verified", verified: true },
  cross_lingual: false,
  provider: "offline",
  model: null,
  latency_ms: 120,
  source_languages: ["de"],
};

describe("ENDPOINTS and CAPABILITIES", () => {
  test("only the routes the API really exposes are named", () => {
    // Mirrors the pinned list in tests/test_ui.py::test_static_mount_is_not_an_openapi_route.
    assert.deepEqual(Object.values(ENDPOINTS).sort(), ["/ask", "/health", "/media", "/products", "/search"]);
  });

  test("features the backend does not have are flagged false, never simulated", () => {
    assert.equal(CAPABILITIES.streaming, false);
    assert.equal(CAPABILITIES.conversationHistory, false);
    assert.equal(CAPABILITIES.conversationSearch, false);
    assert.equal(CAPABILITIES.feedback, false);
    assert.equal(CAPABILITIES.regenerateEndpoint, false);
  });

  test("the AskRequest fields the UI does offer are flagged true", () => {
    for (const key of ["answerLanguageOverride", "evidenceDepth", "languagePolicyToggle", "productScope", "providerOverride", "rawSearch"]) {
      assert.equal(CAPABILITIES[key], true, key);
    }
  });

  test("the length limit matches api/service.py::MAX_QUESTION_CHARS", () => {
    assert.equal(MAX_QUESTION_CHARS, 2000);
    assert.ok(DEFAULT_TIMEOUT_MS >= 30_000, "a Gemini answer can take tens of seconds");
  });
});

describe("mediaUrl — proxy-safe same-origin figure paths", () => {
  test("an absolute URL from the API becomes a path on this origin", () => {
    assert.equal(
      mediaUrl("http://127.0.0.1:8000/media/bellabot/images/en-p018L-fig01.png"),
      "/media/bellabot/images/en-p018L-fig01.png"
    );
  });

  test("a query string is kept, a foreign host is dropped", () => {
    assert.equal(mediaUrl("https://example.test/media/a.png?v=2"), "/media/a.png?v=2");
  });

  test("a relative path passes through unchanged", () => {
    assert.equal(mediaUrl("/media/a.png"), "/media/a.png");
  });

  test("null / undefined / empty -> null, so no empty <img src> is emitted", () => {
    assert.equal(mediaUrl(null), null);
    assert.equal(mediaUrl(undefined), null);
    assert.equal(mediaUrl(""), null);
  });
});

describe("searchUrlFor", () => {
  test("the default call sends only q and k", () => {
    assert.equal(searchUrlFor("payload", { k: 8 }), "/search?q=payload&k=8");
  });

  test("question text is percent-encoded", () => {
    assert.equal(searchUrlFor("Qual è la velocità?", { k: 5 }), "/search?q=Qual+%C3%A8+la+velocit%C3%A0%3F&k=5");
  });

  test("language and product filters are passed through", () => {
    const url = searchUrlFor("q", { k: 8, language: "it", product: "FlashBot" });
    assert.ok(url.includes("language=it"), url);
    assert.ok(url.includes("product=FlashBot"), url);
  });

  test("the policy toggles are only sent when they are off (the server defaults to on)", () => {
    assert.ok(!searchUrlFor("q", { follow_user_language: true }).includes("follow_user_language"));
    assert.ok(searchUrlFor("q", { follow_user_language: false }).includes("follow_user_language=false"));
    assert.ok(searchUrlFor("q", { cross_lingual_fallback: false }).includes("cross_lingual_fallback=false"));
  });

  test("k falls back to the API default of 8 when unset", () => {
    assert.equal(searchUrlFor("q", {}), "/search?q=q&k=8");
  });
});

describe("POST /ask", () => {
  test("the request body contains only declared AskRequest fields", async () => {
    mockFetch(() => json(answerPayload));
    await ask("Wieviel Zuladung hat der FlashBot?", {
      k: 8,
      language: "",            // empty -> omitted, so the server detects the language
      product: "",
      provider: "",
      follow_user_language: true,
      cross_lingual_fallback: true,
    });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/ask");
    assert.equal(calls[0].init.method, "POST");
    assert.equal(calls[0].init.headers["content-type"], "application/json");
    assert.deepEqual(JSON.parse(calls[0].init.body), { question: "Wieviel Zuladung hat der FlashBot?", k: 8 });
  });

  test("a switched-off policy flag is sent explicitly", async () => {
    mockFetch(() => json(answerPayload));
    await ask("q", { k: 5, follow_user_language: false, cross_lingual_fallback: false, provider: "gemini", product: "BellaBot" });
    assert.deepEqual(JSON.parse(calls[0].init.body), {
      question: "q", k: 5, product: "BellaBot", provider: "gemini",
      follow_user_language: false, cross_lingual_fallback: false,
    });
  });

  test("an abort signal is attached so a hung request cannot spin forever", async () => {
    mockFetch(() => json(answerPayload));
    await ask("q", {});
    assert.ok(calls[0].init.signal instanceof AbortSignal);
  });

  test("a 503 carries the server's reason and its exact fix command", async () => {
    mockFetch(() => json({ reason: "search index not built", fix: "python -m ingest.run --data data" }, 503));
    await assert.rejects(
      () => ask("q", {}),
      (error) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.kind, "http");
        assert.equal(error.status, 503);
        assert.equal(error.reason, "search index not built");
        assert.equal(error.fix, "python -m ingest.run --data data");
        assert.equal(error.message, "search index not built");
        return true;
      }
    );
  });

  test("a 422 reports pydantic's first message", async () => {
    mockFetch(() => json({ detail: [{ msg: "String should have at most 2000 characters", type: "string_too_long" }] }, 422));
    await assert.rejects(() => ask("q", {}), /at most 2000 characters/);
  });

  test("a 500 with a plain string detail surfaces that string", async () => {
    mockFetch(() => json({ detail: "internal error" }, 500));
    await assert.rejects(() => ask("q", {}), (error) => error.status === 500 && error.message === "internal error");
  });

  test("an HTML error page (a proxy in front of the API) is reported as bad JSON", async () => {
    mockFetch(() => json("<html>502 Bad Gateway</html>", 200));
    await assert.rejects(() => ask("q", {}), (error) => error.kind === "json");
  });

  test("a 200 without an `answer` field is an empty response, not a success", async () => {
    mockFetch(() => json({ question: "q", route: "default" }));
    await assert.rejects(() => ask("q", {}), (error) => error.kind === "empty" && /no `answer` field/.test(error.message));
  });

  test("a request that never resolves becomes a timeout error", async () => {
    globalThis.fetch = (url, init) =>
      new Promise((resolve, reject) => {
        calls.push({ url, init });
        init.signal.addEventListener("abort", () => {
          const error = new Error("This operation was aborted");
          error.name = "AbortError";
          reject(error);
        });
      });
    const started = Date.now();
    await assert.rejects(
      () => ask("q", { timeoutMs: 40 }),
      (error) => error.kind === "timeout" && /40s|0\.04|within/.test(error.message)
    );
    assert.ok(Date.now() - started < 3000, "the timeout must fire, not hang");
  });

  test("no server at all becomes a network error naming the start command", async () => {
    globalThis.fetch = async () => {
      throw new TypeError("fetch failed");
    };
    await assert.rejects(
      () => ask("q", {}),
      (error) => error.kind === "network" && error.message.includes("python -m api") && error.cause === "fetch failed"
    );
  });
});

describe("GET /search", () => {
  test("it calls the retrieval route on its own and returns the payload untouched", async () => {
    const payload = { route: "lookup", language: "de", confidence: 0.9, hits: [], params: [], profiles: [] };
    mockFetch(() => json(payload));
    const result = await search("payload", { k: 8 });
    assert.equal(calls[0].url, "/search?q=payload&k=8");
    assert.equal(calls[0].init.method, "GET");
    assert.deepEqual(result, payload);
  });

  test("a 503 rejects with the same normalised error as /ask", async () => {
    mockFetch(() => json({ reason: "search index not built", fix: "python -m ingest.run --data data" }, 503));
    await assert.rejects(() => search("q", {}), (error) => error.status === 503 && Boolean(error.fix));
  });
});

describe("GET /health", () => {
  test("a 503 health response is information, not a failure", async () => {
    mockFetch(() => json({ ready: false, detail: "run python -m ingest.run --data data", chunks: 0 }, 503));
    const result = await health();
    assert.equal(result.reachable, true);
    assert.equal(result.status, 503);
    assert.equal(result.ready, false);
    assert.ok(result.detail.includes("ingest.run"));
  });

  test("a ready index reports provider and vector status", async () => {
    mockFetch(() => json({ ready: true, chunks: 1351, provider: { provider: "offline" }, vector_active: false }));
    const result = await health();
    assert.equal(result.ready, true);
    assert.equal(result.provider.provider, "offline");
    assert.equal(result.vector_active, false);
  });

  test("an unreachable API is reported without throwing", async () => {
    globalThis.fetch = async () => {
      throw new TypeError("fetch failed");
    };
    const result = await health();
    assert.equal(result.reachable, false);
    assert.equal(result.ready, false);
    assert.ok(result.detail.length > 0);
  });
});

describe("GET /products", () => {
  test("empty filters are dropped from the query string", async () => {
    mockFetch(() => json({ products: [], count: 0 }));
    await products({ include_params: false, language: "en", product: "", q: null, doc_type: undefined });
    assert.equal(calls[0].url, "/products?include_params=false&language=en");
  });

  test("no filters at all means no query string", async () => {
    mockFetch(() => json({ products: [] }));
    await products();
    assert.equal(calls[0].url, "/products");
  });

  test("an error is thrown to the caller, which shows it in the settings dialog", async () => {
    mockFetch(() => json({ reason: "search index not built" }, 503));
    await assert.rejects(() => products({}), (error) => error.status === 503);
  });
});
