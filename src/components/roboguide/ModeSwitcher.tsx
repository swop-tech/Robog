import { Lightbulb, Wrench } from "lucide-react";
import { MODE_LABELS } from "@/config/app";
import { cn } from "@/lib/utils";
import type { AnswerMode } from "@/types/chat";

const options: { value: AnswerMode; icon: typeof Wrench }[] = [
  { value: "technical", icon: Wrench },
  { value: "non-technical", icon: Lightbulb },
];

export function ModeSwitcher({ value, onChange }: { value: AnswerMode; onChange: (m: AnswerMode) => void }) {
  return (
    <div className="space-y-2">
      <p className="px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Answer mode</p>
      <div role="radiogroup" aria-label="Answer mode" className="grid grid-cols-2 gap-1 rounded-lg border border-border bg-background p-1">
        {options.map(({ value: v, icon: Icon }) => {
          const active = v === value;
          return (
            <button
              key={v}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onChange(v)}
              className={cn(
                "flex items-center justify-center gap-1.5 rounded-md px-2 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                active ? "bg-accent text-accent-foreground shadow-sm ring-1 ring-primary/40" : "text-muted-foreground hover:bg-secondary hover:text-foreground",
              )}
            >
              <Icon className="size-4" />
              {MODE_LABELS[v].short}
            </button>
          );
        })}
      </div>
      <p className="px-1 text-xs text-muted-foreground">
        {value === "technical" ? "Precise specs, units & procedures." : "Everyday language, terms explained."} Switching keeps your conversation.
      </p>
    </div>
  );
}
