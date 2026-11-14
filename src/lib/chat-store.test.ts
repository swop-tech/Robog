import { describe, expect, it } from "vitest";
import { appendMessage, createThread, getThread, updateThread } from "@/lib/chat-store";

describe("chat store", () => {
  it("keeps the conversation when switching answer mode", () => {
    const id = createThread({ mode: "technical" });
    appendMessage(id, { id: "m1", role: "user", content: "What is payload?", createdAt: 1 });
    updateThread(id, { mode: "non-technical" });
    const thread = getThread(id);
    expect(thread?.mode).toBe("non-technical");
    expect(thread?.messages).toHaveLength(1);
  });
});
