import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { AnswerMode } from "@/types/chat";
import { FAQSection } from "./FAQSection";
import { ModeSwitcher } from "./ModeSwitcher";
import { QuickCategories } from "./QuickCategories";

interface SidebarProps {
  mode: AnswerMode;
  onModeChange: (mode: AnswerMode) => void;
  onAsk: (question: string) => void;
  onNewChat: () => void;
  busy?: boolean;
}

export function Sidebar({ mode, onModeChange, onAsk, onNewChat, busy }: SidebarProps) {
  return (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      <div className="p-4 pb-2">
        <Button variant="accentOutline" className="w-full justify-start" onClick={onNewChat}>
          <Plus /> New conversation
        </Button>
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto px-4 py-3">
        <FAQSection onAsk={onAsk} disabled={busy ?? false} />
        <QuickCategories onAsk={onAsk} disabled={busy ?? false} />
      </div>
      <div className="border-t border-sidebar-border p-4">
        <ModeSwitcher value={mode} onChange={onModeChange} />
      </div>
    </div>
  );
}
