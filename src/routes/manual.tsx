import { createFileRoute } from "@tanstack/react-router";
import { InfoCard, PageShell } from "@/components/roboguide/PageShell";

export const Route = createFileRoute("/manual")({
  head: () => ({
    meta: [
      { title: "Manual — RoboGuide AI" },
      {
        name: "description",
        content: "How to use RoboGuide AI: answer modes, machine selection, sources and languages.",
      },
      { property: "og:title", content: "Manual — RoboGuide AI" },
      {
        property: "og:description",
        content: "How to use RoboGuide AI: answer modes, machine selection, sources and languages.",
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
      title="How to use RoboGuide"
      intro="A quick guide to getting reliable answers from your robot manuals."
    >
      <div className="space-y-4">
        <InfoCard title="1. Choose an answer mode">
          <p>
            <strong>Technical</strong> gives precise terminology, parameter values with units,
            tables and detailed procedures. <strong>Non-technical</strong> (shown as <em>Simple</em>{" "}
            in the chat) uses everyday language and explains technical terms.
          </p>
          <p>
            Both modes search the same manuals — only the explanation style changes. Switch any time
            from the sidebar; your conversation is kept.
          </p>
        </InfoCard>
        <InfoCard title="2. Select your machine">
          <p>
            Use the selector at the top of the chat to pick the robot you're asking about. Answers
            are searched in that machine's manual.
          </p>
        </InfoCard>
        <InfoCard title="3. Ask your question">
          <p>
            Type a question and press Enter (Shift + Enter for a new line), or pick one of the FAQs
            or quick categories in the sidebar.
          </p>
        </InfoCard>
        <InfoCard title="4. Check the sources">
          <p>
            Under each answer you'll find the manual name, page number when available, and a link if
            the document is online. Open "Excerpt" to read the original passage. If the manual
            doesn't contain enough information, RoboGuide says so rather than guessing.
          </p>
        </InfoCard>
        <InfoCard title="5. Choose your language">
          <p>
            Use the language button in the chat header to pick the answer language. Sources stay in
            the manual's original language.
          </p>
        </InfoCard>
      </div>
    </PageShell>
  );
}
