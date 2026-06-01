# BYOT — Bring Your Own Tower of Agents

End-to-end reference build on AKS that combines:

| Layer | Component | Role |
|-------|-----------|------|
| Model serving | **AI Runway** (KAITO / llama.cpp, CPU) hosting `Qwen/Qwen3-0.6B` | OpenAI-compatible inference endpoint |
| Agent runtime | **Microsoft Agent Framework** (`agent_framework.openai`) | Wraps the AI Runway endpoint as `OpenAIChatCompletionClient` |
| Isolation | **Kata MicroVM** (`kata-vm-isolation` RuntimeClass on AzureLinux) | One hardened microVM per agent, **pinned one-per-node** via `podAntiAffinity` |
| Protocol | **Model Context Protocol** (`FastMCP` over Streamable HTTP) | Each agent exposes its skills as MCP tools |
| Client | **GitHub Copilot Chat** via `.vscode/mcp.json` | Validates the 4 agents from the IDE |

The 4 agents form a small SDLC tower:

```
Requirements → Code → Test → Deploy
   (R-Agent)   (C-Agent) (T-Agent) (P-Agent)
       │           │         │         │
       └───────────┴────┬────┴─────────┘
                        ▼
        OpenAI-compatible API on AI Runway
                        │
                        ▼
          Qwen/Qwen3-0.6B on KAITO (CPU)
```

> The user prompt referenced `Qwen/Qwen3.5-0.6B`. That tag does not exist on Hugging Face today; this project deploys **`Qwen/Qwen3-0.6B`** (the model AI Runway's own docs use as the canonical small-CPU example). Edit [airunway/modeldeployment-qwen-cpu.yaml](airunway/modeldeployment-qwen-cpu.yaml) if you want a different tag, e.g. `Qwen/Qwen2.5-0.5B-Instruct`.

---

## Repository layout

```
.
├── infra/                                  # Shell scripts: AKS + AI Runway + agents
│   ├── 01-create-aks-kata.sh               # AKS w/ KataVmIsolation, AzureLinux, ACR
│   ├── 02-install-airunway.sh              # AI Runway controller + KAITO provider + Helm install
│   ├── 03-deploy-qwen.sh                   # Apply ModelDeployment for Qwen3-0.6B
│   ├── 04-build-push-agents.sh             # Build & push the single agent image to ACR
│   ├── 05-deploy-agents.sh                 # Apply all agent manifests
│   └── 06-show-mcp-endpoints.sh            # Print public LoadBalancer URLs for Copilot MCP
├── airunway/
│   └── modeldeployment-qwen-cpu.yaml       # The ModelDeployment CRD (provider: kaito, engine: llamacpp)
├── agents/                                 # One image, four roles
│   ├── Dockerfile                          # Python 3.12 image, non-root, read-only rootfs friendly
│   ├── requirements.txt
│   └── app/
│       ├── main.py                         # Starlette: /healthz + /readyz + FastMCP at /mcp
│       ├── airunway_client.py              # Builds OpenAIChatCompletionClient → AI Runway
│       └── roles/
│           ├── __init__.py                 # Role registry (selects by AGENT_ROLE)
│           ├── requirements_agent.py       # MCP tools: gather_requirements, clarify_requirement, ...
│           ├── code_agent.py               # MCP tools: implement_from_requirements, write_module, refactor_code, review_code
│           ├── test_agent.py               # MCP tools: generate_test_plan, generate_test_cases, ...
│           └── deploy_agent.py             # MCP tools: generate_dockerfile, generate_k8s_manifest, ...
├── k8s/
│   ├── namespace.yaml
│   ├── configmap.yaml                      # AIRUNWAY_BASE_URL + AIRUNWAY_MODEL
│   ├── deployment-requirements.yaml        # runtimeClassName: kata-vm-isolation
│   ├── deployment-code.yaml
│   ├── deployment-test.yaml
│   ├── deployment-deploy.yaml
│   ├── services.yaml                       # LoadBalancer svc for each role (public IP)
│   └── networkpolicy.yaml                  # Restrict egress to AI Runway + DNS
├── .vscode/
│   └── mcp.json                            # 4 MCP servers via Azure LoadBalancer URLs
└── docs/
    ├── architecture.md
    └── workflow.md
```

---

## Run order

```bash
# 0. one-time prereqs: az login, kubectl, helm, docker, aks-preview extension
az login

# 1. provision AKS with Kata + ACR + AzureLinux
bash infra/01-create-aks-kata.sh

# 2. install AI Runway controller + KAITO provider
bash infra/02-install-airunway.sh

# 3. deploy Qwen/Qwen3-0.6B on CPU
bash infra/03-deploy-qwen.sh
kubectl -n airunway-models wait --for=condition=Ready modeldeployment/llama3-2-1b-cpu --timeout=20m

# 4. build & push the agent image to ACR
bash infra/04-build-push-agents.sh

# 5. deploy the 4 Kata-isolated MCP agents
bash infra/05-deploy-agents.sh

# 6. print public MCP endpoints for GitHub Copilot
bash infra/06-show-mcp-endpoints.sh
```

Then open VS Code in this folder. The bundled `.vscode/mcp.json` registers all four agents as MCP servers (using the LoadBalancer IPs printed by step 6 — update them if you re-create the cluster). In Copilot Chat (agent mode) you should see the tools:

| Server | Sample tools |
|--------|--------------|
| `byot-requirements` | `gather_requirements`, `clarify_requirement`, `produce_requirements_doc` |
| `byot-code`         | `implement_from_requirements`, `write_module`, `refactor_code`, `review_code` |
| `byot-test`         | `generate_test_plan`, `generate_test_cases`, `review_coverage` |
| `byot-deploy`       | `generate_dockerfile`, `generate_k8s_manifest`, `produce_deploy_plan` |

Ask Copilot, for example: *"Use the byot tower to take this idea — a URL shortener — from requirements through deployment."* — and let Copilot orchestrate the four MCP agents.

---

## Why these pieces fit

* **KAITO/llama.cpp** is the only AI Runway provider with `cpuSupport: true`, so it can serve Qwen3-0.6B without any GPU node (the dynamo provider referenced in the prompt is GPU-only — see [providers.md](https://github.com/kaito-project/airunway/blob/main/docs/providers.md)). The dynamo manifest is still useful as a structural reference and is mirrored in the install script for users who later want a GPU path.
* **Kata `kata-vm-isolation`** gives each agent its own guest kernel; a tool-call escape stays inside the ephemeral microVM. AKS auto-creates the RuntimeClass when `--workload-runtime KataVmIsolation` is set.
* **One agent per node.** [01-create-aks-kata.sh](infra/01-create-aks-kata.sh) provisions **5 nodes** (1 for system + the AI Runway model, 4 for agents), and every agent Deployment carries a `requiredDuringSchedulingIgnoredDuringExecution` `podAntiAffinity` on `app.kubernetes.io/part-of=byot` with `topologyKey: kubernetes.io/hostname`. The scheduler therefore refuses to co-locate any two BYOT agents, giving you four physically distinct Kata hosts — verified at the end of [05-deploy-agents.sh](infra/05-deploy-agents.sh).
* **MCP over Streamable HTTP** is what GitHub Copilot Chat uses for remote MCP servers. Each agent Service is `type: LoadBalancer`, so AKS assigns a public Azure IP and Copilot Chat reaches the Kata pod directly — no `kubectl proxy` and no port-forward (which doesn't work against Kata pods because the listener lives inside the microVM).

See [docs/architecture.md](docs/architecture.md) for the full diagram and [docs/workflow.md](docs/workflow.md) for an example end-to-end flow.
