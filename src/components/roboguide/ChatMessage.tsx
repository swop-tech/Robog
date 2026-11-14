import { FlaskConical, Lightbulb, SearchX, Wrench } from "lucide-react";
import { Message, MessageContent, MessageResponse } from "@/components/ai-elements/message";
import { MODE_LABELS } from "@/config/app";
import { cn } from "@/lib/utils";
import type { ChatMessage as ChatMessageType } from "@/types/chat";
import { BrandLogo } from "./BrandMark";
import { SourceReferences } from "./SourceReference";

const time = (ts: number) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

export function ChatMessage({ message }: { message: ChatMessageType }) {
  if (message.role === "user") {
    return (
      <Message from="user" className="max-w-[85%] sm:max-w-[75%]">
        <MessageContent className="rounded-2xl rounded-br-sm bg-bubble-user px-4 py-2.5 text-[0.9375rem] text-bubble-user-foreground group-[.is-user]:rounded-2xl group-[.is-user]:rounded-br-sm group-[.is-user]:bg-bubble-user group-[.is-user]:text-bubble-user-foreground">
          <p className="whitespace-pre-wrap">{message.content}</p>
        </MessageContent>
        <span className="ml-auto text-[11px] text-muted-foreground">{time(message.createdAt)}</span>
      </Message>
    );
  }

  const technical = message.mode !== "non-technical";
  const notFound = message.outcome === "not_found";
  const ModeIcon = technical ? Wrench : Lightbulb;

  return (
    <Message from="assistant" className="max-w-full sm:max-w-[88%]">
      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
        <BrandLogo className="size-6" />
        <span className="font-semibold text-foreground">RoboGuide</span>
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-medium",
            technical ? "border-border bg-background text-foreground" : "border-primary/40 bg-accent text-accent-foreground",
          )}
        >
          <ModeIcon className="size-3" />
          {technical ? MODE_LABELS.technical.short : MODE_LABELS["non-technical"].short}
        </span>
        {message.isDemo && (
          <span className="inline-flex items-center gap-1 rounded-full border border-dashed border-primary px-2 py-0.5 font-semibold uppercase tracking-wider text-accent-foreground">
            <FlaskConical className="size-3" /> Demo
          </span>
        )}
        {message.robotName && <span className="hidden sm:inline">· {message.robotName}</span>}
        <span>· {time(message.createdAt)}</span>
      </div>
      <MessageContent
        className={cn(
          "w-full rounded-2xl rounded-tl-sm bg-bubble-assistant text-bubble-assistant-foreground",
          technical ? "px-4 py-3" : "px-5 py-4",
          notFound && "border border-dashed border-border bg-background",
        )}
      >
        {notFound ? (
          <div className="flex items-start gap-2.5">
            <SearchX className="mt-0.5 size-5 shrink-0 text-primary" />
            <p className={technical ? "text-sm" : "text-base"}>{message.content}</p>
          </div>
        ) : (
          <MessageResponse className={technical ? "answer-technical" : "answer-simple"}>{message.content}</MessageResponse>
        )}
      </MessageContent>
      {!notFound && <SourceReferences sources={message.sources ?? []} isDemo={message.isDemo} />}
    </Message>
  );
}
