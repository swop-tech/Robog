import { ChevronDown, ExternalLink, FileText, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import type { SourceReference as Source } from "@/types/chat";

function SourceRow({ source, isDemo }: { source: Source; isDemo?: boolean | undefined }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-md bg-background">
      <div className="flex items-center gap-2 px-2.5 py-2">
        <FileText className="size-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          {source.url ? (
            <a
              href={source.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex max-w-full items-center gap-1 truncate text-xs font-medium text-accent-foreground underline-offset-2 hover:underline"
            >
              <span className="truncate">{source.manual}</span>
              <ExternalLink className="size-3 shrink-0" />
            </a>
          ) : (
            <span className="block truncate text-xs font-medium text-foreground">
              {source.manual}
            </span>
          )}
          <span className="flex flex-wrap gap-x-2 text-[11px] text-muted-foreground">
            {source.page !== null && <span className="font-mono">p. {source.page}</span>}
            {source.language && <span>Original language: {source.language}</span>}
            {isDemo && (
              <span className="font-semibold uppercase tracking-wider text-accent-foreground">
                Demo placeholder
              </span>
            )}
          </span>
        </div>
        {source.excerpt && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="flex items-center gap-1 rounded px-1.5 py-1 text-[11px] font-medium text-accent-foreground hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {open ? "Hide" : "Excerpt"}
            <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
          </button>
        )}
      </div>
      {open && source.excerpt && (
        <blockquote className="mx-2.5 mb-2.5 border-l-2 border-primary pl-3 text-xs italic leading-relaxed text-muted-foreground">
          {source.excerpt}
        </blockquote>
      )}
    </li>
  );
}

export function SourceReferences({
  sources,
  isDemo,
}: {
  sources: Source[];
  isDemo?: boolean | undefined;
}) {
  if (sources.length === 0) {
    return (
      <p className="flex items-center gap-1.5 rounded-md border border-dashed border-border px-2.5 py-1.5 text-[11px] text-muted-foreground">
        <TriangleAlert className="size-3.5 text-primary" />
        No manual source was returned for this answer — verify before relying on it.
      </p>
    );
  }
  return (
    <div className="rounded-lg border border-border bg-source p-1.5">
      <p className="px-1.5 pb-1 pt-0.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        Sources ({sources.length})
      </p>
      <ul className="space-y-1">
        {sources.map((s, i) => (
          <SourceRow key={`${s.manual}-${s.page ?? "x"}-${i}`} source={s} isDemo={isDemo} />
        ))}
      </ul>
    </div>
  );
}
