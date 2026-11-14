import { createFileRoute } from "@tanstack/react-router";
import { ChatHistory } from "@/components/roboguide/ChatHistory";
import { PageShell } from "@/components/roboguide/PageShell";

export const Route = createFileRoute("/history")({
  head: () => ({
    meta: [
      { title: "Chat History — RoboGuide AI" },
      { name: "description", content: "Reopen previous RoboGuide AI conversations or start a new one." },
      { property: "og:title", content: "Chat History — RoboGuide AI" },
      { property: "og:description", content: "Reopen previous RoboGuide AI conversations or start a new one." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Page,
});

function Page() {
  return (
    <PageShell title="Chat History" intro="Reopen a previous conversation or start a new one.">
      <ChatHistory />
    </PageShell>
  );
}
