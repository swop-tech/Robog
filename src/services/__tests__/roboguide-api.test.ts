import { describe, expect, it } from "vitest";
import { NOT_FOUND_MESSAGE } from "@/config/app";
import { isMockMode, type ApiConfig } from "@/services/api-config";
import { mockAsk } from "@/services/mock-roboguide";
import { buildRequestBody, mapResponse } from "@/services/roboguide-api";

const config: ApiConfig = {
  baseUrl: "https://api.example.com",
  askPath: "/ask",
  forceMock: false,
  timeoutMs: 1000,
  modeValues: { technical: "technical", "non-technical": "non-technical" },
};

describe("RoboGuide API service", () => {
  it("sends question, robot, mode and response_language in the request body", () => {
    expect(
      buildRequestBody(
        { question: "What is the maximum payload?", robot: "ArmPro X6", mode: "technical", responseLanguage: "English" },
        config,
      ),
    ).toEqual({
      question: "What is the maximum payload?",
      robot: "ArmPro X6",
      mode: "technical",
      response_language: "English",
    });
  });

  it("sends the non-technical mode value for Simple mode", () => {
    const body = buildRequestBody(
      { question: "What does payload mean?", robot: null, mode: "non-technical", responseLanguage: "Italian" },
      config,
    );
    expect(body.mode).toBe("non-technical");
    expect(body.response_language).toBe("Italian");
  });

  it("never invents a page number when the backend omits it", () => {
    const res = mapResponse({ answer: "10 kg", sources: [{ manual: "X6 Manual", excerpt: "Payload: 10 kg" }] });
    expect(res.sources[0]?.page).toBeNull();
    expect(res.sources[0]?.url).toBeNull();
  });

  it("keeps a documented page number", () => {
    const res = mapResponse({ answer: "10 kg", sources: [{ manual: "X6 Manual", page: "42" }] });
    expect(res.sources[0]?.page).toBe(42);
  });

  it("drops sources without a document name instead of showing a blank citation", () => {
    const res = mapResponse({ answer: "ok", sources: [{ page: 3, excerpt: "orphan" }] });
    expect(res.sources).toHaveLength(0);
  });

  it("reports not-found honestly when the backend has no answer", () => {
    const res = mapResponse({ answer: "", sources: [] });
    expect(res.outcome).toBe("not_found");
    expect(res.answer).toBe(NOT_FOUND_MESSAGE);
  });

  it("uses mock mode when no backend URL is configured", () => {
    expect(isMockMode({ ...config, baseUrl: "" })).toBe(true);
    expect(isMockMode(config)).toBe(false);
  });

  it("flags mock answers as demo and never gives them page numbers", async () => {
    const res = await mockAsk({ question: "payload?", robot: "ArmPro X6", mode: "technical", responseLanguage: "English" });
    expect(res.isDemo).toBe(true);
    expect(res.sources.every((s) => s.page === null)).toBe(true);
  });
});
