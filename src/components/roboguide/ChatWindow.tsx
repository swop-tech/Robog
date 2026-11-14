import { RotateCcw, TriangleAlert, X } from "lucide-react";
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { Message } from "@/components/ai-elements/message";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Button } from "@/components/ui/button";
import { FAQS } from "@/config/app";
import type { ChatMessage as ChatMessageType } from "@/types/chat";
import { BrandLogo } from "./BrandMark";
import { ChatMessage } from "./ChatMessage";

interface ChatWindowProps {
  messages: ChatMessageType[];
  loading: boolean;
  error: { message: string } | null;
  robotName: string | null;
  onRetry: () => void;
  onDismissError: () => void;
  onAsk: (q: string) => void;
}

function EmptyState({
  robotName,
  onAsk,
}: {
  robotName: string | null;
  onAsk: (q: string) => void;
}) {
  return (
    <div className="flex min-h-full flex-col items-center justify-center px-4 py-10 text-center">
      <BrandLogo className="size-16" />
      <h1 className="mt-4 font-display text-3xl font-bold text-foreground sm:text-4xl">
        How may I assist you?
      </h1>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        {robotName ? (
          <>
            Ask anything about the <span className="font-medium text-foreground">{robotName}</span>{" "}
            manual — specs, safety, maintenance or troubleshooting.
          </>
        ) : (
          <>
            Select a machine at the top, then ask about its manual — specs, safety, maintenance or
            troubleshooting.
          </>
        )}
      </p>
      <div className="mt-6 flex max-w-xl flex-wrap justify-center gap-2">
        {FAQS.slice(0, 3).map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onAsk(q)}
            className="rounded-full border border-border bg-background px-3.5 py-1.5 text-xs text-foreground transition-colors hover:border-primary hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

export function ChatWindow({
  messages,
  loading,
  error,
  robotName,
  onRetry,
  onDismissError,
  onAsk,
}: ChatWindowProps) {
  const empty = messages.length === 0 && !loading && !error;
  return (
    <Conversation className="min-h-0 bg-background">
      <ConversationContent className="mx-auto min-h-full w-full max-w-3xl gap-6 px-4 py-6 sm:px-6">
        {empty ? (
          <EmptyState robotName={robotName} onAsk={onAsk} />
        ) : (
          messages.map((m) => <ChatMessage key={m.id} message={m} />)
        )}

        {loading && (
          <Message from="assistant">
            <div className="flex items-center gap-2.5 rounded-2xl rounded-tl-sm bg-bubble-assistant px-4 py-3">
              <BrandLogo className="size-5 animate-pulse" />
              <Shimmer className="text-sm">
                {robotName ? `Searching the ${robotName} manual…` : "Searching the manuals…"}
              </Shimmer>
            </div>
          </Message>
        )}

        {error && (
          <div
            role="alert"
            className="flex flex-col gap-3 rounded-xl border border-destructive/30 bg-background p-4 sm:flex-row sm:items-center"
          >
            <TriangleAlert className="size-5 shrink-0 text-destructive" />
            <div className="flex-1">
              <p className="text-sm font-semibold text-foreground">The answer couldn't be loaded</p>
              <p className="text-xs text-muted-foreground">{error.message}</p>
            </div>
            <div className="flex gap-2">
              <Button size="sm" onClick={onRetry}>
                <RotateCcw /> Retry
              </Button>
              <Button size="sm" variant="ghost" onClick={onDismissError} aria-label="Dismiss error">
                <X />
              </Button>
            </div>
          </div>
        )}
      </ConversationContent>
      <ConversationScrollButton className="border-border bg-background text-foreground hover:bg-accent" />
    </Conversation>
  );
}
