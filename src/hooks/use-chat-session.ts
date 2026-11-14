import { useCallback, useEffect, useRef, useState } from "react";
import { findRobot } from "@/config/app";
import { appendMessage, getThread, newId } from "@/lib/chat-store";
import { askRoboGuide, RoboGuideApiError } from "@/services/roboguide-api";

export type SessionStatus = "ready" | "submitted" | "error";

export function useChatSession(threadId: string) {
  const [status, setStatus] = useState<SessionStatus>("ready");
  const [error, setError] = useState<{ message: string; question: string } | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), [threadId]);

  const request = useCallback(
    async (question: string) => {
      const thread = getThread(threadId);
      if (!thread) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setError(null);
      setStatus("submitted");

      const robot = findRobot(thread.robotId);
      try {
        const result = await askRoboGuide(
          {
            question,
            robot: robot?.name ?? null,
            mode: thread.mode,
            responseLanguage: thread.language,
            conversationId: thread.id,
          },
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        appendMessage(threadId, {
          id: newId(),
          role: "assistant",
          content: result.answer,
          createdAt: Date.now(),
          mode: thread.mode,
          robotName: robot?.name ?? null,
          language: thread.language,
          sources: result.sources,
          outcome: result.outcome,
          isDemo: result.isDemo,
        });
        setStatus("ready");
      } catch (err) {
        if (controller.signal.aborted) return;
        setError({
          message:
            err instanceof RoboGuideApiError ? err.message : "Something unexpected went wrong.",
          question,
        });
        setStatus("error");
      }
    },
    [threadId],
  );

  const send = useCallback(
    (text: string) => {
      const question = text.trim();
      if (!question || status === "submitted") return;
      appendMessage(threadId, {
        id: newId(),
        role: "user",
        content: question,
        createdAt: Date.now(),
      });
      void request(question);
    },
    [threadId, request, status],
  );

  const retry = useCallback(() => {
    if (error) void request(error.question);
  }, [error, request]);

  const dismissError = useCallback(() => {
    setError(null);
    setStatus("ready");
  }, []);

  return { status, error, send, retry, dismissError };
}
