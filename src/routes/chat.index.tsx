import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useHydrated } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { createThread, usePreferences } from "@/lib/chat-store";

export const Route = createFileRoute("/chat/")({
  head: () => ({
    meta: [
      { title: "New conversation — RoboGuide AI" },
      { name: "description", content: "Start a new conversation with RoboGuide AI." },
      { property: "og:title", content: "New conversation — RoboGuide AI" },
      { property: "og:description", content: "Start a new conversation with RoboGuide AI." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: NewChat,
});

function NewChat() {
  const hydrated = useHydrated();
  const prefs = usePreferences();
  const navigate = useNavigate();
  const done = useRef(false);

  useEffect(() => {
    if (!hydrated || done.current) return;
    done.current = true;
    if (!prefs.mode) {
      void navigate({ to: "/", replace: true });
      return;
    }
    const id = createThread({ mode: prefs.mode, robotId: prefs.robotId, language: prefs.language });
    void navigate({ to: "/chat/$threadId", params: { threadId: id }, replace: true });
  }, [hydrated, prefs, navigate]);

  return (
    <div className="flex min-h-dvh items-center justify-center text-sm text-muted-foreground">
      Opening a new conversation…
    </div>
  );
}
