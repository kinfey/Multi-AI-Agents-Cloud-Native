# Azure OpenClaw ACA Sandbox Demo

> 中文文档见 [README.zh.md](README.zh.md)。

This workspace packages **OpenClaw** for **Azure Container Apps (ACA)** with execution isolated in **ACA Sandbox** instead of Docker or SSH. The result is a multi-agent programming workflow backed by **Microsoft Foundry `gpt-5.5`**, with a public Control UI served by the gateway and sandboxed tool execution delegated into a pre-created ACA Sandbox.

## What this repo delivers

- OpenClaw gateway running in **Azure Container Apps**
- OpenClaw agent sandbox backend switched to **`backend: "aca"`**
- **Microsoft Foundry / Azure AI Foundry** model access via Entra ID
- **Azure Files** mounted for durable config, workspaces, and browser pairing state
- A reviewed Python workflow with five agents:
  1. Requirement Agent
  2. Coding Agent
  3. Testing Agent
  4. Deployment Agent
  5. Save Agent

## Workflow HTTP API and MCP integration

The OpenClaw gateway exposes an **OpenAI-compatible HTTP API**
(`/v1/chat/completions`, `/v1/models`, `/v1/responses`) on its port, treating
the `model` field as an agent target (`openclaw/<agentId>`) and authenticating
with the gateway token. This is enabled via `gateway.http.endpoints.chatCompletions`
in [docker/entrypoint.sh](docker/entrypoint.sh) and
[openclaw/openclaw.json5](openclaw/openclaw.json5).

A companion **MCP service** in [../acamcp_node](../acamcp_node) calls this API to
drive the five-agent workflow and exposes it as Model Context Protocol tools
(`generate_prototype`, `run_agent`, `check_gateway_health`) over streamable HTTP
at `/mcp`. The MCP service is deployed to AKS and points at this gateway's ACA URL.

## Current validated state

- Live app: `azure-openclaw-aca-app`
- Live region: `swedencentral`
- Public URL: `https://azure-openclaw-aca-app.bluedune-876fc257.swedencentral.azurecontainerapps.io`
- Current healthy revision: `azure-openclaw-aca-app--0000013`
- Current image: `azureopenclawaca20260706.azurecr.io/openclaw@sha256:b115feb3ddd37cb8ca18b9bdb3598bf543a1d47a2367dec8e4ba8c488ad89623`

The originally requested Azure target used `rg-kinfey` / `westus`, but the validated live deployment in this workspace is running in **Sweden Central**.

## Why the ACA Sandbox design matters

This project does **not** disable sandbox mode. Instead:

- the OpenClaw gateway stays in **ACA**
- OpenClaw tools run through a custom **ACA sandbox backend**
- the backend uses `aca sandbox exec` and file copy operations
- one pre-created ACA Sandbox is reused through `OPENCLAW_ACA_SANDBOX_ID`
- the coding/testing/deployment/save agents share **one** sandbox workspace
  (`sandbox.scope: "shared"`) so they operate on the same project files

That keeps the execution boundary aligned with ACA Sandbox rather than falling back to local Docker, raw SSH, or sandbox-off mode.

## Shared sandbox workspace (multi-agent file sharing)

The five agents must hand a *single* project directory down the chain: the
Coding Agent writes it, the Testing Agent runs it, the Deployment Agent ships it,
and the Save Agent packages it. That only works when the agents share a workspace.

Configured in [docker/entrypoint.sh](docker/entrypoint.sh):

```json5
agents: {
  defaults: {
    workspace: "/state/openclaw/workspaces",
    sandbox: { mode: "all", backend: "aca", scope: "shared", workspaceAccess: "rw", … },
  },
  list: [
    { id: "requirements-agent", workspace: "/state/openclaw/workspaces/project", … },
    { id: "coding-agent",       workspace: "/state/openclaw/workspaces/project", … },
    { id: "testing-agent",      workspace: "/state/openclaw/workspaces/project", … },
    { id: "deployment-agent",   workspace: "/state/openclaw/workspaces/project", … },
    { id: "save-agent",         workspace: "/state/openclaw/workspaces/project", … },
  ],
}
```

- **`scope: "shared"`** — all agents resolve to the same sandbox scope key
  (`shared`) and therefore the same sandbox workspace, instead of the per-agent
  isolation of `scope: "agent"`. (Supported scopes: `session` | `agent` |
  `shared`.)
- **Unified `workspace` path** — every agent points at
  `/state/openclaw/workspaces/project`, so with `workspaceAccess: "rw"` the
  effective working directory is identical across agents.
- **Fixed `app/` project directory** — the orchestrator's agent prompts require
  the Coding Agent to build inside `app/` (cleaning it on the first turn), the
  Testing Agent to `cd app` before running tests, and the Deployment Agent to
  deploy `app/`.

> **Why it matters:** with the earlier `scope: "agent"` + per-agent workspaces,
> the Coding Agent wrote to `.../coding-agent` while the Testing Agent looked in
> `.../testing-agent` — an empty directory — so every test round failed with
> "files not present". Sharing the workspace is what lets the review gates in the
> MCP orchestrator actually validate real code.

## Architecture

### Markdown architecture diagram

```text
+---------------------------+        +--------------------------------------+
| Browser / Remote CLI      |        | Microsoft Foundry / Azure AI Foundry |
| - Control UI              |        | - gpt-5.5 deployment                 |
| - External operator       |        | - Entra ID auth                      |
+-------------+-------------+        +------------------+-------------------+
              |                                       ^
              | HTTPS / WSS                           |
              v                                       |
+--------------------------------------------------------------------------+
| Azure Container Apps Ingress                                             |
| - Public FQDN                                                            |
| - Routes HTTP Control UI + gateway WebSocket traffic                     |
+----------------------------------+---------------------------------------+
                                   |
                                   v
+--------------------------------------------------------------------------+
| Azure Container App: OpenClaw Gateway                                    |
|                                                                          |
|  OpenClaw Gateway                                                        |
|  - token auth                                                            |
|  - Control UI static assets                                               |
|  - allowedOrigins derived from gateway URL / ACA env vars                |
|  - multi-agent orchestration                                             |
|                                                                          |
|  Custom ACA Sandbox Backend                                              |
|  - backend: \"aca\"                                                       |
|  - calls `aca sandbox exec`                                              |
|  - syncs workspace files into sandbox                                    |
|                                                                          |
|  Azure CLI / ACA CLI                                                     |
|  - managed identity login                                                |
|  - Foundry token acquisition                                             |
+----------------------+----------------------------+----------------------+
                       |                            |
                       | mount                      | sandbox exec / fs cp
                       v                            v
+----------------------------------+    +----------------------------------+
| Azure Files Share                |    | ACA Sandbox Group / Sandbox      |
| - openclaw.json5                 |    | - pre-created sandbox instance   |
| - shared `project` workspace     |    | - isolated command execution     |
|   (app/ built by coding, run by  |    | - scope: "shared" across agents  |
|    testing, shipped by deploy)   |    | - remote workspace under /tmp    |
| - devices/pending.json           |    +----------------------------------+
| - devices/paired.json            |
| - pairing persistence only       |
+----------------------------------+
                       ^
                       |
                       |
+----------------------------------+
| Azure Container Registry         |
| - OpenClaw image                 |
| - digest-pinned rollout          |
+----------------------------------+
```

### Component responsibilities

| Component | Responsibility |
| --- | --- |
| Azure Container App | Hosts the OpenClaw gateway and Control UI |
| OpenClaw gateway | Serves UI, handles auth, orchestrates agents |
| Custom ACA backend | Executes agent tools inside ACA Sandbox |
| ACA Sandbox | Isolated runtime for tool execution; one shared workspace across agents (`scope: "shared"`) |
| Azure Files | Persists config, the shared `project` workspace, and device pairing files |
| Microsoft Foundry | Supplies the `gpt-5.5` model |
| ACR | Stores the deployed OpenClaw image |

## Key implementation adjustments already applied

### Runtime and config fixes

- Fixed the Foundry endpoint handling so legacy `/models` inputs normalize to `/openai/v1`
- Forced the Foundry provider API mode to `openai-responses` for `gpt-5.5`
- Removed the invalid `agents.defaults.tools` config shape
- Kept persistent workspaces on Azure Files while keeping agent-private directories under `/tmp`
- Switched the agents to a **shared sandbox workspace** (`sandbox.scope: "shared"`,
  unified `/state/openclaw/workspaces/project`) so the coding/testing/deployment/save
  chain operates on the same `app/` project (see "Shared sandbox workspace" above)
- On AKS, the sandbox `aca` CLI gets `ACA_SUBSCRIPTION` / `ACA_RESOURCE_GROUP` /
  `ACA_REGION` / `ACA_SANDBOX_GROUP` via the gateway secret, and the kubelet
  managed identity is granted **Container Apps SandboxGroup Data Owner** (both wired
  in [scripts/deploy-aks-gateway.sh](scripts/deploy-aks-gateway.sh)) so
  `aca sandbox exec` works without a subscription flag

### Image and packaging fixes

- The image build now runs `pnpm build:docker` plus `pnpm ui:build`
- Control UI assets are bundled into the image instead of being built on first startup
- Required fallback templates and `tool-display.json` are re-included in the Docker context

### Control UI fixes

- `gateway.controlUi.allowedOrigins` is derived from `OPENCLAW_GATEWAY_URL` or ACA-injected hostname env vars
- External browser origin validation now succeeds
- Browser pairing state is persisted on Azure Files through `OPENCLAW_PAIRING_STATE_DIR=/state/openclaw`
- Only the **pairing files** are persisted there; the rest of the OpenClaw runtime state stays on container-local storage to avoid startup probe failures caused by moving SQLite runtime state onto Azure Files

### ACA deployment and code download

- The Deployment Agent deploys from the shared sandbox using two idempotent shell
  helpers — `deploy-build` (builds `<app>:latest` in ACR) and `deploy-finish`
  (creates the Container App and returns the real HTTPS URL). The MCP orchestrator
  submits the build then polls `deploy-finish`, which tolerates the ACA sandbox
  `exec` ~120s cap because the ACR build and `containerapp create` finish
  server-side. Each run uses a unique `proto-<hex>` app name.
- The Save Agent uses `package_agent_workspaces` to archive Coding and Testing Agent files while excluding credentials and symbolic links
- `/api/saveagent/artifacts/<artifact-id>.zip` reuses gateway Bearer token authentication
- `scripts/download-agent-artifact.py` safely downloads and extracts to an empty local directory, then opens VS Code Insiders (falling back to stable VS Code)

## Project layout

- [app](app): Python workflow app and tests
- [openclaw](openclaw): reference OpenClaw config
- [docker](docker): Docker wrapper, entrypoint, and image customizations
- [infra](infra): Bicep for ACA, ACR, Storage, identity, and mounts
- [scripts](scripts): sandbox setup, build, deploy, RBAC, and smoke-check helpers
- [.work/openclaw](.work/openclaw): local OpenClaw source overlay including the ACA sandbox backend

## Local workflow test

```bash
PYTHONPATH=app python3 -m unittest discover -s app/tests
PYTHONPATH=app python3 app/main.py "Build a Python CLI calculator" --json
```

## Required environment

Copy [.env.example](.env.example) to `.env`, then fill in the missing values:

```bash
AZURE_RESOURCE_GROUP=rg-kinfey
AZURE_LOCATION=swedencentral
AZURE_OPENCLAW_PREFIX=azure-openclaw-aca
ACR_NAME=<globally-unique-acr-name>
STORAGE_ACCOUNT_NAME=<globally-unique-storage-name>
OPENCLAW_IMAGE=<acr-name>.azurecr.io/openclaw:latest
DOCKER_PLATFORM=linux/amd64
OPENCLAW_UPDATE_SOURCE=0
AZURE_AI_FOUNDRY_ENDPOINT=https://<foundry-resource>.services.ai.azure.com/openai/v1
AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT=gpt-5.5
OPENCLAW_GATEWAY_TOKEN=<generated-token>
OPENCLAW_GATEWAY_URL=https://<container-app-fqdn>
OPENCLAW_ACA_SANDBOX_ID=<sandbox-id>
```

## ACA Sandbox setup

Login first:

```bash
az login
```

Provision the sandbox group and create the sandbox:

```bash
scripts/setup-aca-sandbox.sh
scripts/create-sandbox.sh azure-openclaw-aca
```

The setup flow:

- installs the `aca` CLI if needed
- creates the sandbox group
- grants the signed-in user `Container Apps SandboxGroup Data Owner`
- verifies the environment with `aca doctor`
- writes `OPENCLAW_ACA_SANDBOX_ID` back into `.env`

## Build and push the OpenClaw image

```bash
export ACR_LOGIN_SERVER="<acr-name>.azurecr.io"
scripts/build-openclaw-image.sh
```

The script:

- clones `https://github.com/openclaw/openclaw.git` into `.work/openclaw`
- overlays this repo's Dockerfile, dockerignore, and entrypoint
- builds the customized image
- pushes `openclaw:latest` when ACR is configured

## Deploy infrastructure

```bash
scripts/deploy-infra.sh
```

For deterministic rollouts after a rebuild, prefer updating ACA with an image digest instead of relying only on `:latest`.

## Assign Foundry RBAC

```bash
# Set this in .env first:
# AZURE_AI_FOUNDRY_RESOURCE_ID=/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry-resource>
scripts/assign-foundry-rbac.sh
```

The Container App managed identity needs **Cognitive Services OpenAI User** on the Foundry resource.

## Smoke check

```bash
scripts/smoke-check.sh
```

Validated behavior includes:

- gateway startup succeeds
- Control UI assets are served
- external browser origin is accepted
- OpenClaw agent execution reaches ACA Sandbox
- sandboxed turns complete against `microsoft-foundry/gpt-5.5`

## Accessing the Control UI

1. Open the public ACA URL
2. Use the gateway tokenized access flow
3. On first access from a new remote browser, approve the device pairing request

Because pairing files now persist on Azure Files:

- pending requests are stored in `devices/pending.json`
- approved devices are stored in `devices/paired.json`
- browser refreshes no longer depend on fragile ACA `containerapp exec` access for recovery

## Download and open Agent code locally

After the full workflow succeeds, the Save Agent reports a `DOWNLOAD_URL=...` in
its content. Extract that authenticated URL, download the ZIP to a local `code`
directory, extract it, and open it in VS Code Insiders (or stable VS Code):

```bash
export OPENCLAW_GATEWAY_TOKEN='<gateway-token>'
python scripts/download-agent-artifact.py \
  'https://<gateway-fqdn>/api/saveagent/artifacts/<artifact-id>.zip' \
  './code/project'
```

The script safely extracts into the (empty) destination and then opens it with
`code-insiders`, falling back to `code`. Choose the editor explicitly with
`--editor insiders|code|auto` (default `auto`), or pass `--no-open` to skip
opening.

> The MCP client wrapper `acamcp_node/scripts/mcp-curl-test.sh` automates the
> whole loop: it parses `DOWNLOAD_URL` from the streamed content, downloads,
> extracts into `code/project-<timestamp>`, and opens the editor for you.

## Notes

- `azure openclaw_aca` is not a valid Azure resource name, so deployable resources use `azure-openclaw-aca`
- The OpenClaw provider path is `microsoft-foundry/<deployment-name>`, so this repo uses `microsoft-foundry/gpt-5.5`
- This workspace carries a local OpenClaw source overlay to add the ACA sandbox backend
- The ACA backend uses one pre-created sandbox and does not rely on SSH passthrough
- Do not commit generated gateway tokens, device tokens, or Foundry credentials
