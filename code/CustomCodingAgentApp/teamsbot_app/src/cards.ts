import { Attachment, CardFactory } from "botbuilder";
import { PrototypeSummary } from "./mcpClient";

const CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive";

function card(body: unknown[], actions: unknown[] = []): Attachment {
  return CardFactory.adaptiveCard({
    $schema: "http://adaptivecards.io/schemas/adaptive-card.json",
    type: "AdaptiveCard",
    version: "1.4",
    body,
    ...(actions.length ? { actions } : {}),
  });
}

/** Card shown immediately when a workflow run is accepted. */
export function ackCard(requirement: string): Attachment {
  return card([
    { type: "TextBlock", text: "🚀 Prototype generation started", weight: "Bolder", size: "Medium" },
    {
      type: "TextBlock",
      text: "The multi-agent workflow (requirements → coding → testing → deployment → save) is running and takes about 8–10 minutes. I'll proactively push the result here when it finishes.",
      wrap: true,
    },
    { type: "TextBlock", text: "Requirement", weight: "Bolder", spacing: "Medium" },
    { type: "TextBlock", text: requirement, wrap: true, isSubtle: true },
  ]);
}

/** Final result card produced from a `generate_prototype` summary. */
export function resultCard(summary: PrototypeSummary): Attachment {
  const passed = summary.tests_passed;
  const stageFacts = summary.stages.map((s) => ({
    title: s.agent,
    value: `${s.description} · ${s.tokens.total} tok`,
  }));

  const actions: unknown[] = [];
  if (summary.deployed_url) {
    actions.push({ type: "Action.OpenUrl", title: "Open deployed app", url: summary.deployed_url });
  }
  if (summary.download_url) {
    actions.push({ type: "Action.OpenUrl", title: "Download source ZIP", url: summary.download_url });
  }

  return card(
    [
      {
        type: "TextBlock",
        text: passed ? "✅ Prototype generated" : "⚠️ Prototype finished (some tests did not pass)",
        weight: "Bolder",
        size: "Medium",
        color: passed ? "Good" : "Warning",
      },
      { type: "TextBlock", text: summary.requirement, wrap: true, isSubtle: true },
      {
        type: "FactSet",
        facts: [
          { title: "Tests passed", value: passed ? "Yes" : "No" },
          {
            title: "Deployed URL",
            value: summary.deployed_url ?? "Not deployed (none)",
          },
          {
            title: "Total tokens",
            value: String(summary.total_tokens.total),
          },
        ],
      },
      { type: "TextBlock", text: "Stages", weight: "Bolder", spacing: "Medium" },
      { type: "FactSet", facts: stageFacts },
    ],
    actions
  );
}

/** Simple error card. */
export function errorCard(title: string, detail: string): Attachment {
  return card([
    { type: "TextBlock", text: `❌ ${title}`, weight: "Bolder", color: "Attention" },
    { type: "TextBlock", text: detail, wrap: true },
  ]);
}

export { CARD_CONTENT_TYPE };
