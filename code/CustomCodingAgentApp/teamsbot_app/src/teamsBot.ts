import {
  ActivityHandler,
  CloudAdapter,
  ConversationReference,
  TurnContext,
} from "botbuilder";
import { config } from "./config";
import { mcpService, PrototypeSummary } from "./mcpClient";
import { ackCard, errorCard, resultCard } from "./cards";
import { openArtifactLocally } from "./localActions";

const HELP = [
  "**OpenClaw Workflow Bot**",
  "",
  "- Send a requirement directly (e.g. `Build a BBC-style World Cup feature page`) → triggers the full multi-agent workflow `generate_prototype` (~8–10 minutes; the result card is pushed proactively when done).",
  "- `health` → check the backend OpenClaw gateway health.",
  "- `agent <agentId> <message>` → call a single agent only (requirements-agent / coding-agent / testing-agent / deployment-agent / save-agent).",
  "- `help` → show this help.",
].join("\n");

/**
 * Teams bot that drives the OpenClaw MCP workflow.
 *
 * Because `generate_prototype` runs for ~8-10 minutes (well past Teams' ~15s
 * turn timeout), a run is acknowledged immediately and executed in the
 * background; the final result is delivered via a proactive message.
 */
export class TeamsBot extends ActivityHandler {
  constructor(
    private readonly adapter: CloudAdapter,
    private readonly appId: string
  ) {
    super();

    this.onMessage(async (context, next) => {
      const text = (context.activity.text ?? "").trim();
      TurnContext.removeRecipientMention(context.activity);
      const cleaned = (context.activity.text ?? "").trim() || text;

      if (!cleaned || /^help$/i.test(cleaned)) {
        await context.sendActivity(HELP);
      } else if (/^health$/i.test(cleaned)) {
        await this.handleHealth(context);
      } else if (/^agent\s+/i.test(cleaned)) {
        await this.handleAgent(context, cleaned);
      } else {
        await this.handlePrototype(context, cleaned);
      }
      await next();
    });

    this.onMembersAdded(async (context, next) => {
      for (const member of context.activity.membersAdded ?? []) {
        if (member.id !== context.activity.recipient.id) {
          await context.sendActivity(HELP);
        }
      }
      await next();
    });
  }

  private async handleHealth(context: TurnContext): Promise<void> {
    try {
      const health = await mcpService.checkGatewayHealth();
      await context.sendActivity("```json\n" + JSON.stringify(health, null, 2) + "\n```");
    } catch (err) {
      await context.sendActivity({ attachments: [errorCard("Gateway health check failed", String(err))] });
    }
  }

  private async handleAgent(context: TurnContext, text: string): Promise<void> {
    // Format: agent <agentId> <message...>
    const match = text.match(/^agent\s+(\S+)\s+([\s\S]+)$/i);
    if (!match) {
      await context.sendActivity("Usage: `agent <agentId> <message>`");
      return;
    }
    const [, agentId, message] = match;
    await context.sendActivity(`▶ Calling **${agentId}** …`);
    try {
      const reply = await mcpService.runAgent(agentId, message);
      await context.sendActivity(reply);
    } catch (err) {
      await context.sendActivity({ attachments: [errorCard(`Call to ${agentId} failed`, String(err))] });
    }
  }

  private async handlePrototype(context: TurnContext, requirement: string): Promise<void> {
    // Ack immediately so the user isn't left waiting past the turn timeout.
    await context.sendActivity({ attachments: [ackCard(requirement)] });

    // Capture a conversation reference for the proactive result message.
    const reference = TurnContext.getConversationReference(context.activity);

    // Fire-and-forget: run the long workflow, then push the result proactively.
    void this.runPrototypeInBackground(reference, requirement);
  }

  private async runPrototypeInBackground(
    reference: Partial<ConversationReference>,
    requirement: string
  ): Promise<void> {
    // Serialize proactive sends so progress lines arrive in order and never
    // overlap the final card.
    let queue: Promise<void> = Promise.resolve();
    const push = (fn: (context: TurnContext) => Promise<void>): Promise<void> => {
      queue = queue
        .then(() => this.adapter.continueConversationAsync(this.appId, reference, fn))
        .catch((e) => console.error("[proactive send failed]", e));
      return queue;
    };

    // Forward each agent/stage status update from the MCP server to Teams.
    const onProgress = (message: string) => {
      void push(async (context) => {
        await context.sendActivity(`⏳ ${message}`);
      });
    };

    let summary: PrototypeSummary | null = null;
    let failure: string | null = null;
    try {
      const result = await mcpService.generatePrototype(requirement, onProgress);
      summary = result.summary;
    } catch (err) {
      failure = String(err);
    }

    // Post the final card after all queued progress messages have flushed.
    await push(async (context) => {
      if (summary) {
        await context.sendActivity({ attachments: [resultCard(summary)] });
      } else {
        await context.sendActivity({
          attachments: [errorCard("Prototype generation failed", failure ?? "Unknown error")],
        });
      }
    });

    // Optionally (only when running locally) open the deployed URL in the
    // browser and download+extract+open the source in VS Code.
    if (summary && config.autoOpenLocal) {
      const { lines } = await openArtifactLocally(
        summary.deployed_url,
        summary.download_url
      );
      await push(async (context) => {
        await context.sendActivity(["🖥️ Local actions:", ...lines].join("\n"));
      });
    }
  }
}
