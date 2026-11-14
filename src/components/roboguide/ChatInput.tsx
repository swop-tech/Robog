import { Lightbulb, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  PromptInput,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
} from "@/components/ai-elements/prompt-input";
import { MODE_LABELS } from "@/config/app";
import type { AnswerMode } from "@/types/chat";

interface ChatInputProps {
  onSend: (text: string) => void;
  busy: boolean;
  mode: AnswerMode;
  /** Changes when focus should be restored (e.g. after an answer arrives). */
  focusKey?: unknown;
}

export function ChatInput({ onSend, busy, mode, focusKey }: ChatInputProps) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const canSend = value.trim().length > 0 && !busy;
  const ModeIcon = mode === "technical" ? Wrench : Lightbulb;

  useEffect(() => {
    ref.current?.focus();
  }, [focusKey]);

  return (
    <div className="shrink-0 border-t border-border bg-background px-3 pb-3 pt-3 sm:px-6 sm:pb-5">
      <div className="mx-auto max-w-3xl">
        <PromptInput
          onSubmit={({ text }) => {
            if (!text.trim() || busy) return;
            onSend(text);
            setValue("");
            ref.current?.focus();
          }}
          className="[&_[data-slot=input-group]]:rounded-xl [&_[data-slot=input-group]]:border-border [&_[data-slot=input-group]]:shadow-card [&_[data-slot=input-group]]:has-[textarea:focus-visible]:border-primary [&_[data-slot=input-group]]:has-[textarea:focus-visible]:ring-2 [&_[data-slot=input-group]]:has-[textarea:focus-visible]:ring-ring/30"
        >
          <PromptInputTextarea
            ref={ref}
            value={value}
            onChange={(e) => setValue(e.currentTarget.value)}
            placeholder="Type your question here..."
            aria-label="Your question"
            className="min-h-14 px-4 text-[0.9375rem]"
          />
          <PromptInputFooter className="px-3 pb-2.5">
            <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <ModeIcon className="size-3.5 text-primary" />
              <span className="font-medium text-foreground">{MODE_LABELS[mode].short} mode</span>
              <span className="hidden sm:inline">· Enter to send · Shift + Enter for a new line</span>
            </span>
            <PromptInputSubmit
              status={busy ? "submitted" : "ready"}
              disabled={!canSend}
              aria-label="Send question"
              className="rounded-lg bg-primary text-primary-foreground hover:bg-primary-strong disabled:bg-border disabled:text-muted-foreground disabled:opacity-100"
            />
          </PromptInputFooter>
        </PromptInput>
      </div>
    </div>
  );
}
