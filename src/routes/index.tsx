import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useHydrated } from "@tanstack/react-router";
import { ArrowRight, History, Lightbulb, Wrench } from "lucide-react";
import { useState } from "react";
import { DemoBanner } from "@/components/roboguide/DemoBanner";
import { Header } from "@/components/roboguide/Header";
import { ModeSelectionCard } from "@/components/roboguide/ModeSelectionCard";
import { Button } from "@/components/ui/button";
import { MODE_LABELS } from "@/config/app";
import { createThread, setPreferences, usePreferences, useThreads } from "@/lib/chat-store";
import type { AnswerMode } from "@/types/chat";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "RoboGuide AI — Choose how you want answers" },
      {
        name: "description",
        content: "Pick Technical or Non-technical mode and start asking questions about your industrial robot's manual.",
      },
      { property: "og:title", content: "RoboGuide AI — Choose how you want answers" },
      {
        property: "og:description",
        content: "Pick Technical or Non-technical mode and start asking questions about your industrial robot's manual.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Welcome,
});

function Welcome() {
  const [mode, setMode] = useState<AnswerMode | null>(null);
  const navigate = useNavigate();
  const prefs = usePreferences();
  const threads = useThreads();
  const hydrated = useHydrated();

  const start = () => {
    if (!mode) return;
    setPreferences({ mode });
    const id = createThread({ mode, robotId: prefs.robotId, language: prefs.language });
    void navigate({ to: "/chat/$threadId", params: { threadId: id } });
  };

  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <Header />
      <DemoBanner />
      <main className="bg-blueprint flex flex-1 items-center justify-center px-4 py-12 sm:py-16">
        <div className="w-full max-w-3xl text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-accent-foreground">Robot manual assistant</p>
          <h1 className="mt-3 font-display text-4xl font-bold text-foreground sm:text-6xl">How can I help you?</h1>
          <p className="mt-8 text-lg font-medium text-foreground sm:mt-10" id="mode-label">
            Choose a mode:
          </p>

          <div role="radiogroup" aria-labelledby="mode-label" className="mt-4 grid gap-4 sm:grid-cols-2 sm:gap-6">
            <ModeSelectionCard
              title={MODE_LABELS.technical.full}
              description={MODE_LABELS.technical.description}
              detail="Specs, units, step-by-step procedures — for engineers and technicians."
              icon={Wrench}
              selected={mode === "technical"}
              onSelect={() => setMode("technical")}
            />
            <ModeSelectionCard
              title={MODE_LABELS["non-technical"].full}
              description={MODE_LABELS["non-technical"].description}
              detail="Everyday language with terms explained — for anyone."
              icon={Lightbulb}
              selected={mode === "non-technical"}
              onSelect={() => setMode("non-technical")}
            />
          </div>

          <div className="mt-10 flex flex-col items-center gap-3">
            <Button variant="hero" size="xl" disabled={!mode} onClick={start} className="min-w-56">
              Start Chat <ArrowRight />
            </Button>
            <p className="h-4 text-xs text-muted-foreground" aria-live="polite">
              {mode ? `${MODE_LABELS[mode].full} mode selected — you can switch any time.` : "Select a mode to continue."}
            </p>
            {hydrated && threads.length > 0 && (
              <Link to="/history" className="mt-2 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-accent-foreground">
                <History className="size-4" /> Continue a previous conversation
              </Link>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
