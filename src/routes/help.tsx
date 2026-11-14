import { createFileRoute } from "@tanstack/react-router";
import { InfoCard, PageShell } from "@/components/roboguide/PageShell";

export const Route = createFileRoute("/help")({
  head: () => ({
    meta: [
      { title: "Need Help? — RoboGuide AI" },
      { name: "description", content: "Help and troubleshooting for using RoboGuide AI." },
      { property: "og:title", content: "Need Help? — RoboGuide AI" },
      { property: "og:description", content: "Help and troubleshooting for using RoboGuide AI." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Page,
});

function Page() {
  return (
    <PageShell title="Need help?" intro="Answers to common questions about using RoboGuide.">
      <div className="space-y-4">
        <InfoCard title="I don't see my machine in the list">
          <p>
            The machine list currently contains sample entries. Your administrator can connect the
            real list of documented machines.
          </p>
        </InfoCard>
        <InfoCard title="The answer says it couldn't find enough information">
          <p>
            The selected machine's manual doesn't cover the question clearly. Try rephrasing, check
            the selected machine, or ask a more specific question.
          </p>
        </InfoCard>
        <InfoCard title="An answer failed to load">
          <p>
            Press <strong>Retry</strong> under the error message. Your question is kept, so you
            don't need to type it again.
          </p>
        </InfoCard>
        <InfoCard title="Demo mode (for presentations)">
          <p>
            Without a connected manual service, RoboGuide shows clearly labelled placeholder
            answers. Add <code>#error</code> to a question to preview the error and retry flow, or{" "}
            <code>#notfound</code> to preview the "not enough information" answer.
          </p>
        </InfoCard>
      </div>
    </PageShell>
  );
}
