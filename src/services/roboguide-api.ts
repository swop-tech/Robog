/**
 * RoboGuide API service layer — the ONLY place the frontend talks to the backend.
 *
 * - `buildRequestBody` shapes the outgoing request (edit to match the backend contract).
 * - `mapResponse` turns whatever the backend returns into the UI's `AskResult` (tolerant field mapping).
 * - When no backend URL is configured, `askRoboGuide` uses the clearly-labelled mock in ./mock-roboguide.
 */
import { NOT_FOUND_MESSAGE } from "@/config/app";
import type { AnswerMode, AnswerOutcome, SourceReference } from "@/types/chat";
import { apiConfig, isMockMode, type ApiConfig } from "./api-config";
import { RoboGuideApiError } from "./api-errors";
import { mockAsk } from "./mock-roboguide";

export { RoboGuideApiError };

export interface AskInput {
  question: string;
  /** Selected robot model name, or null if none chosen yet. */
  robot: string | null;
  mode: AnswerMode;
  /** Preferred answer language, e.g. "English". */
  responseLanguage: string;
  /** Local conversation id — useful if the backend keeps conversational context. */
  conversationId?: string;
}

export interface AskResult {
  answer: string;
  sources: SourceReference[];
  outcome: AnswerOutcome;
  /** True when the answer came from the mock service, not a real backend. */
  isDemo: boolean;
}

/** Proposed request body — adjust field names here to match the backend. */
export function buildRequestBody(input: AskInput, config: ApiConfig = apiConfig) {
  return {
    question: input.question,
    robot: input.robot,
    mode: config.modeValues[input.mode],
    response_language: input.responseLanguage,
    ...(input.conversationId ? { conversation_id: input.conversationId } : {}),
  };
}

type Raw = Record<string, unknown>;
const isObj = (v: unknown): v is Raw => typeof v === "object" && v !== null && !Array.isArray(v);
const pickString = (obj: Raw, keys: string[]): string | null => {
  for (const k of keys) {
    const v = obj[k];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return null;
};
const pickPage = (obj: Raw): number | null => {
  for (const k of ["page", "page_number", "pageNumber"]) {
    const v = obj[k];
    const n = typeof v === "number" ? v : typeof v === "string" && v.trim() ? Number(v) : NaN;
    if (Number.isInteger(n) && n > 0) return n;
  }
  return null;
};

function mapSource(raw: unknown): SourceReference | null {
  if (!isObj(raw)) return null;
  const manual = pickString(raw, ["manual", "document", "title", "source", "file_name", "filename"]);
  if (!manual) return null; // never show a citation without a document name
  return {
    manual,
    page: pickPage(raw),
    excerpt: pickString(raw, ["excerpt", "snippet", "text", "content", "chunk"]),
    url: pickString(raw, ["url", "link", "document_url", "href"]),
    language: pickString(raw, ["language", "lang", "manual_language"]),
  };
}

/** Tolerant response mapping. Missing data stays missing — nothing is fabricated. */
export function mapResponse(raw: unknown): Omit<AskResult, "isDemo"> {
  const obj = isObj(raw) ? raw : {};
  const answer = pickString(obj, ["answer", "response", "text", "output", "message"]);
  const rawSources = obj.sources ?? obj.references ?? obj.citations ?? [];
  const sources = (Array.isArray(rawSources) ? rawSources : [])
    .map(mapSource)
    .filter((s): s is SourceReference => s !== null);

  const notFound =
    obj.found === false ||
    obj.insufficient_information === true ||
    obj.status === "not_found" ||
    !answer;

  return {
    answer: answer ?? NOT_FOUND_MESSAGE,
    sources,
    outcome: notFound ? "not_found" : "answered",
  };
}

async function realAsk(input: AskInput, signal?: AbortSignal): Promise<AskResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException("timeout", "TimeoutError")), apiConfig.timeoutMs);
  const onAbort = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", onAbort);

  try {
    const res = await fetch(`${apiConfig.baseUrl}${apiConfig.askPath}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(buildRequestBody(input)),
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new RoboGuideApiError(`The RoboGuide service responded with an error (${res.status}).`, res.status);
    }
    const data: unknown = await res.json().catch(() => {
      throw new RoboGuideApiError("The RoboGuide service returned an unreadable response.");
    });
    return { ...mapResponse(data), isDemo: false };
  } catch (err) {
    if (err instanceof RoboGuideApiError) throw err;
    if (signal?.aborted) throw err;
    if (controller.signal.aborted) {
      throw new RoboGuideApiError("The request took too long. Please try again.");
    }
    throw new RoboGuideApiError("Couldn't reach the RoboGuide service. Check your connection and try again.");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}

export function askRoboGuide(input: AskInput, options: { signal?: AbortSignal } = {}): Promise<AskResult> {
  return isMockMode() ? mockAsk(input, options.signal) : realAsk(input, options.signal);
}

export { isMockMode };
