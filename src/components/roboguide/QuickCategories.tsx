import { ChevronRight, HardHat, LayoutGrid, Search, Settings2 } from "lucide-react";
import { useState } from "react";
import { QUICK_CATEGORIES, type QuickCategory } from "@/config/app";
import { cn } from "@/lib/utils";
import { SidebarSection } from "./SidebarSection";

const icons: Record<QuickCategory["id"], typeof HardHat> = {
  safety: HardHat,
  maintenance: Settings2,
  troubleshooting: Search,
};

export function QuickCategories({
  onAsk,
  disabled,
}: {
  onAsk: (q: string) => void;
  disabled?: boolean;
}) {
  const [active, setActive] = useState<QuickCategory["id"] | null>(null);
  return (
    <SidebarSection title="Quick categories" icon={LayoutGrid}>
      <ul className="space-y-0.5">
        {QUICK_CATEGORIES.map((cat) => {
          const Icon = icons[cat.id];
          const open = active === cat.id;
          return (
            <li key={cat.id}>
              <button
                type="button"
                aria-expanded={open}
                onClick={() => setActive(open ? null : cat.id)}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  open ? "bg-accent text-accent-foreground" : "text-foreground hover:bg-background",
                )}
              >
                <Icon className={cn("size-4", open ? "text-primary" : "text-foreground")} />
                <span className="flex-1 text-left">{cat.label}</span>
                <ChevronRight
                  className={cn(
                    "size-4 text-muted-foreground transition-transform",
                    open && "rotate-90",
                  )}
                />
              </button>
              {open && (
                <ul className="mb-1 ml-4 mt-1 space-y-0.5 border-l border-border pl-3">
                  {cat.questions.map((q) => (
                    <li key={q}>
                      <button
                        type="button"
                        disabled={disabled}
                        onClick={() => onAsk(q)}
                        className="w-full rounded-md px-2 py-1.5 text-left text-xs text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
                      >
                        {q}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </SidebarSection>
  );
}
