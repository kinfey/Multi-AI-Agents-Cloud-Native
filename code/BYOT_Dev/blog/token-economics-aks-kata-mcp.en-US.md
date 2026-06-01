# When Every Token Has a Price Tag: Building a Cost-Aware, Hardware-Isolated MCP Tower on AKS with AI Runway and Kata MicroVM

> *A technical-evangelist deep dive on combining token economics, hybrid model placement, AI Runway, AKS Pod Sandboxing (Kata MicroVM), and the Model Context Protocol — using the [`BYOT_Dev`](../README.md) reference build as the concrete example.*

---

## 1. The moment the bill arrived

For most of 2024 and 2025, "Agents" were a demo word. In 2026 they are a line item on the cloud invoice.

Every major model provider — OpenAI, Anthropic, Google, Mistral, DeepSeek, and even the in-cluster open-weights serving stacks — now bills by the token. Input tokens, output tokens, cached tokens, reasoning tokens, tool-call tokens. The unit price has come down. The number of tokens an autonomous agent burns through has gone up by an order of magnitude.

The slide deck I keep coming back to is module 02 of the [Enterprise Agent Workshop](https://github.com/kinfey/EnterpriseAgenticWorkshop) — *Token Economics and Cost Control*. The short version: an agentic system is **not** a chat app. A chat app emits one model call per user turn. An agent emits a model call to plan, another to pick a tool, another to interpret the tool result, another to decide the next step, and another to summarize — and then it loops. Multiply by tools that themselves invoke models. Multiply again by retries and reflection.

The bill is no longer "what does the model cost per million tokens." The bill is "what does my **architecture** cost per user request."

This post is about an architecture that answers that question on purpose — and that does it without giving up the security properties an enterprise actually needs. The blueprint lives in this repo, [`BYOT_Dev`](../README.md): a four-agent SDLC tower (Requirements → Code → Test → Deploy) running on AKS, each agent boxed inside its own Kata MicroVM, each one exposing tools to GitHub Copilot Chat over the Model Context Protocol, and all of them sharing a single on-cluster small-language-model endpoint served by **AI Runway**.

---

## 2. Why agentic workloads inflate the token bill

Three forces compound:

1. **Autonomy multiplies call count.** A user typing "build me a URL shortener" produces *one* prompt at the IDE. By the time a 4-agent pipeline has clarified requirements, generated code, written tests, and produced a Kubernetes manifest, you have spent 30–200 model calls — most of them invisible to the user.
2. **Reasoning eats output tokens.** Modern reasoning models think before they speak. That hidden chain-of-thought is **billed**. A 5-line answer might charge you for 3,000 reasoning tokens.
3. **Context inflation.** Every tool result is re-injected into the next call. A 50 KB code review answer becomes the context of the next refactor turn. Costs grow super-linearly with conversation depth.

You can't out-prompt-engineer this. The only durable mitigation is **architectural** — and it has three levers:

| Lever | What it means in practice |
|-------|---------------------------|
| **Model tiering** | Use a small, cheap model for narrow tasks; reserve the frontier model for orchestration and judgement. |
| **Placement tiering** | Place each model where it's cheapest to run: on-cluster CPU for tiny SLMs, on-cluster GPU for mid-size models, cloud APIs for frontier reasoning. |
| **Protocol tiering** | Use a standard like MCP so the expensive orchestrator can hand off subtasks to the cheap workers without lock-in. |

The architecture this post describes pulls all three levers at once.

---

## 3. The mental model: frontier brain, small-model hands

Look at this picture:

```
   ┌─────────────────────────────────────────────────┐
   │  GitHub Copilot Chat (IDE)                      │  ← frontier model
   │  user types, Copilot plans                      │     **already included in the Copilot seat**
   │  Copilot's own token budget                     │     — no second pay-per-token meter for inference
   └─────────────────────┬───────────────────────────┘
                         │ MCP (Streamable HTTP)
                         ▼
   ┌─────────────────────────────────────────────────┐
   │  4× Kata MicroVM Pods on AKS                    │  ← small / mid models, on cluster
   │  byot-requirements                              │     (paid as compute, not per token)
   │  byot-code                                      │
   │  byot-test                                      │
   │  byot-deploy                                    │
   └─────────────────────┬───────────────────────────┘
                         │ OpenAI-compatible HTTP (in-cluster)
                         ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │   AI Runway — one OpenAI-compatible front door,                  │
   │   two backends, both on the same AKS cluster                     │
   │                                                                  │
   │   ┌──────────────────┐   ┌──────────────────────┐                │
   │   │ tiny-cpu         │   │ mid-gpu              │                │
   │   │ llama.cpp 1B–3B  │   │ vLLM 7B–14B          │                │
   │   │ D4s_v3 CPU node  │   │ AKS GPU node pool    │                │
   │   │ always-on,       │   │ Cluster Autoscaler   │                │
   │   │ steady state     │   │ scales 0 → N nodes   │                │
   │   └──────────────────┘   └──────────────────────┘                │
   │                                                                  │
   │   ~85% of agent calls    ~15% (heavier refactors, multi-file,    │
   │                                long-context spikes)              │
   └──────────────────────────────────────────────────────────────────┘
```

The economics of this layout:

- The **frontier reasoning** — picking the right tool, sequencing the agents, reading the final result, deciding when the answer is good enough — stays at the top, where the IDE already has a billing relationship via the user's Copilot seat. That seat **already includes** a frontier-model token allowance. You are not adding a new pay-per-token meter for orchestration; you are reusing the one the developer already pays for.
- The **bulk work** — "expand REQ-007 into a checklist", "generate a FastAPI module for these requirements", "write a unit-test plan for this code", "produce a Deployment YAML for this service" — runs on a **small model living inside your cluster**. The marginal cost is CPU-seconds, not API tokens.
- The **heavier work** — multi-file refactors, long-context reasoning, or any spike that would queue behind the small CPU model — **scales onto a GPU node pool on the same AKS cluster** through the same AI Runway front door. No external endpoint, no second per-token bill: AKS spins up a GPU node when AI Runway needs one, runs the larger model under the same Kata isolation, and spins back down to zero when traffic dies.
- The **placement** is a configuration knob. The reference build runs Llama-3.2-1B on a single `Standard_D4s_v3` CPU node. The exact same agent code, with no recompilation, will talk to a 7B model on a GPU node that AKS auto-scales from zero — same cluster, same Kata RuntimeClass, same OpenAI-compatible URL pattern — the day you change one URL in the ConfigMap.

This is the heart of "token economics architecture": **the cheapest call is the one you didn't bill to the most expensive meter** — and **the heaviest call is one you run on AKS capacity that wasn't there a minute ago and won't be there a minute from now.**

---

## 4. AI Runway: the placement abstraction

The reason a single ConfigMap edit moves the workload between CPU, GPU, and cloud is that the agents don't talk to a model — they talk to an **OpenAI-compatible URL**. The thing on the other end of that URL is an [AI Runway](https://github.com/kaito-project/airunway) `ModelDeployment` custom resource.

In [`airunway/modeldeployment-qwen-cpu.yaml`](../airunway/modeldeployment-qwen-cpu.yaml) (and verified in the repo's notes), the CR looks roughly like:

```yaml
spec:
  image: ghcr.io/kaito-project/aikit/llama3.2:1b
  model: { id: "kaito/llama3.2-1b", source: huggingface }
  engine: { type: llamacpp }
  provider:
    name: kaito
    overrides:
      resource:
        instanceType: Standard_D4s_v3
        preferredNodes: ["aks-nodepool1-21523631-vmss000001"]
  nodeSelector: { agentpool: nodepool1 }
  resources: { cpu: "2", memory: "4Gi" }
  scaling: { replicas: 1 }
```

AI Runway then takes care of:

- selecting the **engine** (`llamacpp` for CPU, `vllm` or `dynamo` for GPU);
- selecting the **provider** (`kaito` today, others coming);
- pulling the **model image** from the AIKit catalog;
- exposing an OpenAI-compatible `Service` at `http://llama3-2-1b-cpu.airunway-models.svc:80/v1`.

> **A note on the CPU-only example.** This repo deliberately uses **CPU + Llama-3.2-1B** to prove the architecture can run on the cheapest node SKU available. In production you should not assume CPU is always right. The right answer is **scenario-driven**:
>
> | Scenario | Suggested placement |
> |----------|---------------------|
> | High-volume, narrow, latency-tolerant task (e.g. "expand a requirement into bullet points") | On-cluster CPU SLM (1B–3B) — what this repo demonstrates |
> | Code generation, refactoring, multi-file reasoning | On-cluster GPU mid-model (7B–14B) via KAITO `vllm`, on an AKS GPU pool that auto-scales from zero |
> | Privacy-sensitive enterprise data, must not leave the cluster | On-cluster GPU, possibly with confidential compute |
> | Frontier reasoning, planning, judging tool output | **The Copilot seat's already-included frontier model**, called sparingly via MCP — not a second pay-per-token endpoint you have to provision |
>
> AI Runway makes that choice a YAML edit, not a refactor. The point of the abstraction is **optionality** — the right to change your mind about token economics quarter by quarter without rewriting agents.

---

## 5. Hybrid scaling: all inference on AKS, planning on the Copilot tokens you already pay for

The single biggest token-economics mistake an enterprise can make right now is treating model placement as a binary — "all in the cluster" or "all on a pay-per-token cloud API." Real workloads are neither. The pattern that actually saves money has two ingredients, **and both of them are already on your invoice**:

1. **AKS that you already provisioned.** A small CPU node pool for the steady-state workload, plus a GPU node pool that scales from **zero** when the small pool can't keep up. Same cluster, same Kata isolation, one invoice line.
2. **The Copilot seat the developer already pays for.** Copilot Chat's frontier model has its own token allowance baked into the seat. Use *that* allowance — not a separately provisioned cloud inference endpoint — to do the planning that drives the cheap AKS workers via MCP.

That is the whole "hybrid." No external Foundry endpoint, no second per-token meter for inference. Just AKS capacity that grows when you need it + a frontier brain you already pay for.

The agent traffic split is roughly:

- **~85% of agent calls** are short, narrow, predictable — *"expand this requirement"*, *"format this YAML"*, *"summarize this diff"*. A 1B–3B model on a CPU node answers these in seconds; the bill is the node, not the token.
- **~15%** are heavier — multi-file refactors, long-context reasoning, the 400-line FastAPI generation. They need a 7B–14B model on a GPU.
- **Planning and judgement on top of all of it** are done by the Copilot seat's frontier model, which the user is paying for whether you build BYOT or not.

### Lever A — a GPU node pool on the same AKS cluster, scaled 0 → N

Keep the always-on `tiny-cpu` ModelDeployment for steady state. Add a second AI Runway `ModelDeployment` for the mid model on a GPU node pool that is **created at size zero** and managed by the AKS Cluster Autoscaler (or Karpenter):

```bash
az aks nodepool add \
  --cluster-name $CLUSTER --resource-group $RG \
  --name gpupool \
  --node-vm-size Standard_NC24ads_A100_v4 \
  --node-count 0 --min-count 0 --max-count 4 \
  --enable-cluster-autoscaler \
  --node-taints sku=gpu:NoSchedule \
  --workload-runtime KataVmIsolation
```

```yaml
# airunway/modeldeployment-mid-gpu.yaml (sketch)
spec:
  image: ghcr.io/kaito-project/aikit/qwen2.5:7b
  engine: { type: vllm }
  provider:
    name: kaito
    overrides:
      resource:
        instanceType: Standard_NC24ads_A100_v4
  nodeSelector: { agentpool: gpupool }
  tolerations: [{ key: sku, operator: Equal, value: gpu, effect: NoSchedule }]
  resources: { cpu: "4", memory: "32Gi", nvidia.com/gpu: "1" }
  scaling: { replicas: 0, maxReplicas: 4 }
```

The key trick is `replicas: 0` plus an autoscaler `min-count 0`. **When nobody is asking the mid model anything, no GPU node is running and no GPU node is billed.** The first request causes AI Runway to scale to 1, which triggers the Cluster Autoscaler to provision a GPU node, which gets scheduled with Kata Pod Sandboxing intact. When traffic dies down, both the replica and the node go back to zero. All of this is *inside* AKS — the agents never leave the cluster to find a GPU.

### Lever B — reuse the Copilot frontier tokens you already pay for

This is the lever most token-cost writeups miss. **Every developer using BYOT already has a Copilot seat.** That seat carries a frontier-model token allowance which Copilot Chat consumes the moment the user types into the chat. The orchestration loop in [`docs/workflow.md`](../docs/workflow.md) — *"plan which tool to call next, read the tool's output, summarize the result"* — is paid out of **that** allowance, not out of a new inference endpoint you provision.

This means:

- You do **not** stand up a separate cloud OpenAI / Foundry deployment for "the smart model." The smart model is already on the user's screen.
- You do **not** put a per-token meter on the agent-to-frontier path. The frontier is upstream of your agents — it calls *them* via MCP, not the other way around.
- The only per-token spend the architecture introduces is what Copilot itself charges against the seat, which is independent of how many BYOT agents you stand up.

The net effect: the parts of the workload that are *expensive per token* (planning, judgement) run on tokens the company already buys; the parts that are *cheap to compute* (long-form generation) run on AKS compute you already pay for as node hours.

### How the agents pick between the two AKS backends

The Agent Framework client in [`agents/app/airunway_client.py`](../agents/app/airunway_client.py) takes its `base_url` and `model` from the ConfigMap. Three strategies, in increasing sophistication:

1. **Per-role static binding.** `byot-requirements` and `byot-test` (cheap, narrow) get `AIRUNWAY_BASE_URL=tiny-cpu`. `byot-code` and `byot-deploy` (heavier generation) get `AIRUNWAY_BASE_URL=mid-gpu`. One ConfigMap, one rollout.
2. **Try-then-scale-up inside the tool.** Each tool tries `tiny-cpu` first; if the answer is too short, fails a quality check, or times out, it retries against `mid-gpu`. The small model handles the easy 85%; the GPU pool handles only the 15% that actually needed it.
3. **AI Gateway in front of both.** Put [Azure API Management as an AI Gateway](https://learn.microsoft.com/azure/api-management/genai-gateway-capabilities) in front of the two AI Runway services. The agent talks to one URL; the gateway does semantic caching, token budgeting, and load-aware routing between `tiny-cpu` and `mid-gpu`. Both backends remain in your AKS — the gateway only routes.

### A back-of-envelope token saving

Assume one Copilot Chat session through the BYOT tower fires **30 model calls** at the lower agents. If those 30 went to an external frontier API at, say, $5 / million output tokens with an average 2 K output per call, that is **$0.30 / session** in *additional* lower-tier model spend — stacked on top of what Copilot Chat already charges the seat for planning.

With the hybrid AKS + seat-tokens pattern:

- 25–26 calls (~85%) → `tiny-cpu` on a CPU node that is already running for the always-on agents → **≈ $0 marginal**
- 4–5 calls (~15%) → `mid-gpu`, billed as GPU node hours only while AI Runway has scaled up, and amortised across every concurrent BYOT user that lands on the same node → **≈ $0.02–0.05**
- Planning / judgement → already inside the Copilot seat allowance the developer is paying for → **$0 additional**

A $0.30-per-session pay-per-token outcome collapses toward **≈ $0.02–0.05 of pure AKS compute**, and the GPU bill returns to zero when nobody is asking hard questions. That is the lever. The reason it works is that **AI Runway gives the agents a single in-cluster front door**, AKS gives the cluster elastic GPU capacity it doesn't pay for while idle, and Copilot Chat brings its own pre-paid frontier brain.

---

## 6. Kata MicroVM: the hardware-level helmet for agentic code

Cost is one half of the agentic-workload problem. The other half is what happens **inside** the box you put the agent in.

Earlier this year I published [*Giving the Copilot SDK Agent a "hardware-level helmet" using Kata microVM on AKS*](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/giving-the-copilot-sdk-agent-a-hardware-level-helmet-using-kata-microvm-on-aks/4518668). The argument, compressed:

> A traditional container is an apartment with shared roof — the host Linux kernel. For a hand-written service the tenant is predictable. For an agent, **the tenant is the model**, deciding at runtime which shell command to run, which file to read, which `npx` package to install. That's a new threat model. Container namespaces aren't sized for it. You want a **dedicated guest kernel per Pod** — a microVM.

[Kata Containers](https://katacontainers.io/) is the integration layer that gives Kubernetes microVMs. AKS ships it as **Pod Sandboxing** with the `kata-vm-isolation` RuntimeClass on top of Hyper-V — created automatically when the node pool is provisioned with `--workload-runtime KataVmIsolation`.

In [`BYOT_Dev`](../README.md) every agent Pod sets:

```yaml
spec:
  runtimeClassName: kata-vm-isolation
  containers:
    - name: agent
      securityContext:
        runAsNonRoot: true
        readOnlyRootFilesystem: true
        capabilities: { drop: ["ALL"] }
        seccompProfile: { type: RuntimeDefault }
```

…and AKS does the rest. The Pod boots a real Hyper-V microVM, with its own guest kernel, before the container even starts. Verifying it is one command:

```bash
kubectl -n agents exec deploy/byot-requirements -- uname -r
# compare with the kernel on the node — they differ → microVM confirmed
```

The repository also pins **one agent per node** via `podAntiAffinity` on `kubernetes.io/hostname`, so the four agents live on four physically distinct Kata hosts — a model escape in one cannot reach the others through a shared host kernel, because there is no shared host kernel.

The connection to token economics is this: the moment you trust a cheap on-cluster model to run agent loops on real customer code, the security envelope has to be **stronger** than a normal container, not weaker. Kata is the thing that makes "cheap" and "safe" not a trade-off. And because AKS Pod Sandboxing applies the same way to the CPU pool, the GPU pool, and any future node pool you add for burst, the hybrid placement story above **does not weaken the isolation story** — every Pod, on every tier, still boots its own guest kernel.

---

## 7. MCP: how GitHub Copilot Chat actually drives this tower

The final piece is the protocol. The agents inside the Kata MicroVMs are useless unless something can call them. The "something" the user already has open is **GitHub Copilot Chat in VS Code**.

The [Model Context Protocol](https://modelcontextprotocol.io) is the standard Copilot Chat (and almost every other serious agentic IDE) speaks to remote tool servers. In this repo each role exposes its tools via `FastMCP` over Streamable HTTP — see [`agents/app/main.py`](../agents/app/main.py) and the per-role tool sets in [`agents/app/roles/`](../agents/app/roles/).

Service exposure is a small but important detail. The repo uses `type: LoadBalancer` for each role's Service — see [`k8s/services.yaml`](../k8s/services.yaml) — because:

- `kubectl port-forward` **does not work against Kata Pods** (the listener lives inside the microVM, not in the host sandbox netns);
- `kubectl proxy` works but pins Copilot to `localhost` and requires a long-running local process;
- a LoadBalancer gives each agent a public Azure IP the IDE can hit directly.

Once the four LoadBalancer IPs are in [`.vscode/mcp.json`](../README.md), Copilot Chat in agent mode sees four MCP servers — `byot-requirements`, `byot-code`, `byot-test`, `byot-deploy` — and the user can simply say:

> *"Use the byot tower to take this idea — a URL shortener with click analytics — from requirements through deployment."*

What happens under the covers ([`docs/workflow.md`](../docs/workflow.md)):

1. Copilot's frontier model **plans** the sequence. *Frontier tokens spent: small, but smart.*
2. It calls `byot-requirements.gather_requirements({"idea": "URL shortener…"})` over MCP. *No frontier tokens; the cluster-side Llama-3.2-1B does the work.*
3. It calls `byot-code.implement_from_requirements({...})`. *Same — cluster-side small model.*
4. It calls `byot-test.generate_test_plan({...})`. *Same.*
5. It calls `byot-deploy.generate_k8s_manifest({...})`. *Same.*
6. Copilot's frontier model **reads** the four results and presents a coherent summary to the user. *Frontier tokens spent: small.*

The expensive model decided **what** to do five times. The cheap model did the actual long-form generation four times. That is the token-economics win, and the only reason it's possible without lock-in is that MCP is an open standard.

---

## 8. Reading the architecture as a budget statement

Translate the picture into a unit-cost table — now with the hybrid tiers explicit:

| Layer | Where the cost lives | What controls it |
|-------|----------------------|------------------|
| User input + IDE planning | Copilot seat (per-user subscription) | Already paid — flat rate |
| **Frontier orchestration tokens** | **Copilot seat token allowance — already included, used for MCP planning, no separate endpoint** | Number of agent rounds Copilot does |
| Tool-call traffic | Azure LoadBalancer egress | Negligible at this scale |
| **`tiny-cpu` inference (steady state, ~85%)** | AKS CPU node hours (1× `D4s_v3` in this demo) | Replicas, model size, batch size |
| **`mid-gpu` inference (autoscaled, ~15%)** | AKS GPU node hours on the same cluster, **only while replicas > 0** | Cluster Autoscaler / Karpenter `min=0 max=N`, scale-to-zero |
| Hardware isolation | AKS Pod Sandboxing (Kata) — same node hours | Whether you turn it on (you should) |
| Provider swap-out | AI Runway YAML | A `kubectl apply` |

Three things to notice.

First, **most of the per-request variable cost has moved from a token meter to a node meter**. CPU hours are easier to forecast, easier to chargeback, and easier to cap than per-call token spend. You know how many `D4s_v3` cores you're paying for; you do not know in advance how many tokens a frontier model will decide it needs.

Second, **GPU capacity is no longer a fixed bet, and it never leaves AKS.** The GPU node pool sits at zero nodes until AI Runway needs it, and when it does, it scales up *inside the same cluster* under the same Kata RuntimeClass — no second region, no second tenancy, no second per-token bill.

Third, **the frontier brain is reused, not re-bought.** The planning and judgement that drives the whole tower runs on the Copilot seat token allowance the developer already pays for. There is no separate "smart model" cloud endpoint provisioned by BYOT, so there is no second per-token meter to babysit.

And because Kata Pod Sandboxing is included in AKS and applies the same way on the CPU pool and the GPU pool, **the security cost on top of the compute cost is zero**.

That is what makes this architecture cost-aware **and** elastically-scalable **and** safety-aware at the same time. Those three used to be a trade-off. They no longer are.

---

## 9. Six commands, end-to-end

For completeness, the repo's run order ([`README.md`](../README.md)):

```bash
# 0. one-time prereqs: az login, kubectl, helm, docker, aks-preview
az login

# 1. provision AKS with Kata + ACR + AzureLinux
bash infra/01-create-aks-kata.sh

# 2. install AI Runway controller + KAITO provider (pinned to v0.5.0)
bash infra/02-install-airunway.sh

# 3. deploy Llama-3.2-1B on CPU via AI Runway ModelDeployment
bash infra/03-deploy-qwen.sh

# 4. build & push the single agent image to ACR
bash infra/04-build-push-agents.sh

# 5. deploy the 4 Kata-isolated MCP agents
bash infra/05-deploy-agents.sh

# 6. print the public MCP endpoints for GitHub Copilot
bash infra/06-show-mcp-endpoints.sh
```

Drop the printed IPs into [`.vscode/mcp.json`](../README.md), open Copilot Chat, and you have a fully working, hardware-isolated, cost-aware agentic tower talking to a small model on a CPU node — driven by the frontier model the user is already paying a seat for. Add the `mid-gpu` ModelDeployment on a scale-to-zero GPU node pool alongside it whenever your traffic justifies the next tier; the agents and the Copilot integration don't change, and nothing leaves AKS.

---

## 10. Wrapping up: the through-line

Let me trace it one more time:

1. **Token economics is the new SLO.** Agentic workloads multiply model calls; every call has a price. Architecture, not prompts, is what bends the curve.
2. **Tier your models, tier your placement.** Frontier reasoning at the top; small models for the bulk work; on-cluster CPU for steady state and on-cluster GPU for the heavy 15%.
3. **Mix AKS compute with the Copilot tokens you already pay for.** Don't add a second pay-per-token cloud endpoint for inference. The heavy compute belongs on an AKS GPU pool that scales from zero; the planning belongs on the Copilot seat allowance the developer already has. That combination both **saves tokens** (no new per-token meter) and **scales elastically** (the cluster grows only when AI Runway asks).
4. **AI Runway makes placement a YAML edit.** Today's CPU Llama is tomorrow's GPU Qwen on the same cluster. Same agent code.
5. **Kata MicroVM is non-negotiable for agentic code.** The tenant is the model. The roof must be your own. AKS Pod Sandboxing makes it turnkey — and it applies the same way on the CPU pool and the GPU pool.
6. **MCP is the bridge.** GitHub Copilot Chat is already an MCP client. Expose the cheap workers as MCP tools and the frontier brain calls them — burning the seat tokens, not new tokens.
7. **The reference build is in this repo.** Six commands, four agents, one tiny CPU model, full microVM isolation, real Copilot Chat integration — and a hybrid scaling path you can layer on without changing the agents or leaving AKS.

In the agentic era, a container is not just a box for your application — it is a box for **uncertainty and for tokens**. The microVM hardens the box; AI Runway lets you slide the model in and out of the box, between CPU and GPU nodes in the same cluster, without rewriting anything; MCP lets the user's expensive IDE drive the cheap box from the outside on tokens already on its tab. That is the through-line.

Build the tower. Watch the bill.

---

### Further reading

- *Token Economics and Cost Control* — slide 02 in the [Enterprise Agent Workshop](https://github.com/kinfey/EnterpriseAgenticWorkshop/blob/main/ppt/pdf/en/02.token-economics-cost-control.pdf).
- [Giving the Copilot SDK Agent a "hardware-level helmet" using Kata microVM on AKS](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/giving-the-copilot-sdk-agent-a-hardware-level-helmet-using-kata-microvm-on-aks/4518668).
- [AI Runway](https://github.com/kaito-project/airunway) and [KAITO](https://github.com/kaito-project/kaito).
- [Kata Containers](https://katacontainers.io/) · [AKS Pod Sandboxing](https://learn.microsoft.com/azure/aks/use-pod-sandboxing).
- [Model Context Protocol](https://modelcontextprotocol.io) · [GitHub Copilot Chat MCP support](https://code.visualstudio.com/docs/copilot/mcp).
- This reference build: [`BYOT_Dev`](../README.md) · architecture diagram in [`docs/architecture.md`](../docs/architecture.md) · end-to-end flow in [`docs/workflow.md`](../docs/workflow.md).
