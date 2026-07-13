# Your PM Types a Sentence, Azure Ships an App — Safely

### A blueprint for long-running autonomous agents with OpenClaw, MCP, and the Azure Container Apps sandbox

*By Kinfey Lo · Senior Cloud Advocate, Microsoft*

---

## The one shift that changes everything

For two years, "AI coding" meant autocomplete. A suggestion appears in your editor, you hit tab, you move on. The agent only existed while you were actively typing.

That is no longer the only model. A new category of tools runs **asynchronously and autonomously**: you message the agent from a chat window — Teams, Slack, Telegram — describe what you want, and walk away. The agent plans, writes code, runs tests, deploys, and hands you back a result. Some of them never sleep: they hold a persistent memory, load their own skills, and act on a schedule without being prompted.

This is the world of **OpenClaw**, **Hermes Agent**, and the other long-running autonomous agents that exploded across developer culture in 2026. OpenClaw alone crossed 377,000 GitHub stars and millions of active users, becoming — for a while — the most-starred project on GitHub. You install it with one line, connect a channel, and start delegating from your phone.

The workflow moves from *pair programming* to *delegation and review*. The interactive copilot asks, "What should I write next?" The autonomous agent asks, "What do you need done?"

And that reframing is exactly why three questions now keep architects awake:

1. **Is it safe?** You are handing a self-driving process the ability to run shell commands, touch files, and call APIs. One community report memorably described these agents as a teammate in your group chat who happens to have root access to your codebase. That is not a compliment — it is a threat model.
2. **Can it fit into real multi-agent work?** A single agent is a demo. Production is a *fleet* — specialists that hand off to each other with gates in between.
3. **Is it flexible and controllable?** Autonomy is thrilling right up until the agent packages last week's stale files into this week's deliverable, or loops forever on a failing test.

This post answers all three — not with hand-waving, but with a working reference implementation you can clone today: **[`CustomCodingAgentApp`](https://github.com/kinfey/Multi-AI-Agents-Cloud-Native/tree/main/code/CustomCodingAgentApp)** in the `Multi-AI-Agents-Cloud-Native` repo, an **"Agentic Prototype Factory"** that turns a plain-language idea into a tested, live-on-Azure prototype **without leaving the chat window**.

> A product manager types *"Build a BBC-style World Cup feature page"* in **Microsoft Teams**. Minutes later they get back a **running HTTPS URL** and a **downloadable source ZIP**. Under the hood, five specialized **OpenClaw** agents powered by **Microsoft Foundry `gpt-5.5`** collaborate in a shared sandbox, run real `pytest`/`Jest` suites, and ship the result to **Azure Container Apps** — all orchestrated behind a **Model Context Protocol (MCP)** service so any MCP client (GitHub Copilot, Claude, the Teams bot) can drive it.

We'll build up to that architecture in the order you should learn it.

---

## Part 1 — Long-running autonomous agents, and their two hard problems

### What actually makes them different

A traditional chatbot is *text in, text out*. It waits for you. An autonomous agent inverts that:

| Property | Traditional chatbot | Long-running autonomous agent |
| --- | --- | --- |
| Execution | Responds to a prompt | Acts proactively (a "heartbeat" wakes it on a schedule) |
| Scope | Words | Files, shell, browser, APIs — the real machine |
| Memory | This session only | Persistent across sessions |
| Interface | A web box | Any chat channel + the terminal |
| Autonomy | None | Plans and takes multi-step action on its own |

Architecturally, OpenClaw is not a library you import — it's a **runtime**. A single long-running process (the **Gateway**) bridges your messaging channels to an LLM backend, keeps sessions alive, queues work in ordered lanes, and drives the classic agent loop: *call the model → execute the tool calls it asks for → feed results back → repeat until done*. There is no rigid step-planner; the model itself steers. That is what makes it feel magical — and what makes it hard to contain.

That containment problem has two faces.

### Hard problem #1 — Security

The same properties that make an autonomous agent useful make it dangerous. Full system access + proactive execution + a 32,000-server tool ecosystem is a large, self-driving attack surface. OpenClaw's own short history is the cautionary tale: a **critical one-click remote-code-execution CVE** early in its life, **hundreds of malicious community "skills"** discovered on its marketplace, and **tens of thousands of gateways found exposed on the open internet**. None of this means "don't use autonomous agents." It means: **never run one with ambient credentials on a machine you care about.** The agent belongs in a box with a hard wall around it.

### Hard problem #2 — Persistence and continuity

Real agent work is *long*. Refactoring a codebase, researching across dozens of pages, building-testing-deploying an app — these take minutes to hours, far past a single request/response. So the runtime needs durable sessions, a place to keep state, and a workspace that survives across steps. But a persistent workspace that is *reused* creates its own hazard: **state leakage**. Files from yesterday's task can contaminate — or get shipped inside — today's result. Continuity and cleanliness pull in opposite directions, and you have to engineer the tension out.

### One agent is a demo; production is a fleet

A single monolithic agent asked to "gather requirements, write the code, test it, deploy it, and package it" will do all four *mediocrely* and blur the boundaries between them. The production pattern is **orchestrator-worker**: specialized agents, each with one job, handing off to the next through **explicit gates**. OpenClaw supports exactly this — it can spawn sub-agents and even dispatch external coding harnesses, acting as a meta-orchestrator rather than a single model. The open question is never *whether* to go multi-agent; it's **where the seams and the guardrails go**.

### The answer to "is it safe?": put the agent in a microVM

If the agent needs root to be useful, then give it root — **inside a disposable microVM**, not on your host. In 2026 there are several credible ways to do this:

- **Kata Containers on AKS** — each pod gets its own lightweight VM boundary and guest kernel.
- **Hyperlight Wasm** — per-call, snapshot-restored Wasm microVMs for running LLM-generated code.
- **Azure Container Apps dynamic sessions** — prewarmed, **Hyper-V-isolated** sandboxes that start in **milliseconds**, scale to thousands, and are purpose-built for *"secure execution of custom code"* and *"running LLM-generated scripts."*

That last one — the **ACA sandbox** — is the sweet spot for a chat-driven agent factory: strong isolation without you operating a Kubernetes cluster, and an `exec` API to run commands inside the box. It's what the reference implementation uses.

---

## Part 2 — Putting OpenClaw *into* the ACA sandbox

Here is where the repo stops being a diagram and becomes running code. The **Agentic Prototype Factory** decomposes the "idea → live app" job into **five specialized OpenClaw agents that run in sequence**, all inside the sandbox:

```
requirements  →  coding  →  testing  →  deployment  →  save
```

Each is addressable as its own model target on the OpenClaw gateway's OpenAI-compatible API:

| `model` value | Routes to |
| --- | --- |
| `openclaw` / `openclaw/default` | Default agent |
| `openclaw/requirements-agent` | Requirement Agent |
| `openclaw/coding-agent` | Coding Agent |
| `openclaw/testing-agent` | Testing Agent |
| `openclaw/deployment-agent` | Deployment Agent |
| `openclaw/save-agent` | Save & download Agent |

### Control, not vibes: review gates with feedback loops

Autonomy without gates is how you get an agent that confidently deploys a broken app. The orchestrator wires the five agents into a graph with **hard, bounded gates**:


![arch](./imgs/arch.png)

Every knob is explicit and lives in `server.py`: `_MAX_TEST_ROUNDS = 3`, `_MAX_DEPLOY_REVIEW = 2`, `_DEPLOY_POLL_ATTEMPTS = 12`, `_DEPLOY_POLL_DELAY_S = 20`. The Testing Agent must end each turn with a literal `TESTS_PASSED` / `TESTS_FAILED` verdict; the orchestrator won't declare success until it **HTTP-checks the deployed URL and inspects the response body** — because a `ResourceNotFound` can happily return an HTTP 200. That is what "flexible and controllable" looks like in practice: the LLM drives creatively *inside* a deterministic state machine.

### The deterministic pre-run wipe (solving state leakage)

Because the sandbox is **reused** across runs (fast, cheap), the orchestrator does something disciplined before *every* run: it **wipes all lingering agent workspaces**. Stale files from a previous task can never leak into — or be packaged as — the new result. This is the engineered answer to Hard Problem #2.

### Working *with* the sandbox's limits, not against them

The ACA sandbox `exec` API is **hard-capped at ~120 seconds** — shorter than a cold `az acr build` plus `az containerapp create`. A naive agent would time out and report failure. The clever bit: **those commands finish server-side on Azure even after the client `exec` disconnects.** So deployment is split in two:

1. **`deploy-build <dir> <app>`** — installs the deploy helpers, writes a tight `.dockerignore`, and kicks off the ACR build tagged `<app>:latest`. If the client drops at ~120s, the image still lands in ACR.
2. **`deploy-finish <app>`** — idempotent, polled up to 12×. It reports `STILL_BUILDING` until the image exists, then fires a `--no-wait` `containerapp create`, and finally returns `DEPLOYED_URL=https://<fqdn>`.

This is the single most important lesson of the whole sample: **an autonomous agent doesn't need a longer timeout — it needs to understand the durability semantics of the platform it runs on.**

---

## Part 3 — MCP, and why its security is the whole ballgame

The five-agent workflow is powerful, but it would be a silo if the only way to reach it were a bespoke API. Instead, the repo wraps the entire orchestration as a **Model Context Protocol (MCP)** service (`acamcp_node`) exposed over streamable HTTP at `/mcp`, with a tiny, legible tool surface:

| MCP tool | What it does |
| --- | --- |
| `generate_prototype` | Run the full five-agent workflow end to end |
| `run_agent` | Invoke a single named agent |
| `check_gateway_health` | Liveness / readiness of the OpenClaw gateway |

The payoff is enormous: **any** MCP client can now drive the factory — GitHub Copilot, Claude, or the Teams bot we're about to meet. One protocol, many front-ends.

But MCP is not just an integration convenience — it's a **control plane**, and *every MCP tool is a privileged capability*. In an ecosystem with 32,000+ community servers, "just add an MCP server" is a supply-chain decision. A tool call is code execution by another name. So the security posture has to be deliberate. Here is how the reference implementation hardens it — and the principles are portable to any MCP deployment:

- **Auth in front of the protocol.** The MCP ingress sits behind **basic auth** (`MCP_BASIC_AUTH_PASSWORD`); the gateway itself requires the gateway token as a **bearer credential** (`Authorization: Bearer <token>`). No anonymous tool calls.
- **A tiny, named allowlist — not a blank check.** The gateway routes only to six explicit `model` targets. There is no "run arbitrary agent" escape hatch; the routing table *is* the allowlist.
- **No secrets in the workload.** There are **no model API keys** anywhere in the running containers — model access is brokered entirely through **Entra ID managed identities**. The gateway token is stored as a Kubernetes secret and **never baked into an image**.
- **Private by default.** The gateway's OpenAI-compatible endpoint is **operator-level access** — it stays on **private ingress**, with TLS and authentication added before anything is ever exposed publicly.
- **Least privilege at the identity layer.** The gateway is granted exactly the Foundry roles it needs (`Cognitive Services User` / `Cognitive Services OpenAI User`) on the Foundry resource — nothing more.

The takeaway for MCP is the same as for the agent itself: **treat the protocol as a doorway, and put a guard on the door.** Authentication, an explicit allowlist, private ingress, and brokered identity turn MCP from an open blast radius into a governed control plane.

---

## Part 4 — The complete solution: Teams + MCP on ACA + OpenClaw on the ACA sandbox

Now assemble the three deployable components into one loop:

![app](./imgs/app.png)

### The request lifecycle, end to end

1. A PM sends one sentence in **Teams**. The `teamsbot_app` bot — acting as an **MCP client** via `mcpClient.ts` — opens an MCP handshake and calls `generate_prototype`.
2. The **MCP service on ACA** (`acamcp_node`) runs the orchestrator: pre-run wipe, then requirements → coding → testing.
3. The **OpenClaw gateway in the ACA sandbox** (`acasbxapp_node`) executes each agent, talking to **Foundry `gpt-5.5`** through a **managed identity** — no keys in the box.
4. Real `pytest` + `Jest` suites run inside the sandbox. Fail → loop back (bounded). Pass → deploy.
5. Deployment uses the **build + poll** split to survive the ~120s `exec` cap; the app lands in **Azure Container Apps** and is **health-checked body-aware** at its live URL.
6. The **Save Agent** produces an authenticated **ZIP** download URL. The bot streams each agent's progress back into the Teams thread and returns the **running HTTPS URL + source ZIP** — optionally auto-opening the project in VS Code Insiders.

### How the architecture answers the three questions

| The question | How this solution answers it |
| --- | --- |
| **Is it safe?** | The autonomous agent runs in a **Hyper-V-isolated ACA sandbox**, not on anyone's laptop. **No model keys** in the workload — **Entra ID managed identity** brokers Foundry. MCP behind **basic auth**; gateway behind a **bearer token** on **private ingress**; token as a **secret, never in an image**. A **deterministic pre-run wipe** removes cross-run leakage. |
| **Does it fit multi-agent work?** | It **is** a multi-agent system — five specialist OpenClaw agents with **A2A hand-offs and review gates** — and because it's exposed via **MCP**, *any* client (Copilot, Claude, Teams) can orchestrate it. |
| **Is it flexible and controllable?** | Creativity lives **inside a deterministic state machine**: explicit `TESTS_PASSED/FAILED` verdicts, **bounded** retry loops (`_MAX_TEST_ROUNDS`, `_MAX_DEPLOY_REVIEW`), **body-aware** health checks, and a human approving in the Teams thread. |

### Deploy it yourself

The repo ships scripts for all three tiers (the gateway uses the platform's **managed identity** to reach Foundry — no key handling, no image rebuild):

```bash
# 1) OpenClaw gateway + the 5 agents  (acasbxapp_node)
cd acasbxapp_node
cp .env.example .env               # gateway token, Foundry endpoint, sandbox ids
./scripts/build-openclaw-image.sh  # build + push the OpenClaw image to ACR
./scripts/deploy-aks-gateway.sh    # grant Foundry roles + deploy

# 2) MCP service  (acamcp_node)
cd ../acamcp_node
cp .env.example .env               # ACR + cluster; gateway token read from ../acasbxapp_node/.env
./scripts/build-images.sh          # build + push the MCP image
./scripts/deploy-aks.sh            # secret + manifests to the openclaw namespace
./scripts/smoke-check.sh           # verify the MCP handshake

# 3) Teams bot  (teamsbot_app) — Node.js/TypeScript MCP client
cd ../teamsbot_app
# configure + run per the folder README, then sideload the Teams app package
```

> The reference implementation targets **Azure (ACA + AKS)** — the OpenClaw gateway and MCP service run as containers, and the **code-execution sandbox** uses the **ACA dynamic-sessions `exec` API**. Keep the gateway on private ingress and add TLS before any public exposure.

---

## Final thought

Strip away the World Cup demo and a reusable pattern remains — a blueprint for running *any* long-running autonomous agent in the enterprise:

> **A message-driven agent** (OpenClaw / Hermes) **+ a microVM sandbox** (Azure Container Apps dynamic sessions) **+ an MCP control plane with auth** **+ enterprise identity** (Entra ID managed identity) **+ a human surface** (Microsoft Teams).

The autonomy that made these agents go viral is the same autonomy that makes security teams nervous. You don't resolve that tension by slowing the agent down — you resolve it by **giving it a box with a hard wall, a control plane with a guard on the door, an identity instead of a secret, and a human in the loop.** Do that, and "your PM types a sentence, Azure ships an app" stops being a scary demo and becomes something you can actually put in production.

Clone it, break it, harden it further:
**[`kinfey/Multi-AI-Agents-Cloud-Native` → `code/CustomCodingAgentApp`](https://github.com/kinfey/Multi-AI-Agents-Cloud-Native/tree/main/code/CustomCodingAgentApp)**

*The chat window is the new terminal. Let's make it a safe one.*
