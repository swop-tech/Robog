import { CircleHelp, MessageSquareText } from "lucide-react";
import { FAQS } from "@/config/app";
import { SidebarSection } from "./SidebarSection";

export function FAQSection({ onAsk, disabled }: { onAsk: (q: string) => void; disabled?: boolean }) {
  return (
    <SidebarSection title="FAQs" icon={CircleHelp}>
      <ul className="space-y-0.5">
        {FAQS.map((q) => (
          <li key={q}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onAsk(q)}
              className="group flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
            >
              <MessageSquareText className="mt-0.5 size-3.5 shrink-0 text-muted-foreground group-hover:text-primary" />
              <span>{q}</span>
            </button>
          </li>
        ))}
      </ul>
    </SidebarSection>
  );
}
