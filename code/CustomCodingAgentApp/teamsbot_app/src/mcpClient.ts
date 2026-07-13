import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { LoggingMessageNotificationSchema } from "@modelcontextprotocol/sdk/types.js";
import { config } from "./config";

/** Shape of the `generate_prototype` tool summary (see acamcp_node/app/server.py). */
export interface PrototypeSummary {
  requirement: string;
  stages: Array<{
    agent: string;
    description: string;
    chars: number;
    tokens: { prompt: number; completion: number; total: number };
  }>;
  total_tokens: { prompt: number; completion: number; total: number };
  download_url: string | null;
  deployed_url: string | null;
  tests_passed: boolean;
  saved_zip: string | null;
  saved_dir: string | null;
  output_dir: string;
}

export interface PrototypeResult {
  summary: PrototypeSummary;
  artifacts: unknown[];
}

/** A progress notification streamed by the MCP server while a tool runs. */
export type ProgressListener = (message: string) => void;

function basicAuthHeader(): Record<string, string> {
  if (!config.mcpBasicAuthPassword) return {};
  const raw = `${config.mcpBasicAuthUser || "mcp"}:${config.mcpBasicAuthPassword}`;
  return { Authorization: `Basic ${Buffer.from(raw).toString("base64")}` };
}

/**
 * A thin wrapper around the official MCP TypeScript SDK that connects to the
 * acamcp_node MCP server over streamable HTTP and calls its tools.
 *
 * A fresh connection is opened per call to keep the (long-running) tool
 * invocations isolated — `generate_prototype` can take 8-10 minutes.
 */
export class McpService {
  private async withClient<T>(fn: (client: Client) => Promise<T>): Promise<T> {
    const transport = new StreamableHTTPClientTransport(new URL(config.mcpUrl), {
      requestInit: { headers: basicAuthHeader() },
    });
    const client = new Client(
      { name: "teamsbot-app", version: "1.0.0" },
      { capabilities: {} }
    );
    try {
      await client.connect(transport);
      return await fn(client);
    } finally {
      await client.close().catch(() => undefined);
    }
  }

  /** Extract the first text block from an MCP tool result and JSON-parse it. */
  private parseStructured<T>(result: any): T | null {
    if (result?.structuredContent) return result.structuredContent as T;
    const block = Array.isArray(result?.content)
      ? result.content.find((c: any) => c?.type === "text")
      : undefined;
    if (!block?.text) return null;
    try {
      return JSON.parse(block.text) as T;
    } catch {
      return null;
    }
  }

  /** Run the full gated multi-agent workflow. Long-running (~8-10 min). */
  async generatePrototype(
    requirement: string,
    onProgress?: ProgressListener
  ): Promise<PrototypeResult> {
    return this.withClient(async (client) => {
      // Agent/stage status text is delivered by the server as MCP log
      // notifications (ctx.info -> notifications/message), not as numeric
      // progress. Register a handler so we can surface each agent's status.
      if (onProgress) {
        client.setNotificationHandler(LoggingMessageNotificationSchema, (n) => {
          const data: unknown = n.params?.data;
          const text = typeof data === "string" ? data : JSON.stringify(data);
          if (text) onProgress(text);
        });
        await client.setLoggingLevel("info").catch(() => undefined);
      }

      const result = await client.callTool(
        { name: "generate_prototype", arguments: { requirement } },
        undefined,
        {
          timeout: config.mcpToolTimeoutMs,
          resetTimeoutOnProgress: true,
          maxTotalTimeout: config.mcpMaxTotalTimeoutMs,
          onprogress: (p) => {
            // Numeric stage progress (fallback text only if no message).
            if (p.message) onProgress?.(p.message);
          },
        }
      );
      const parsed = this.parseStructured<PrototypeResult>(result);
      if (!parsed) {
        throw new Error("generate_prototype returned an unrecognised payload");
      }
      return parsed;
    });
  }

  /** Send one message to a single OpenClaw agent. */
  async runAgent(agentId: string, message: string): Promise<string> {
    return this.withClient(async (client) => {
      const result = await client.callTool(
        { name: "run_agent", arguments: { agent_id: agentId, message } },
        undefined,
        { timeout: config.mcpToolTimeoutMs, resetTimeoutOnProgress: true }
      );
      const parsed = this.parseStructured<{ content: string }>(result);
      return parsed?.content ?? "(no content)";
    });
  }

  /** Report the backing gateway health. */
  async checkGatewayHealth(): Promise<unknown> {
    return this.withClient(async (client) => {
      const result = await client.callTool({ name: "check_gateway_health", arguments: {} });
      return this.parseStructured(result) ?? result;
    });
  }
}

export const mcpService = new McpService();
