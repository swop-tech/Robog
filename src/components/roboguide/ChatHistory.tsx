import { Link, useNavigate } from "@tanstack/react-router";
import { useHydrated } from "@tanstack/react-router";
import { Lightbulb, MessageSquare, Plus, Trash2, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import { findRobot, MODE_LABELS } from "@/config/app";
import { createThread, deleteThread, usePreferences, useThreads } from "@/lib/chat-store";

export function ChatHistory() {
  const threads = useThreads();
  const prefs = usePreferences();
  const hydrated = useHydrated();
  const navigate = useNavigate();
  const sorted = [...threads].sort((a, b) => b.updatedAt - a.updatedAt);

  const startNew = () => {
    if (!prefs.mode) {
      void navigate({ to: "/" });
      return;
    }
    const id = createThread({ mode: prefs.mode, robotId: prefs.robotId, language: prefs.language });
    void navigate({ to: "/chat/$threadId", params: { threadId: id } });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {hydrated
            ? `${sorted.length} conversation${sorted.length === 1 ? "" : "s"} saved in this browser`
            : "Loading…"}
        </p>
        <Button onClick={startNew}>
          <Plus /> New conversation
        </Button>
      </div>

      {hydrated && sorted.length === 0 && (
        <div className="rounded-xl border border-dashed border-border bg-card p-10 text-center">
          <MessageSquare className="mx-auto size-8 text-muted-foreground" />
          <p className="mt-3 text-sm text-muted-foreground">
            No conversations yet. Start one to see it here.
          </p>
        </div>
      )}

      <ul className="space-y-2">
        {sorted.map((t) => {
          const robot = findRobot(t.robotId);
          const ModeIcon = t.mode === "technical" ? Wrench : Lightbulb;
          return (
            <li
              key={t.id}
              className="group flex items-center gap-2 rounded-xl border border-border bg-card shadow-card transition-colors hover:border-primary/60"
            >
              <Link
                to="/chat/$threadId"
                params={{ threadId: t.id }}
                className="min-w-0 flex-1 rounded-xl p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <p className="truncate font-medium text-foreground">{t.title}</p>
                <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1">
                    <ModeIcon className="size-3 text-primary" /> {MODE_LABELS[t.mode].short}
                  </span>
                  <span>· {robot?.name ?? "No machine selected"}</span>
                  <span>
                    · {t.messages.length} message{t.messages.length === 1 ? "" : "s"}
                  </span>
                  <span>
                    ·{" "}
                    {new Date(t.updatedAt).toLocaleString([], {
                      dateStyle: "medium",
                      timeStyle: "short",
                    })}
                  </span>
                </p>
              </Link>
              <Button
                variant="ghost"
                size="icon"
                className="mr-2 text-muted-foreground hover:bg-accent hover:text-destructive"
                aria-label={`Delete conversation: ${t.title}`}
                onClick={() => deleteThread(t.id)}
              >
                <Trash2 />
              </Button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
