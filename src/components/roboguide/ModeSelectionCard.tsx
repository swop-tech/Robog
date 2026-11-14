import { Check, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface ModeSelectionCardProps {
  title: string;
  description: string;
  icon: LucideIcon;
  selected: boolean;
  onSelect: () => void;
  detail: string;
}

export function ModeSelectionCard({
  title,
  description,
  icon: Icon,
  selected,
  onSelect,
  detail,
}: ModeSelectionCardProps) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={cn(
        "group relative flex w-full flex-col items-start gap-4 rounded-2xl border bg-card p-6 text-left shadow-card transition-all duration-200 sm:p-7",
        "hover:-translate-y-0.5 hover:border-primary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        selected ? "border-primary bg-accent/40 shadow-card-active" : "border-border",
      )}
    >
      <span
        className={cn(
          "absolute right-5 top-5 flex size-6 items-center justify-center rounded-full border transition-colors",
          selected
            ? "border-primary bg-primary text-primary-foreground"
            : "border-border bg-background text-transparent",
        )}
        aria-hidden
      >
        <Check className="size-3.5" strokeWidth={3} />
      </span>
      <span
        className={cn(
          "flex size-14 items-center justify-center rounded-xl transition-colors",
          selected
            ? "bg-primary text-primary-foreground"
            : "bg-secondary text-foreground group-hover:text-primary",
        )}
      >
        <Icon className="size-7" strokeWidth={1.75} />
      </span>
      <span className="space-y-1.5">
        <span className="block font-display text-2xl font-bold text-foreground">{title}</span>
        <span className="block text-sm text-foreground">{description}</span>
        <span className="block text-xs text-muted-foreground">{detail}</span>
      </span>
    </button>
  );
}
