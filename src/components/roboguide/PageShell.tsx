import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";
import { Header } from "./Header";

export function PageShell({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col bg-secondary">
      <Header />
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
        <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-accent-foreground">
          <ArrowLeft className="size-4" /> Back
        </Link>
        <h1 className="mt-4 font-display text-3xl font-bold text-foreground sm:text-4xl">{title}</h1>
        {intro && <p className="mt-2 text-muted-foreground">{intro}</p>}
        <div className="mt-8">{children}</div>
      </main>
    </div>
  );
}

export function InfoCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-border bg-card p-5 shadow-card sm:p-6">
      <h2 className="font-display text-lg font-semibold text-foreground">{title}</h2>
      <div className="mt-2 space-y-2 text-sm leading-relaxed text-foreground">{children}</div>
    </section>
  );
}
