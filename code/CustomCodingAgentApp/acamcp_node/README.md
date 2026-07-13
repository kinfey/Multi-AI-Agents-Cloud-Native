# acamcp_node — OpenClaw Workflow MCP Service

> 中文文档见 [README.zh.md](README.zh.md)。

An **MCP (Model Context Protocol)** server that exposes the OpenClaw multi-agent
programming workflow as tools for AI clients (GitHub Copilot, Claude, custom
clients). It follows the Azure Container Apps
[standalone MCP server](https://learn.microsoft.com/azure/container-apps/mcp-overview)
pattern (official SDK, streamable HTTP at `/mcp`) and is deployed to **AKS**.

The workflow backend is **acasbxapp_node**, the OpenClaw gateway (built from
`acasbxapp_node/docker/Dockerfile.openclaw`) already running on **Azure Container
Apps** (`azure-openclaw-aca-app`). The MCP server calls the gateway's
OpenAI-compatible HTTP API — it does not reimplement the workflow.

## Architecture

```text
+---------------------+     MCP (streamable HTTP, JSON-RPC 2.0)
|   MCP client        | ------------------------------------------+
| (Copilot / Claude)  |                                           |
+---------------------+                                           v
                                              +-----------------------------------+
                                              | acamcp-server (this repo) on AKS  |
                                              | FastMCP, endpoint /mcp:8000       |
                                              | tools: generate_prototype,        |
                                              |        run_agent,                 |
                                              |        check_gateway_health       |
                                              +------------------+----------------+
                                                                 | HTTPS + Bearer token
                                                                 | POST /v1/chat/completions
                                                                 | model = openclaw/<agentId>
                                                                 v
                                              +-----------------------------------+
                                              | acasbxapp_node = OpenClaw gateway |
                                              | on Azure Container Apps           |
                                              | agents: requirements/coding/      |
                                              |         testing/deployment/save   |
                                              | model: Microsoft Foundry gpt-5.5  |
                                              | tools run in ACA Sandbox          |
                                              +-----------------------------------+
```

The gateway's OpenAI-compatible endpoint treats the `model` field as an *agent
target* (`openclaw/<agentId>`) and authenticates with the gateway token as a
bearer credential. See the gateway docs `docs/gateway/openai-http-api.md`.

## MCP capabilities

| Type | Name | Description |
| --- | --- | --- |
| Tool | `generate_prototype` | Runs the full **gated** workflow (requirements → coding → testing → deployment → save) with review gates and feedback loops (see below). Returns a summary including `tests_passed` and the health-checked `deployed_url`. |
| Tool | `run_agent` | Sends one message to a single agent and returns its reply. |
| Tool | `check_gateway_health` | Reports the OpenClaw gateway health. |
| Resource | `workflow://info` | Ordered workflow stages and gateway info. |
| Prompt | `prototype_request` | Template for requesting a prototype. |

## Review gates and feedback loops

`generate_prototype` is not a blind linear chain. `app/server.py` drives a
**gated state machine** that reviews each agent's output and loops back on
failure so that a green result actually reflects working, deployed code:

```text
   [0/5] deterministic workspace wipe (clears the reused sandbox)
      |
      v
requirements-agent
      |
      v
  coding-agent  <-----------------------+  (test failures fed back, up to _MAX_TEST_ROUNDS)
      |                                  |
      v                                  |
  testing-agent --- TESTS_FAILED --------+
      |  (runs backend tests with pytest + frontend tests with Jest)
      |  TESTS_PASSED
      v
 deploy-build  (submit ACR build; finishes server-side past the ~120s exec cap)
      |
      v
 poll deploy-finish  x_DEPLOY_POLL_ATTEMPTS --- STILL_BUILDING/STILL_DEPLOYING --+
      |  DEPLOYED_URL=...                                                         |
      v                                                                          |
  health review: HTTP GET the deployed URL (body-aware: catches                  |
      |  ResourceNotFound behind HTTP 200)                                       |
      |  unreachable? -> fix + rebuild under a FRESH app name, re-poll ----------+ (_MAX_DEPLOY_REVIEW)
      |  reachable
      v
   save-agent  -> token-guarded ZIP download URL
```

Gate parameters (constants in `app/server.py`):

| Gate | Constant | Value | Behavior |
| --- | --- | --- | --- |
| Test gate | `_MAX_TEST_ROUNDS` | `3` | Testing agent ends each round with `TESTS_PASSED` / `TESTS_FAILED`; on failure the errors are fed back to the coding agent, up to N rounds. |
| Deploy poll | `_DEPLOY_POLL_ATTEMPTS` / `_DEPLOY_POLL_DELAY_S` | `12` / `20` | After submitting the build, the orchestrator polls the idempotent `deploy-finish` step up to N times (delay seconds apart) until it reports a real `DEPLOYED_URL=`. This tolerates the ACA sandbox `exec` ~120s hard cap, since the ACR build and `containerapp create` complete server-side. |
| Health review | `_MAX_DEPLOY_REVIEW` | `2` | The orchestrator HTTP-GETs the deployed URL (inspecting the body, so a stale `ResourceNotFound` behind HTTP 200 counts as unreachable); if unreachable it fixes + rebuilds under a fresh `proto-<hex>` app name and re-polls before declaring success. |

The final summary carries `tests_passed` and the verified `deployed_url` and
reports them **honestly** (e.g. `deployed_url: null` if deployment never became
reachable) rather than masking failures.

### Split build → poll deployment

The ACA sandbox `exec` API is hard-capped at ~120s, shorter than a cold
`az acr build` + `az containerapp create`. Because both run **server-side** and
survive the client disconnect, the deploy is split into two idempotent helpers
(installed in the sandbox via a base64 self-heal command):

- **`deploy-build <dir> <app>`** — writes a `.dockerignore`, builds `<app>:latest`
  in ACR, and saves the Dockerfile `EXPOSE` port. Tolerates the ~120s disconnect.
- **`deploy-finish <app>`** — polled: `STILL_BUILDING` → `--no-wait`
  `containerapp create` → `DEPLOYED_URL=https://<fqdn>` once `Succeeded`.

A **deterministic pre-run wipe** (the `[0/5]` stage) clears every lingering agent
workspace in the reused sandbox first, so stale files from a previous run cannot
leak into or be packaged as the new result.

The coding/testing/deployment/save agents run in a **shared sandbox workspace**
(configured in `acasbxapp_node/docker/entrypoint.sh`, `scope: "shared"`), all
operating on a fixed `app/` project directory — that is what lets the test gate
validate the code the coding agent actually wrote. See the
[acasbxapp_node README](../acasbxapp_node/README.md#shared-sandbox-workspace-multi-agent-file-sharing).

## Layout

```text
acamcp_node/
  app/
    __init__.py
    config.py           # env-driven settings (gateway URL/token, agents)
    gateway_client.py   # OpenClawGatewayClient (/v1/chat/completions)
    server.py           # FastMCP server + tools (build_server factory)
    __main__.py         # runs streamable HTTP transport
    tests/test_server.py
  docker/Dockerfile
  k8s/
    namespace.yaml
    acamcp-server.yaml  # Deployment + Service (gateway token via secret)
    ingress.yaml        # managed NGINX (app routing) ingress
  scripts/
    build-images.sh     # build + push the MCP image
    deploy-aks.sh       # create gateway-token secret + apply manifests
    smoke-check.sh
  requirements.txt
  pyproject.toml
```

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `OPENCLAW_GATEWAY_URL` | ACA app URL | OpenClaw gateway base URL |
| `OPENCLAW_GATEWAY_TOKEN` | _(empty)_ | Gateway bearer token (K8s secret in AKS) |
| `GATEWAY_TIMEOUT_SECONDS` | `300` (config default; AKS manifest sets `1800`) | Per-agent call timeout |
| `OPENCLAW_LOCAL_SAVE_DIR` | `~/Downloads` | Default download/extract dir when `output_dir` is omitted |
| `MCP_HOST` | `0.0.0.0` | MCP bind host |
| `MCP_PORT` | `8000` | MCP bind port |

## Prerequisite: enable the gateway HTTP API

The gateway's OpenAI-compatible endpoint is disabled by default. This repo
enables it in `acasbxapp_node/docker/entrypoint.sh` and `openclaw/openclaw.json5`:

```json5
gateway: { http: { endpoints: { chatCompletions: { enabled: true } } } }
```

After changing it, rebuild the OpenClaw image and roll a new ACA revision:

```bash
cd ../acasbxapp_node && ./scripts/build-openclaw-image.sh
az containerapp update -n azure-openclaw-aca-app -g rg-kinfey \
  --image <acr>/openclaw@<new-digest>
```

> Security: this endpoint is operator-level access guarded by the gateway token.
> Keep it on private ingress in production; add TLS and auth for public exposure.

## Local development

```bash
conda activate agentdev
cd acamcp_node
pip install -r requirements.txt

OPENCLAW_GATEWAY_URL=https://azure-openclaw-aca-app.bluedune-876fc257.swedencentral.azurecontainerapps.io \
OPENCLAW_GATEWAY_TOKEN=<token> \
MCP_HOST=127.0.0.1 MCP_PORT=8010 python -m app
# MCP endpoint: http://127.0.0.1:8010/mcp
```

### Run tests

```bash
conda activate agentdev
cd acamcp_node
python -m pytest -q
```

## Deploy to AKS

```bash
cd acamcp_node
cp .env.example .env    # fill AZURE_RESOURCE_GROUP, AKS_CLUSTER_NAME, ACR_NAME
# OPENCLAW_GATEWAY_TOKEN is read from ../acasbxapp_node/.env if left blank.

./scripts/build-images.sh   # build + push acamcp-server
./scripts/deploy-aks.sh      # secret + manifests to the openclaw namespace
./scripts/smoke-check.sh     # port-forward + MCP handshake
```

## Connect an MCP client

Deployed to AKS (`kinfey-aks-openclaw-cluster`, namespace `openclaw`) and
published through the app routing ingress. Public endpoint:

```
http://74.241.158.87/mcp
```

VS Code `mcp.json`:

```json
{
  "servers": {
    "openclaw-workflow": {
      "type": "http",
      "url": "http://74.241.158.87/mcp"
    }
  }
}
```

## Download and open the result locally

When `generate_prototype` finishes, the Save Agent emits a `DOWNLOAD_URL=...` in
its content and the tool summary surfaces it as `download_url` (plus
`deployed_url` and `tests_passed`). **The download + extract is built into the
tool** — the archive is fetched to `output_dir` (default `~/Downloads`, or
`OPENCLAW_LOCAL_SAVE_DIR`) and extracted automatically; `summary.saved_zip` and
`summary.saved_dir` point at the results.

Opening the project in an editor is a **separate, client-side step** — the MCP
server may run in a remote pod, so it does not launch your local VS Code. The
bundled client wrapper runs the whole loop (handshake → stream → parse
`DOWNLOAD_URL` → download → extract into `code/project-<timestamp>` → open the
editor **on your machine**):

```bash
MCP_BASIC_AUTH_PASSWORD='<mcp-ingress-password>' \
OPENCLAW_GATEWAY_TOKEN='<gateway-token>' \
CODE_DIR="$PWD/code" \
./scripts/mcp-curl-test.sh "Build a World Cup single-page app (FastAPI + HTML/CSS/JS)"
```

`CODE_DIR` defaults to `<repo>/code`. The script prefers `code-insiders` and
falls back to `code`, then to the macOS `open -a` application launcher. To open a
ZIP you already downloaded, use
[`acasbxapp_node/scripts/download-agent-artifact.py`](../acasbxapp_node/scripts/download-agent-artifact.py)
(`--editor insiders|code|auto`).
