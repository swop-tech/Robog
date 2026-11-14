import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useHydrated } from "@tanstack/react-router";
import { Menu } from "lucide-react";
import { useEffect, useState } from "react";
import { ChatInput } from "@/components/roboguide/ChatInput";
import { ChatWindow } from "@/components/roboguide/ChatWindow";
import { DemoBanner } from "@/components/roboguide/DemoBanner";
import { Header } from "@/components/roboguide/Header";
import { LanguageSelector } from "@/components/roboguide/LanguageSelector";
import { RobotSelector } from "@/components/roboguide/RobotSelector";
import { Sidebar } from "@/components/roboguide/Sidebar";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { findRobot } from "@/config/app";
import { useChatSession } from "@/hooks/use-chat-session";
import { createThread, ensureThread, setPreferences, updateThread, usePreferences, useThreads } from "@/lib/chat-store";
import type { AnswerMode, ChatThread } from "@/types/chat";

export const Route = createFileRoute("/chat/$threadId")({
  head: () => ({
    meta: [
      { title: "Chat — RoboGuide AI" },
      { name: "description", content: "Ask RoboGuide AI about your robot's manual and see the sources behind every answer." },
      { property: "og:title", content: "Chat — RoboGuide AI" },
      { property: "og:description", content: "Ask RoboGuide AI about your robot's manual and see the sources behind every answer." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: ChatPage,
});

function ChatPage() {
  const { threadId } = Route.useParams();
  const hydrated = useHydrated();
  const threads = useThreads();
  const prefs = usePreferences();
  const thread = threads.find((t) => t.id === threadId) ?? null;

  useEffect(() => {
    if (hydrated && !thread) {
      ensureThread(threadId, { mode: prefs.mode ?? "technical", robotId: prefs.robotId, language: prefs.language });
    }
  }, [hydrated, thread, threadId, prefs]);

  if (!thread) {
    return <div className="flex min-h-dvh items-center justify-center text-sm text-muted-foreground">Loading conversation…</div>;
  }
  return <ChatScreen key={thread.id} thread={thread} />;
}

function ChatScreen({ thread }: { thread: ChatThread }) {
  const navigate = useNavigate();
  const session = useChatSession(thread.id);
  const [navOpen, setNavOpen] = useState(false);
  const robot = findRobot(thread.robotId);
  const busy = session.status === "submitted";

  const ask = (q: string) => {
    setNavOpen(false);
    session.send(q);
  };
  const setMode = (mode: AnswerMode) => {
    updateThread(thread.id, { mode });
    setPreferences({ mode });
  };
  const setRobot = (robotId: string) => {
    updateThread(thread.id, { robotId });
    setPreferences({ robotId });
  };
  const setLanguage = (language: string) => {
    updateThread(thread.id, { language });
    setPreferences({ language });
  };
  const newChat = () => {
    setNavOpen(false);
    const id = createThread({ mode: thread.mode, robotId: thread.robotId, language: thread.language });
    void navigate({ to: "/chat/$threadId", params: { threadId: id } });
  };

  const sidebar = <Sidebar mode={thread.mode} onModeChange={setMode} onAsk={ask} onNewChat={newChat} busy={busy} />;
  const robotSelector = <RobotSelector value={thread.robotId} onChange={setRobot} />;

  return (
    <div className="flex h-dvh flex-col bg-background">
      <Header
        leading={
          <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open menu" onClick={() => setNavOpen(true)}>
            <Menu className="size-5" />
          </Button>
        }
        center={robotSelector}
        actions={<LanguageSelector value={thread.language} onChange={setLanguage} />}
        mobileRow={robotSelector}
      />
      <DemoBanner />
      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-64 shrink-0 border-r border-border md:block lg:w-72">{sidebar}</aside>
        <Sheet open={navOpen} onOpenChange={setNavOpen}>
          <SheetContent side="left" className="w-80 max-w-[85vw] p-0">
            <SheetTitle className="sr-only">Menu</SheetTitle>
            <SheetDescription className="sr-only">FAQs, quick categories and answer mode</SheetDescription>
            {sidebar}
          </SheetContent>
        </Sheet>
        <main className="flex min-w-0 flex-1 flex-col">
          <ChatWindow
            messages={thread.messages}
            loading={busy}
            error={session.error}
            robotName={robot?.name ?? null}
            onRetry={session.retry}
            onDismissError={session.dismissError}
            onAsk={ask}
          />
          <ChatInput onSend={session.send} busy={busy} mode={thread.mode} focusKey={`${thread.messages.length}-${session.status}`} />
        </main>
      </div>
    </div>
  );
}
