import * as dotenv from "dotenv";

dotenv.config();

/** Runtime configuration read from environment variables (.env). */
export interface AppConfig {
  port: number;
  appId: string;
  appPassword: string;
  appType: string;
  appTenantId: string;
  mcpUrl: string;
  mcpBasicAuthUser: string;
  mcpBasicAuthPassword: string;
  mcpToolTimeoutMs: number;
  mcpMaxTotalTimeoutMs: number;
  mcpTlsInsecure: boolean;
  autoOpenLocal: boolean;
  downloadDir: string;
  editor: string;
  gatewayToken: string;
}

function num(value: string | undefined, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function bool(value: string | undefined, fallback: boolean): boolean {
  if (value == null || value === "") return fallback;
  return /^(1|true|yes|on)$/i.test(value.trim());
}

export const config: AppConfig = {
  port: num(process.env.PORT, 3978),
  appId: process.env.MICROSOFT_APP_ID ?? "",
  appPassword: process.env.MICROSOFT_APP_PASSWORD ?? "",
  appType: process.env.MICROSOFT_APP_TYPE ?? "MultiTenant",
  appTenantId: process.env.MICROSOFT_APP_TENANT_ID ?? "",
  mcpUrl: process.env.MCP_URL ?? "https://74.241.158.87.nip.io/mcp",
  mcpBasicAuthUser: process.env.MCP_BASIC_AUTH_USER ?? "",
  mcpBasicAuthPassword: process.env.MCP_BASIC_AUTH_PASSWORD ?? "",
  mcpToolTimeoutMs: num(process.env.MCP_TOOL_TIMEOUT_MS, 900_000),
  // Absolute cap for the whole generate_prototype call. The per-request
  // timeout above is reset on every progress notification, but this total cap
  // is NOT — so it must be large enough for a full run incl. test/deploy retry
  // loops, or the client throws "Maximum total timeout exceeded" (-32001).
  mcpMaxTotalTimeoutMs: num(process.env.MCP_MAX_TOTAL_TIMEOUT_MS, 3_600_000),
  mcpTlsInsecure: bool(process.env.MCP_TLS_INSECURE, false),
  // Only meaningful when the bot runs on the user's own machine (npm start).
  // On ACA the bot cannot reach the user's browser / VS Code, so keep it off.
  autoOpenLocal: bool(process.env.AUTO_OPEN_LOCAL, false),
  downloadDir: process.env.DOWNLOAD_DIR ?? "~/Downloads",
  editor: (process.env.EDITOR_PREFERENCE ?? "auto").toLowerCase(),
  gatewayToken: process.env.OPENCLAW_GATEWAY_TOKEN ?? "",
};

// The nip.io ingress presents a self-signed certificate. Allow opting out of
// TLS verification for that case (never enable this against a trusted CA host).
if (config.mcpTlsInsecure) {
  process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
}
