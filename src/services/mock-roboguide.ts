/**
 * MOCK / DEMO service — used only when no backend URL is configured.
 * It never returns real specifications, procedures, page numbers or citations.
 * Its answers are layout placeholders and are always flagged `isDemo: true`.
 *
 * Demo triggers (for live presentations):
 *   - include "#error"    in a question → simulates a failed request (shows Retry)
 *   - include "#notfound" in a question → simulates "not enough information in the manual"
 */
import { NOT_FOUND_MESSAGE } from "@/config/app";
import type { AskInput, AskResult } from "./roboguide-api";
import { RoboGuideApiError } from "./roboguide-api";

const wait = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    });
  });

function technicalTemplate(input: AskInput): string {
  return [
    `**Demo response — not taken from a manual.** This placeholder shows how a *technical* answer about **${input.robot}** will be laid out once the RoboGuide backend is connected.`,
    "",
    "| Parameter | Documented value | Unit |",
    "| --- | --- | --- |",
    "| Value retrieved from the manual | — | — |",
    "",
    "**Procedure (from manual)**",
    "1. Steps retrieved from the relevant manual section appear here.",
    "2. Each step keeps the manual's exact terminology and parameters.",
    "",
    "> **Safety:** warnings and cautions from the manual will be highlighted here.",
    "",
    `_Requested answer language: ${input.responseLanguage}._`,
  ].join("\n");
}

function simpleTemplate(input: AskInput): string {
  return [
    `**Demo response — not taken from a manual.** In Simple mode, answers about **${input.robot}** use everyday words and short sentences.`,
    "",
    "When the backend is connected, you'll get a clear explanation here — with technical words explained in plain language, and examples where they help.",
    "",
    `_Requested answer language: ${input.responseLanguage}._`,
  ].join("\n");
}

export async function mockAsk(input: AskInput, signal?: AbortSignal): Promise<AskResult> {
  await wait(900 + Math.random() * 700, signal);
  const q = input.question.toLowerCase();

  if (q.includes("#error")) {
    throw new RoboGuideApiError("Demo: simulated connection failure.");
  }

  if (q.includes("#notfound")) {
    return { answer: NOT_FOUND_MESSAGE, sources: [], outcome: "not_found", isDemo: true };
  }

  if (!input.robot) {
    return {
      answer:
        "Please select a machine at the top of the screen first, so I know which manual to search.",
      sources: [],
      outcome: "not_found",
      isDemo: true,
    };
  }

  return {
    answer: input.mode === "technical" ? technicalTemplate(input) : simpleTemplate(input),
    sources: [
      {
        manual: `Placeholder — ${input.robot} manual (not connected)`,
        page: null,
        excerpt:
          "The relevant excerpt from the retrieved manual section will appear here, in the manual's original language.",
        url: null,
        language: null,
      },
    ],
    outcome: "answered",
    isDemo: true,
  };
}
