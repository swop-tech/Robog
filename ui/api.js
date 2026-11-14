/* API client — the only place in the UI that talks to the backend.
 *
 * Endpoints used (all of them real; `GET /openapi.json` lists exactly these):
 *
 *   POST /ask                          grounded, evidence-validated answer
 *   GET  /search                       raw hybrid retrieval (Bonus 01 drawer)
 *   GET  /health                       index + provider + vector status
 *   GET  /products                     catalog / profile cards / parameter rows
 *   GET  /media/{doc_id}/{filename}    the manuals' figures
 *
 * Nothing else exists, so nothing else is called: there is no streaming, no
 * conversation-history, no feedback and no regenerate endpoint. `CAPABILITIES`
 * below is the honest list the UI renders from — a control that would need a
 * missing endpoint is labelled "not supported by the API" instead of faking a
 * result. The exact request each one needs is written up in
 * `docs/ui-backend-gaps.md`.
 *
 * No API keys live here or anywhere in the browser: the Gemini key is read by
 * the *server* from `.env` (`answer/llm.py`), and the UI only ever sees
 * `provider`/`model` strings in the response.
 */

export const ENDPOINTS = {
  ask: "/ask",
  search: "/search",
  health: "/health",
  products: "/products",
  media: "/media",
};

/**
 * What the backend does and does not offer today. The UI must not pretend
 * otherwise: `false` means "label it, do not simulate it".
 */
export const CAPABILITIES = {
  streaming: false,             // POST /ask returns one JSON body; no SSE/WebSocket route exists
  conversationHistory: false,   // POST /ask is stateless; conversations live in localStorage only
  conversationSearch: false,    // same — search runs over the local store
  feedback: false,              // no POST /feedback; thumbs are stored locally and labelled as such
  regenerateEndpoint: false,    // "Regenerate" re-issues the same POST /ask (a real request)
  answerLanguageOverride: true, // AskRequest.language
  evidenceDepth: true,          // AskRequest.k (1..50)
  languagePolicyToggle: true,   // AskRequest.follow_user_language / cross_lingual_fallback
  productScope: true,           // AskRequest.product
  providerOverride: true,       // AskRequest.provider ("offline" | "gemini")
  rawSearch: true,              // GET /search, unchanged (Bonus 01)
};

/** `AskRequest` accepts at most this many characters (`api/service.py`). */
export const MAX_QUESTION_CHARS = 2000;

/** A request that never resolves would leave the spinner forever. */
export const DEFAULT_TIMEOUT_MS = 60_000;

/** Normalised failure: the UI renders `message` and, when present, `fix`. */
export class ApiError extends Error {
  constructor(kind, message, extra = {}) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;            // "network" | "timeout" | "http" | "json" | "empty"
    Object.assign(this, extra);
  }
}

/**
 * The API returns absolute `image_url`s (`http://host/media/…`). Behind a preview
 * proxy the browser must use its own origin, so keep only path + query.
 */
export function mediaUrl(url) {
  if (!url) return null;
  try {
    const parsed = new URL(url, globalThis.location?.origin ?? "http://local");
    return parsed.pathname + parsed.search;
  } catch {
    return String(url);
  }
}

/** Build the exact `GET /search` URL for a question (shown in the drawer). */
export function searchUrlFor(question, opts = {}) {
  const params = new URLSearchParams({ q: question, k: String(opts.k ?? 8) });
  if (opts.language) params.set("language", opts.language);
  if (opts.product) params.set("product", opts.product);
  if (opts.follow_user_language === false) params.set("follow_user_language", "false");
  if (opts.cross_lingual_fallback === false) params.set("cross_lingual_fallback", "false");
  return `${ENDPOINTS.search}?${params.toString()}`;
}

async function request(path, { method = "GET", body, timeoutMs = DEFAULT_TIMEOUT_MS, headers = {} } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: body ? { "content-type": "application/json", ...headers } : headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (err) {
    if (err?.name === "AbortError") {
      return { status: 0, payload: null, error: new ApiError("timeout", `No response within ${Math.round(timeoutMs / 1000)}s`, { timeoutMs }) };
    }
    return {
      status: 0,
      payload: null,
      error: new ApiError("network", "Cannot reach the API — is `python -m api` running?", { cause: String(err?.message || err) }),
    };
  } finally {
    clearTimeout(timer);
  }

  let payload = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      return {
        status: response.status,
        payload: null,
        error: new ApiError("json", `The API returned ${response.status} but the body was not JSON`),
      };
    }
  }

  if (!response.ok) {
    // 503 is the documented "index not built" response: it carries `reason` and
    // the exact `fix` command, which the UI shows verbatim.
    const detail = payload?.detail;
    const message =
      (typeof detail === "string" && detail) ||
      payload?.reason ||
      (Array.isArray(detail) && detail[0]?.msg) ||
      `HTTP ${response.status}`;
    return {
      status: response.status,
      payload,
      error: new ApiError("http", message, {
        status: response.status,
        reason: payload?.reason,
        fix: payload?.fix,
        body: payload,
      }),
    };
  }
  return { status: response.status, payload, error: null };
}

/**
 * POST /ask — the full RAG pipeline.
 *
 * Only fields `AskRequest` actually declares are sent; unknown keys would be
 * ignored by pydantic, and silently dropping a setting is worse than not
 * offering it.
 */
export async function ask(question, opts = {}) {
  const body = { question };
  if (opts.k) body.k = opts.k;
  if (opts.language) body.language = opts.language;
  if (opts.product) body.product = opts.product;
  if (opts.provider) body.provider = opts.provider;
  if (opts.follow_user_language === false) body.follow_user_language = false;
  if (opts.cross_lingual_fallback === false) body.cross_lingual_fallback = false;

  const { status, payload, error } = await request(ENDPOINTS.ask, { method: "POST", body, timeoutMs: opts.timeoutMs });
  if (error) throw error;
  if (!payload || typeof payload.answer !== "string") {
    throw new ApiError("empty", "The API answered but returned no `answer` field", { status, body: payload });
  }
  return payload;
}

/** GET /search — raw retrieval for the Bonus 01 drawer. */
export async function search(question, opts = {}) {
  const { payload, error } = await request(searchUrlFor(question, opts), { timeoutMs: opts.timeoutMs });
  if (error) throw error;
  return payload;
}

/**
 * GET /health. A 503 here is *information* (index not built), not a failure, so
 * the payload is returned either way and `reachable` says whether we got one.
 */
export async function health() {
  const { status, payload, error } = await request(ENDPOINTS.health);
  if (payload && typeof payload === "object") {
    return { reachable: true, status, ...payload };
  }
  return { reachable: false, status, ready: false, detail: error?.message ?? "unreachable" };
}

/** GET /products — the catalog, used to scope a question to one product. */
export async function products(params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const { payload, error } = await request(`${ENDPOINTS.products}${suffix}`);
  if (error) throw error;
  return payload;
}
