# teamsbot_app — Microsoft Teams bot for the OpenClaw MCP workflow

> 中文文档见 [README.zh.md](README.zh.md)。

A **Microsoft Teams** chat bot (Node.js + TypeScript, Bot Framework) that acts as
an **MCP client** for the [`acamcp_node`](../acamcp_node) OpenClaw workflow MCP
service. Send a plain-language requirement in Teams and the bot drives the full
multi-agent workflow (requirements → coding → testing → deployment → save),
then **proactively** returns the deployed URL and source-download link.

## Architecture

```text
+-------------------+   Bot Framework    +--------------------------+  MCP (streamable HTTP)
|   Microsoft Teams | <----------------> |  teamsbot_app (this repo)| ----------------------+
|   chat client     |   /api/messages    |  restify + CloudAdapter  |                       |
+-------------------+                    |  @modelcontextprotocol   |                       v
                                         +--------------------------+        +---------------------------+
                                                     ^                       |  acamcp_node MCP server   |
                                                     |  proactive result     |  (FastMCP, /mcp on AKS)   |
                                                     +---- card push --------|  generate_prototype,      |
                                                                             |  run_agent,               |
                                                                             |  check_gateway_health     |
                                                                             +-------------+-------------+
                                                                                           | HTTPS + gateway token
                                                                                           v
                                                                             +---------------------------+
                                                                             |  acasbxapp_node gateway   |
                                                                             |  (OpenClaw on ACA)        |
                                                                             +---------------------------+
```

The bot **does not** reimplement the workflow — it only calls the MCP tools. All
model orchestration, review gates and sandbox execution stay in `acamcp_node` /
`acasbxapp_node`.

## Why async / proactive messaging

`generate_prototype` runs the entire gated workflow and takes **~8–10 minutes**,
far beyond Teams' ~15-second turn timeout. So the bot:

1. **Acknowledges immediately** with a "🚀 已开始生成原型" Adaptive Card and
   captures the conversation reference.
2. Runs `generate_prototype` **in the background**.
3. **Proactively pushes** a result Adaptive Card (deployed URL, download ZIP,
   `tests_passed`, per-stage token usage) via `CloudAdapter.continueConversationAsync`.

## Commands

| You type | Bot does |
| --- | --- |
| any requirement, e.g. `做一个 BBC 风格的世界杯专题页` | Runs `generate_prototype` (async, proactive result). |
| `health` | Calls `check_gateway_health` and returns the JSON. |
| `agent <agentId> <message>` | Calls `run_agent` for a single agent (`requirements-agent`, `coding-agent`, `testing-agent`, `deployment-agent`, `save-agent`). |
| `help` | Shows the command list. |

## Artifacts note (why no auto VS Code open)

The MCP server runs in a **remote AKS pod**, and Teams is a hosted chat surface —
neither can open VS Code or write to your machine. The bot therefore surfaces the
**`download_url`** (source ZIP) and **`deployed_url`** as buttons on the result
card. To open the code locally, use the client-side helper in the repo:
`acamcp_node/scripts/mcp-curl-test.sh` (downloads + extracts to `code/` and opens
VS Code) or `acasbxapp_node/scripts/download-agent-artifact.py`.

### Auto-open the result (local runs only)

When you run the bot **on your own machine** (`npm start`) you can have it open
the result automatically. Set `AUTO_OPEN_LOCAL=true` (plus `OPENCLAW_GATEWAY_TOKEN`)
in `.env`; after a workflow finishes the bot will:

1. open `deployed_url` in your **default browser**,
2. download `download_url` (gateway-authenticated ZIP) into `DOWNLOAD_DIR`
   (default `./Downloads`),
3. extract it, and
4. open the extracted folder in **VS Code / VS Code Insiders**
   (`EDITOR_PREFERENCE=auto|insiders|code`; falls back to the macOS app bundle
   and `open -a`).

It then posts a "🖥️ 本地动作" summary of what it did back into Teams. This flag has
**no effect on the ACA-hosted bot** — the container can't reach your browser or
editor, so leave it `false` in the cloud deployment.

## Project layout

```text
teamsbot_app/
├── package.json          # deps: botbuilder, @modelcontextprotocol/sdk, restify
├── tsconfig.json
├── .env.example          # Bot + MCP configuration
├── src/
│   ├── index.ts          # restify server + CloudAdapter + /api/messages
│   ├── config.ts         # env-driven config (+ self-signed TLS opt-out)
│   ├── mcpClient.ts       # MCP streamable-HTTP client wrapper
│   ├── localActions.ts    # local browser/download/VS Code open (opt-in)
│   ├── teamsBot.ts        # ActivityHandler: commands + async workflow
│   └── cards.ts          # Adaptive Card builders (ack / result / error)
└── appManifest/          # Teams app package (manifest.json + icons)
```

## Configuration

Copy `.env.example` → `.env` and fill in the values.

| Variable | Purpose |
| --- | --- |
| `MICROSOFT_APP_ID` / `MICROSOFT_APP_PASSWORD` | Azure Bot registration credentials. Leave empty for local Emulator testing. |
| `MICROSOFT_APP_TYPE` / `MICROSOFT_APP_TENANT_ID` | `MultiTenant` (default) / `SingleTenant` / `UserAssignedMSI`. |
| `PORT` | Messaging endpoint port (default `3978`). |
| `MCP_URL` | MCP endpoint (default `https://74.241.158.87.nip.io/mcp`). |
| `MCP_BASIC_AUTH_USER` / `MCP_BASIC_AUTH_PASSWORD` | Ingress Basic auth. |
| `MCP_TLS_INSECURE` | `true` to skip TLS verify for the self-signed nip.io host. |
| `MCP_TOOL_TIMEOUT_MS` | Per-tool-call timeout (default `900000` = 15 min). |
| `AUTO_OPEN_LOCAL` | `true` (local runs only) to auto open browser + download + open VS Code. |
| `DOWNLOAD_DIR` | Where to save/extract the source (default `./Downloads`). |
| `EDITOR_PREFERENCE` | `auto` / `insiders` / `code`. |
| `OPENCLAW_GATEWAY_TOKEN` | Bearer token to download the artifact ZIP (from `acasbxapp_node/.env`). |

## Run locally

```bash
cd teamsbot_app
cp .env.example .env          # adjust as needed
npm install
npm run build
npm start                     # or: npm run dev  (ts-node, no build step)
```

Test the messaging pipeline with the
[Bot Framework Emulator](https://github.com/microsoft/BotFramework-Emulator):
point it at `http://localhost:3978/api/messages` (leave App ID/Password blank).
Health probe: `curl http://localhost:3978/healthz`.

### Verify MCP connectivity only

```bash
node -e 'require("dotenv").config();require("./dist/mcpClient.js").mcpService.checkGatewayHealth().then(h=>console.log(h))'
```

## Deploy to Teams

The steps below mirror the actual deployment used for this repo (Entra app →
service principal → Azure Bot → Azure Container Apps → Teams package).

### 1. Register the identity (Entra app + service principal)

```bash
# App registration (single-tenant is required — multi-tenant bot creation is deprecated).
APP_ID=$(az ad app create \
  --display-name "OpenClaw Teams Bot" \
  --sign-in-audience AzureADMyOrg \
  --query appId -o tsv)

# ⚠️ REQUIRED: create the service principal (enterprise app) for APP_ID.
# `az ad app create` alone does NOT create it; without the SP the bot cannot
# acquire a Bot Framework token and will silently NEVER reply in Teams
# (token error AADSTS7000229 "missing service principal in the tenant").
az ad sp create --id "$APP_ID"

# Client secret → MICROSOFT_APP_PASSWORD in .env.
az ad app credential reset --id "$APP_ID" --display-name "teamsbot-secret" --years 1 --query password -o tsv
```

Put `APP_ID`, the secret, `MICROSOFT_APP_TYPE=SingleTenant`, and your tenant id
into `.env` (`MICROSOFT_APP_ID` / `MICROSOFT_APP_PASSWORD` / `MICROSOFT_APP_TENANT_ID`).

### 2. Create the Azure Bot + Teams channel

```bash
az bot create -g rg-kinfey -n openclaw-teams-bot \
  --app-type SingleTenant --appid "$APP_ID" \
  --tenant-id "$MICROSOFT_APP_TENANT_ID" \
  --endpoint "https://REPLACE_WITH_PUBLIC_HOST/api/messages"
az bot msteams create -g rg-kinfey -n openclaw-teams-bot   # enable Teams channel
```

### 3. Deploy to Azure Container Apps

```bash
cd teamsbot_app
az containerapp up -n acateams-app -g rg-kinfey \
  --environment azure-openclaw-aca-env \
  --source . --ingress external --target-port 3978

# Store secrets, then wire env vars (password/MCP pass via secretref).
az containerapp secret set -g rg-kinfey -n acateams-app \
  --secrets app-password="$MICROSOFT_APP_PASSWORD" mcp-pass="$MCP_BASIC_AUTH_PASSWORD"
az containerapp update -g rg-kinfey -n acateams-app --set-env-vars \
  MICROSOFT_APP_ID="$MICROSOFT_APP_ID" MICROSOFT_APP_TYPE=SingleTenant \
  MICROSOFT_APP_TENANT_ID="$MICROSOFT_APP_TENANT_ID" \
  MICROSOFT_APP_PASSWORD=secretref:app-password \
  MCP_URL="$MCP_URL" MCP_BASIC_AUTH_USER="$MCP_BASIC_AUTH_USER" \
  MCP_BASIC_AUTH_PASSWORD=secretref:mcp-pass \
  MCP_TLS_INSECURE=true MCP_TOOL_TIMEOUT_MS=900000 PORT=3978
```

### 4. Point the bot at the ACA endpoint

```bash
az bot update -g rg-kinfey -n openclaw-teams-bot \
  --endpoint "https://acateams-app.<env-fqdn>/api/messages"
# Sanity check: GET /healthz on the app should return {"status":"ok",...}
```

### 5. Package + upload the Teams app

`manifest.json` must use a valid schema — **`packageName` is not allowed from
manifest v1.17 onward** (`additionalProperties: false`), so it is omitted here.

```bash
# Substitute ${{MICROSOFT_APP_ID}} and zip manifest + icons.
zip -j teamsAppPackage.zip appManifest/manifest.json appManifest/color.png appManifest/outline.png
```

Upload the zip to Teams (*Apps → Manage your apps → Upload a custom app*).

### Troubleshooting: bot never replies

| Symptom | Cause | Fix |
| --- | --- | --- |
| No reply, no inbound logs, token request fails with **AADSTS7000229** | App registration has **no service principal** | `az ad sp create --id <APP_ID>` |
| `POST /api/messages` returns 401 for real Teams traffic | Wrong `MICROSOFT_APP_PASSWORD` / stale secret (a new `credential reset` invalidates old ones) | Reset the secret and update the `app-password` ACA secret |
| Multi-tenant bot creation fails | `az bot create --app-type MultiTenant` is deprecated | Use `SingleTenant` (+ set the app `--sign-in-audience AzureADMyOrg`) |

> The `color.png` / `outline.png` in `appManifest/` are simple generated
> placeholders — replace them with real 192×192 and 32×32 icons before publishing.
