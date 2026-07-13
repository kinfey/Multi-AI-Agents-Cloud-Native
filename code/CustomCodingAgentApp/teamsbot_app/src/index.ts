import * as restify from "restify";
import {
  CloudAdapter,
  ConfigurationBotFrameworkAuthentication,
  ConfigurationServiceClientCredentialFactory,
  TurnContext,
} from "botbuilder";
import { config } from "./config";
import { TeamsBot } from "./teamsBot";

// ---- Bot Framework authentication + adapter ----
const credentialsFactory = new ConfigurationServiceClientCredentialFactory({
  MicrosoftAppId: config.appId,
  MicrosoftAppPassword: config.appPassword,
  MicrosoftAppType: config.appType,
  MicrosoftAppTenantId: config.appTenantId,
});

const botFrameworkAuthentication = new ConfigurationBotFrameworkAuthentication(
  {},
  credentialsFactory
);

const adapter = new CloudAdapter(botFrameworkAuthentication);

adapter.onTurnError = async (context: TurnContext, error: Error) => {
  console.error("[onTurnError]", error);
  try {
    await context.sendActivity("The bot ran into an error. Please try again later.");
  } catch {
    /* ignore */
  }
};

const bot = new TeamsBot(adapter, config.appId);

// ---- HTTP server ----
const server = restify.createServer();
server.use(restify.plugins.bodyParser());

server.post("/api/messages", async (req, res) => {
  const auth = req.headers["authorization"] ? "auth=yes" : "auth=NO";
  const actType = (req as any).body?.type ?? "?";
  const text = (req as any).body?.text ?? "";
  console.log(
    `[inbound] POST /api/messages ${auth} type=${actType} text=${JSON.stringify(String(text).slice(0, 80))}`
  );
  await adapter.process(req, res, (context) => bot.run(context));
});

server.get("/healthz", (_req, res, next) => {
  res.send(200, { status: "ok", mcpUrl: config.mcpUrl });
  return next();
});

server.listen(config.port, () => {
  console.log(`teamsbot_app listening on http://localhost:${config.port}`);
  console.log(`  messaging endpoint: POST /api/messages`);
  console.log(`  MCP service:        ${config.mcpUrl}`);
});
