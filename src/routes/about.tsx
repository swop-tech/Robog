import { createFileRoute } from "@tanstack/react-router";
import { InfoCard, PageShell } from "@/components/roboguide/PageShell";

export const Route = createFileRoute("/about")({
  head: () => ({
    meta: [
      { title: "About — RoboGuide AI" },
      {
        name: "description",
        content: "RoboGuide AI is a multilingual assistant for industrial robot user manuals.",
      },
      { property: "og:title", content: "About — RoboGuide AI" },
      {
        property: "og:description",
        content: "RoboGuide AI is a multilingual assistant for industrial robot user manuals.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Page,
});

function Page() {
  return (
    <PageShell
      title="About RoboGuide AI"
      intro="A multilingual conversational assistant for industrial robot user manuals."
    >
      <div className="space-y-4">
        <InfoCard title="What it does">
          <p>
            RoboGuide helps engineers, technicians and everyday users find information in robot
            manuals quickly — from payload specifications to safety procedures and troubleshooting
            steps.
          </p>
        </InfoCard>
        <InfoCard title="Grounded in the manuals">
          <p>
            Answers are based only on the available documentation. Every answer shows where it came
            from, and RoboGuide tells you honestly when the manual doesn't cover your question.
          </p>
        </InfoCard>
        <InfoCard title="Two ways to explain">
          <p>
            The same information, explained at two levels: detailed and technical, or simple and
            accessible.
          </p>
        </InfoCard>
      </div>
    </PageShell>
  );
}
