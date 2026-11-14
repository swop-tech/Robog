import type { AnswerMode } from "@/types/chat";

/**
 * Backend integration settings. Everything here is configurable through Vite env vars
 * (see .env.example) so it can be matched to the backend team's actual contract.
 */
export interface ApiConfig {
  /** Backend base URL, e.g. https://roboguide-api.example.com. Empty = mock/demo mode. */
  baseUrl: string;
  /** Path of the question endpoint, appended to baseUrl. */
  askPath: string;
  /** Force mock/demo mode even if a baseUrl is set. */
  forceMock: boolean;
  /** Request timeout in milliseconds. */
  timeoutMs: number;
  /** Value sent for each mode in the request's `mode` field. */
  modeValues: Record<AnswerMode, string>;
}

const env = import.meta.env;

export const apiConfig: ApiConfig = {
  baseUrl: String(env["VITE_ROBOGUIDE_API_URL"] ?? "").replace(/\/+$/, ""),
  askPath: String(env["VITE_ROBOGUIDE_ASK_PATH"] || "/ask"),
  forceMock: env["VITE_ROBOGUIDE_MOCK"] === "true",
  timeoutMs: Number(env["VITE_ROBOGUIDE_TIMEOUT_MS"]) || 60_000,
  modeValues: {
    technical: String(env["VITE_ROBOGUIDE_MODE_TECHNICAL"] || "technical"),
    "non-technical": String(env["VITE_ROBOGUIDE_MODE_NON_TECHNICAL"] || "non-technical"),
  },
};

export const isMockMode = (config: ApiConfig = apiConfig) => config.forceMock || !config.baseUrl;
