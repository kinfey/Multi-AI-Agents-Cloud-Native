# Agentic Prototype Factory

> **Turn a plain-language idea into a tested, live-on-Azure application prototype —
> without leaving the chat window.**
>
> A product manager types *"Build a BBC-style World Cup feature page"* in
> **Microsoft Teams**; minutes later they get back a **running HTTPS URL** and a
> **downloadable source ZIP**. Under the hood, five specialized **OpenClaw** agents
> (requirements → coding → testing → deployment → save) powered by **Microsoft
> Foundry `gpt-5.5`** collaborate in a shared sandbox, run real **pytest**/**Jest**
> suites, and ship the result to **Azure Container Apps** — all orchestrated behind
> a **Model Context Protocol (MCP)** service so any MCP client (GitHub Copilot,
> Claude, the Teams bot) can drive it.
>
> **Scenario:** rapid prototyping / demo-building for PMs, solution engineers, and
> hackathon teams. **Tech:** OpenClaw · Microsoft Foundry gpt-5.5 · MCP · Azure
> (AKS + ACA) · Entra ID · Microsoft Teams Bot Framework.
> _Repository: `CustomCodingAgentApp`._

A multi-agent programming workflow built on **OpenClaw**, backed by **Microsoft
Foundry `gpt-5.5`**, and exposed to AI clients through a **Model Context
Protocol (MCP)** service. The workflow turns a plain-language requirement into a
reviewed project prototype by running five specialized agents in sequence:
**requirements → coding → testing → deployment → save**.

The solution has three deployable components:

| Component | Role |
| --- | --- |
| [`acasbxapp_node`](acasbxapp_node) | The **OpenClaw gateway** — hosts the five agents, connects to Microsoft Foundry, and exposes an OpenAI-compatible HTTP API. Runs on Azure Container Apps (ACA) **and** on AKS. |
| [`acamcp_node`](acamcp_node) | The **MCP service** — wraps the workflow as MCP tools (`generate_prototype`, `run_agent`, `check_gateway_health`) over streamable HTTP at `/mcp`, following the [ACA standalone MCP hosting model](https://learn.microsoft.com/azure/container-apps/mcp-overview). |
| [`teamsbot_app`](teamsbot_app) | A **Microsoft Teams bot** (Node.js/TypeScript) that acts as an MCP client — send a requirement in Teams and it drives the whole workflow, streams each agent's progress, and returns the deployed URL + source ZIP. Optionally auto-opens the result locally. |

## What it delivers

- A five-agent OpenClaw workflow (Requirement, Coding, Testing, Deployment, Save) with
  agent-to-agent orchestration.
- **A deterministic pre-run wipe**: before every run the orchestrator clears all
  lingering agent workspaces in the reused sandbox, so stale files from a previous
  task can never leak into (or be packaged as) the new result.
- **Review gates with feedback loops** in the MCP orchestrator: the Testing Agent
  actually executes backend tests with **pytest** and frontend tests with **Jest**
  and must emit `TESTS_PASSED`; failures loop back to the Coding Agent (up to 3
  rounds). The deployment is health-checked and, if unreachable, rebuilt+redeployed
  (up to 2 rounds).
- **Robust ACA deployment (split build → poll)**: the ACA sandbox `exec` API is
  hard-capped at ~120s, which is shorter than an `az acr build` + `containerapp
  create`. The orchestrator therefore submits the build (which finishes
  **server-side** in ACR even after the client disconnects) and then **polls** an
  idempotent `deploy-finish` step until the container app reports a real URL —
  instead of failing on the timeout.
- **A unique app name per run** (`proto-<hex>`) plus a body-aware health check
  (detects Azure `ResourceNotFound` even behind HTTP 200), so the reported
  `deployed_url` is always a fresh, genuinely reachable app — never a stale or
  fabricated URL.
- **Honest reporting**: the workflow surfaces `tests_passed` and the verified
  `deployed_url` instead of falsely claiming success.
- **Live progress in Teams**: the Teams bot streams each stage (`[0/5] 🧹 wipe`,
  the project **architecture in Markdown**, the **test cases + pass/fail table**,
  `⏳ polling deploy`, `✓ deployed at <url>`).
- A **shared sandbox workspace** so the Coding, Testing, Deployment, and Save
  agents all operate on the *same* project files (`app/`) — the Testing Agent
  runs the exact code the Coding Agent wrote.
- Controlled ACA deployment that returns the verified HTTPS application URL, followed by an authenticated ZIP download of all Coding and Testing Agent files.
- Model access via **Microsoft Foundry** (`gpt-5.5`) using **Entra ID** — no API
  keys in the workload.
- An **OpenAI-compatible gateway API** (`/v1/chat/completions`, `/v1/models`)
  where the `model` field targets an agent (`openclaw/<agentId>`).
- An **MCP server** so GitHub Copilot, Claude, the Teams bot, or any MCP client
  can invoke the workflow as tools.
- Full **AKS deployment** of both backend components, plus the ACA deployment of
  the gateway and the Teams bot.

## Architecture

The MCP service is the public interface. It calls the OpenClaw gateway over the
gateway's OpenAI-compatible API. The gateway runs the agents against Microsoft
Foundry and (optionally) executes tools in an ACA Sandbox.

```text
        +-------------------------------+      +-------------------------------+
        |         MCP client            |      |   Microsoft Teams  (chat)     |
        | (GitHub Copilot / Claude / …) |      |   via teamsbot_app (MCP client)|
        +---------------+---------------+      +---------------+---------------+
                        |                                      |
                        +------------------+-------------------+
                                           |  MCP streamable HTTP (JSON-RPC 2.0)
                                           |  POST /mcp
                                           v
              +--------------------------------------------------+
              |  acamcp_node  —  MCP service (FastMCP)            |
              |  endpoint: /mcp                                   |
              |  tools:                                          |
              |    - generate_prototype  (runs the 5-agent chain)|
              |    - run_agent           (one agent)             |
              |    - check_gateway_health                        |
              +-----------------------+--------------------------+
                                      |  HTTPS + Bearer token
                                      |  POST /v1/chat/completions
                                      |  model = openclaw/<agentId>
                                      v
              +--------------------------------------------------+
              |  acasbxapp_node  —  OpenClaw gateway             |
              |  port 18789  (token auth, Control UI, /v1 API)   |
              |                                                  |
              |  agents:  requirements-agent -> coding-agent     |
              |           -> testing-agent -> deployment-agent   |
              |           -> save-agent                          |
              +------------+----------------------+--------------+
                           |                      |
                           | Entra ID token       | aca sandbox exec / fs
                           v                      v
        +---------------------------+   +----------------------------+
        | Microsoft Foundry         |   | ACA Sandbox (isolated      |
        | gpt-5.5 deployment        |   | tool execution)            |
        +---------------------------+   +----------------------------+
```

### Review gates and feedback loops

`generate_prototype` is not a blind linear chain — the MCP orchestrator
(`acamcp_node/app/server.py`) drives a **gated state machine** that reviews each
agent's output and loops back on failure:

```text
   [0/5] deterministic workspace wipe  (clears the reused sandbox)
        |
        v
requirements-agent
        |
        v
   coding-agent  <----------------------+  (feed test failures back, up to 3 rounds)
        |                                |
        v                                |
   testing-agent  --- TESTS_FAILED ------+
        |  (runs real backend tests with pytest + frontend tests with Jest)
        |  TESTS_PASSED
        v
 deploy-build  (submit ACR build; finishes server-side even past the ~120s cap)
        |
        v
 poll deploy-finish  x12 (20s apart) --- STILL_BUILDING/STILL_DEPLOYING ---+
        |  DEPLOYED_URL=...                                                 |
        v                                                                   |
   health review: HTTP GET the deployed URL (body-aware: catches           |
        |  ResourceNotFound behind HTTP 200)                               |
        |  unreachable? -> fix + rebuild under a FRESH app name, re-poll ---+ (x2)
        |  reachable
        v
    save-agent  -> authenticated ZIP download URL
```

Gate parameters (in `server.py`): `_MAX_TEST_ROUNDS = 3`,
`_MAX_DEPLOY_REVIEW = 2`, `_DEPLOY_POLL_ATTEMPTS = 12`, `_DEPLOY_POLL_DELAY_S = 20`.
The Testing Agent ends every turn with a `TESTS_PASSED` / `TESTS_FAILED` verdict;
the orchestrator health-checks the deployed URL with an HTTP request (inspecting
the response body, not just the status code) before declaring success. The final
summary carries `tests_passed` and the verified `deployed_url`.

### Why the deployment is split into build + poll

The ACA **sandbox `exec` API is hard-capped at ~120 seconds** — shorter than a
cold `az acr build` plus `az containerapp create`. Crucially, both of those
commands run **server-side** and finish on Azure even after the client `exec`
disconnects. So the orchestrator does not try to do it all in one call:

1. **`deploy-build <dir> <app>`** — installs the deploy helpers, writes a
   `.dockerignore` to keep the context small, and starts the ACR build tagged
   `<app>:latest`. If the client disconnects at ~120s, the image still lands in
   ACR. The Dockerfile `EXPOSE` port is saved for the next step.
2. **`deploy-finish <app>`** (idempotent, polled up to 12×) — reports
   `STILL_BUILDING` until the image exists, then kicks a `--no-wait`
   `containerapp create`, then reports `DEPLOYED_URL=https://<fqdn>` once the app
   reaches `Succeeded`.

This is what turned the old `MCP error -32001: Maximum total timeout exceeded` /
`Network issue — retry policy expired` deploy failures into reliable deployments.

### Shared sandbox workspace

The Coding, Testing, Deployment, and Save agents run in **one shared ACA sandbox
workspace** (`sandbox.scope: "shared"`, all agents pointed at
`/state/openclaw/workspaces/project` in `acasbxapp_node/docker/entrypoint.sh`).
The Coding Agent writes the project into a fixed `app/` subdirectory; the Testing
Agent `cd app` and runs the very same files; the Deployment Agent deploys `app/`;
the Save Agent packages it. Without this, each agent had an isolated workspace and
the Testing Agent saw an empty directory — so tests could never pass.

### Deployment topology

Two live deployments exist. On AKS the entire path is self-contained.

```text
Azure Container Apps (existing)                Azure Kubernetes Service (AKS)
+--------------------------------+             namespace: openclaw
| azure-openclaw-aca-app         |             +------------------------------------------+
| OpenClaw gateway (Dockerfile.  |             |  Ingress (managed NGINX)                 |
| openclaw), Azure Files /state, |             |  http://<public-ip>/mcp   -> acamcp-server|
| system-assigned identity ->    |             |  http://<public-ip>/      -> acasbxapp-gw |
| Foundry                        |             +-------------------+----------------------+
+--------------------------------+                                 |
                                                +------------------v-----------------------+
                                                |  acamcp-server  (Service :80 -> :8000)   |
                                                |    MCP /mcp                              |
                                                +------------------+-----------------------+
                                                                   | http://acasbxapp-gateway:18789
                                                +------------------v-----------------------+
                                                |  acasbxapp-gateway (Service :18789)      |
                                                |    OpenClaw gateway + /v1 API            |
                                                |    auth: AKS kubelet managed identity    |
                                                |          -> Microsoft Foundry gpt-5.5    |
                                                +------------------------------------------+
```

### Component responsibilities

| Component | Responsibility |
| --- | --- |
| MCP service (`acamcp_node`) | Public MCP interface; orchestrates the agent chain via the gateway API; runs review gates + split build→poll deployment |
| OpenClaw gateway (`acasbxapp_node`) | Runs agents, exposes `/v1/chat/completions`, manages token auth and Control UI |
| Teams bot (`teamsbot_app`) | MCP client for Microsoft Teams; async/proactive result, streams per-stage progress, optional local auto-open |
| Microsoft Foundry | Serves the `gpt-5.5` model, accessed with Entra ID |
| ACA Sandbox | Isolated runtime for agent tool execution; one **shared** workspace (`app/`) across the coding/testing/deployment/save agents |
| Azure Container Registry | Stores the `openclaw`, `acamcp-server`, and per-run prototype images |
| AKS kubelet managed identity | Grants the in-cluster gateway access to Foundry via IMDS |

## Repository layout

```text
CustomCodingAgentApp/
  acasbxapp_node/            OpenClaw gateway (agents + Foundry + sandbox)
    app/                     Python workflow model + tests
    docker/                  Dockerfile.openclaw, entrypoint.sh
    infra/                   Bicep for the ACA deployment
    k8s/                     gateway.yaml (AKS)
    openclaw/                openclaw.json5 + agent definitions
    scripts/                 build-openclaw-image.sh, deploy-infra.sh,
                             deploy-aks-gateway.sh, …
  acamcp_node/               MCP service (orchestrator)
    app/                     config, gateway_client, server (5-agent chain +
                             review gates + split-deploy), tests
    docker/                  Dockerfile
    k8s/                     namespace, acamcp-server, ingress
    scripts/                 build-images.sh, deploy-aks.sh, smoke-check.sh,
                             mcp-curl-test.sh
  teamsbot_app/              Microsoft Teams bot (MCP client)
    src/                     index.ts, teamsBot.ts, mcpClient.ts,
                             localActions.ts, cards.ts, config.ts
    appManifest/             Teams app package (manifest.json + icons)
```

Component documentation:

- OpenClaw gateway + ACA Sandbox: [acasbxapp_node/README.md](acasbxapp_node/README.md)
  ([中文](acasbxapp_node/README.zh.md))
- MCP service: [acamcp_node/README.md](acamcp_node/README.md)
  ([中文](acamcp_node/README.zh.md))
- Teams bot: [teamsbot_app/README.md](teamsbot_app/README.md)
  ([中文](teamsbot_app/README.zh.md))

## Gateway API contract

The gateway exposes an OpenAI-compatible surface (enabled via
`gateway.http.endpoints.chatCompletions`). The `model` field is an **agent
target**:

| `model` value | Routes to |
| --- | --- |
| `openclaw` / `openclaw/default` | Default agent |
| `openclaw/requirements-agent` | Requirement Agent |
| `openclaw/coding-agent` | Coding Agent |
| `openclaw/testing-agent` | Testing Agent |
| `openclaw/deployment-agent` | Deployment Agent |
| `openclaw/save-agent` | Save and download Agent |

Authentication uses the gateway token as a bearer credential
(`Authorization: Bearer <token>`).

## Deploy

### OpenClaw gateway on AKS (`acasbxapp_node`)

```bash
cd acasbxapp_node
cp .env.example .env               # set gateway token, Foundry endpoint, sandbox ids
./scripts/build-openclaw-image.sh  # build + push the openclaw image to ACR
./scripts/deploy-aks-gateway.sh    # grant Foundry roles + deploy to AKS
```

The gateway authenticates to Microsoft Foundry with the **AKS kubelet managed
identity** (granted `Cognitive Services User` / `Cognitive Services OpenAI User`
on the Foundry resource). No image rebuild or workload identity is required.

### MCP service on AKS (`acamcp_node`)

```bash
cd acamcp_node
cp .env.example .env               # set ACR + cluster; gateway token read from ../acasbxapp_node/.env
./scripts/build-images.sh          # build + push the MCP image
./scripts/deploy-aks.sh            # secret + manifests to the openclaw namespace
./scripts/smoke-check.sh           # verify the MCP handshake
```

### Teams bot (`teamsbot_app`)

```bash
cd teamsbot_app
cp .env.example .env                # set MCP_URL + Basic auth, bot appId/secret
npm install && npm run build
npm start                           # local run (Bot Framework Emulator or Teams)
```

Send any requirement in Teams and the bot drives `generate_prototype`
asynchronously, streaming each stage back and finally posting the deployed URL +
source ZIP. With `AUTO_OPEN_LOCAL=true` (local runs only) it opens `deployed_url`
in your browser, downloads the ZIP into `DOWNLOAD_DIR`, extracts it, and opens it
in VS Code / VS Code Insiders. Full Teams registration + ACA hosting steps are in
[teamsbot_app/README.md](teamsbot_app/README.md).

## Use it

Connect any MCP client to the public endpoint (`mcp.json`):

```json
{
  "servers": {
    "openclaw-workflow": {
      "type": "http",
      "url": "http://<public-ip>/mcp"
    }
  }
}
```

Or call it directly:

```bash
conda activate agentdev
python - <<'PY'
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
async def main():
    async with streamablehttp_client("http://<public-ip>/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("generate_prototype",
                {"requirement": "Build a REST API for a todo list",
                 "output_dir": "./code"})   # where to download + extract (optional)
            print(res.structuredContent["summary"])
asyncio.run(main())
PY
```

The download + extract is **built into the tool**: the archive is fetched to
`output_dir` (default `~/Downloads`) and extracted (`summary.saved_dir`). Opening
the result in an editor is a separate, client-side step (the MCP server may be
remote). Extract the Save Agent `DOWNLOAD_URL` and open the project in VS Code
Insiders (falling back to stable VS Code):

```bash
cd acasbxapp_node
export OPENCLAW_GATEWAY_TOKEN='<gateway-token>'
python scripts/download-agent-artifact.py \
  'https://<gateway-fqdn>/api/saveagent/artifacts/<artifact-id>.zip' \
  './code/project'   # --editor insiders|code|auto
```

Or let the MCP client wrapper do the whole loop (handshake → stream → parse
`DOWNLOAD_URL` → download → extract into `code/project-<timestamp>` → open the
editor):

```bash
cd acamcp_node
MCP_BASIC_AUTH_PASSWORD='<mcp-ingress-password>' \
OPENCLAW_GATEWAY_TOKEN='<gateway-token>' \
CODE_DIR="$PWD/code" \
./scripts/mcp-curl-test.sh "Build a REST API for a todo list"
```

## Security notes

- The gateway's OpenAI-compatible endpoint is **operator-level access** guarded
  by the gateway token. Keep it on private ingress in production; add TLS and
  authentication before public exposure.
- No model API keys live in the workload — model access is brokered through
  Entra ID managed identities.
- The gateway token is stored as a Kubernetes secret, never baked into an image.

---

中文文档见 [README.zh.md](README.zh.md).
