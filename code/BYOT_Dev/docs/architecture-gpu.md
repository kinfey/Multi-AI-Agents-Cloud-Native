# Architecture

```
                           ┌──────────────────────────────────────────────────────────────────┐
                           │                          AKS Cluster                             │
                           │                                                                  │
  ┌──────────────────┐     │   ┌────────────────────────────┐   ┌──────────────────────────┐  │
  │  VS Code +       │     │   │ Namespace: airunway-system │   │ Namespace: agents        │  │
  │  GitHub Copilot  │     │   │  ┌──────────────────────┐  │   │                          │  │
  │  Chat (Agent)    │     │   │  │ airunway-controller  │  │   │  ┌────────────────────┐  │  │
  │                  │     │   │  │ airunway-kaito-prov  │  │   │  │ Pod (Kata microVM) │  │  │
  └────────┬─────────┘     │   │  └──────────────────────┘  │   │  │ runtimeClassName:  │  │  │
           │ MCP (HTTP)    │   └────────────┬───────────────┘   │  │ kata-vm-isolation  │  │  │
           ▼               │                │ (creates)         │  │  ┌──────────────┐  │  │  │
  ┌──────────────────┐     │                ▼                   │  │  │ FastMCP /mcp │  │  │  │
  │  kubectl proxy   │  ───┼──► Services / Pods               ─►│  │  │ + Starlette  │  │  │  │
  │  localhost:8001  │     │                                    │  │  │ + Agent Fwk  │  │  │  │
  └──────────────────┘     │   ┌────────────────────────────┐   │  │  └─────┬────────┘  │  │  │
                           │   │ Namespace: airunway-models │   │  │        │ OpenAI    │  │  │
                           │   │  ┌──────────────────────┐  │   │  │        ▼ REST      │  │  │
                           │   │  │ ModelDeployment      │  │◄──┘  └────────────────────┘  │  │
                           │   │  │  qwen3.6-27b         │  │      (one Deployment / role) │  │
                           │   │  │  engine: llamacpp    │  │      requirements / code /   │  │
                           │   │  │  provider: kaito     │  │      test / deploy           │  │
                           │   │  │  model: Qwen3.6-27B  │  │                              │  │
                           │   │  └──────────────────────┘  │                              │  │
                           │   │  Service ClusterIP :8000   │                              │  │
                           │   └────────────────────────────┘                              │  │
                           └──────────────────────────────────────────────────────────────────┘

         Hardening per Kata pod:
         • runtimeClassName: kata-vm-isolation   → microVM + isolated guest kernel
         • runAsNonRoot, readOnlyRootFilesystem, cap drop ALL, seccomp RuntimeDefault
         • NetworkPolicy: only DNS + airunway-models egress
```

## Data flow (one tool call)

1. User asks Copilot Chat: *"Generate requirements for a URL shortener."*
2. Copilot's MCP client opens an HTTP request to `http://<byot-requirements-LB-IP>/mcp` (public Azure LoadBalancer IP).
3. Azure LoadBalancer forwards into the cluster; AKS routes the request through the `kata-vm-isolation` RuntimeClass, which boots a Hyper-V microVM and dispatches into the agent container.
4. The agent's `FastMCP` server receives the JSON-RPC `tools/call` for `gather_requirements`.
5. The tool body builds an Agent Framework `Agent` with `OpenAIChatCompletionClient(base_url=$AIRUNWAY_BASE_URL, model=$AIRUNWAY_MODEL, api_key=…)`.
6. The agent issues a Chat Completions request to AI Runway's `Service/llama3-2-1b-cpu:8000/v1/chat/completions`.
7. KAITO's llama.cpp pod runs inference on CPU, returns a completion.
8. The tool returns the answer as the MCP tool result. Copilot Chat surfaces it in the IDE.

## Why one image with `AGENT_ROLE`

All four agents share identical scaffolding (Agent Framework client + FastMCP + health probes); only their **prompts + tool sets** differ. A single image keeps the build/push step fast and the manifests near-identical — each agent's Deployment just sets `env: AGENT_ROLE=requirements|code|test|deploy`.

## Why MCP over Streamable HTTP (not stdio)

* Copilot Chat supports remote MCP servers via HTTP.
* Each Kata pod is a network-reachable Service; stdio would require running the agent process locally.
* Stateless mode keeps tool calls idempotent and shard-friendly for replicas > 1.

## Why Azure LoadBalancer instead of `kubectl proxy` / `port-forward`

`kubectl port-forward` does not work against Kata pods because the listener lives inside the microVM, not inside the sandbox netns on the host. Switching the four agent Services to `type: LoadBalancer` gives each agent a public Azure IP that Copilot Chat can reach directly — no local proxy required.
