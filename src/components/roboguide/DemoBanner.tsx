import { FlaskConical } from "lucide-react";
import { isMockMode } from "@/services/roboguide-api";

/** Visible whenever the app runs without a real backend, so demo answers are never mistaken for manual data. */
export function DemoBanner() {
  if (!isMockMode()) return null;
  return (
    <div
      role="status"
      className="flex shrink-0 items-center justify-center gap-2 border-b border-border bg-muted px-4 py-1.5 text-center text-xs text-muted-foreground"
    >
      <FlaskConical className="size-3.5 text-primary" />
      <span>
        <strong className="font-semibold text-foreground">Demo mode</strong> — no manual service
        connected. Answers are placeholders, not verified manual information.
      </span>
    </div>
  );
}
