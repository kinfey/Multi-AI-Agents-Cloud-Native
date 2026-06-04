# Harness Agents on Hyperlight: Running Untrusted Agent Code Inside MicroVMs

> A technical evangelism walkthrough of a small but real workload — a daily
> Mandarin podcast pipeline for the FIFA World Cup 2026 — built on
> [Microsoft Agent Framework][maf]'s harness agents and running every line
> of model-generated Python inside a [Hyperlight][hl] microVM via
> [`hyperlight-sandbox`][hls]. We'll start with *why* microVMs matter for
> agents, then walk down through Hyperlight, the harness pattern, and
> finally how it all comes together as an AKS-native pipeline.

---

## 1. Why MicroVMs Matter for Agents

LLM agents have one inconvenient truth: **the model writes code, and the
code runs**. As soon as you give an agent a `python` tool, a `bash` tool,
or even a "fetch this URL" tool, you have a remote code execution
primitive whose author is a probabilistic text generator. Prompt
injection, jailbreaks, and plain hallucinated `rm -rf` are no longer
research papers — they're Tuesday.

Process-level sandboxes (subprocess + seccomp, Python `exec`, Docker on
its own) were never designed for this threat model. They share a kernel
with the host; one CVE in the kernel's syscall surface is one CVE between
your agent and your laptop. Containers raise the bar but not the
boundary.

**MicroVMs change the boundary.** A microVM is a stripped-down virtual
machine — no firmware, no PCI bus, only the hardware the workload
actually needs — booted in milliseconds on top of KVM, Hyper-V, or MSHV.
Firecracker proved the model for AWS Lambda; gVisor and Kata advanced
it; and as Docker's own team
[recently wrote][docker-microvm], it is now the default architecture
behind Docker Sandboxes precisely because *"a kernel-level boundary is
the right boundary when the code inside is untrusted."*

For agents, microVMs unlock three properties that matter:

1. **Hardware-enforced isolation** — even a kernel exploit in the guest
   stops at the hypervisor. The host kernel and the host filesystem are
   not in scope for the model.
2. **Snapshot/restore in milliseconds** — every tool call can start from
   a clean state. Secrets, env vars, sockets, and "I remembered to
   `import os`" all vanish between invocations.
3. **Tiny, embeddable footprint** — no QEMU, no init system, no
   `/usr/lib/...`. The VM is a function call from the host process,
   which means the agent's runtime can *own* the sandbox lifecycle
   instead of shelling out to Docker.

That last property is what brings us to Hyperlight.

## 2. Hyperlight and `hyperlight-sandbox`

[**Hyperlight**][hl] is Microsoft's open-source, lightweight Virtual
Machine Manager designed to be **embedded inside an application
process**. From its README:

> *"Hyperlight is a lightweight VMM designed to be embedded within
> applications. It enables safe execution of untrusted code within
> micro virtual machines with very low latency and minimal overhead."*

The differentiators vs. classic microVM stacks:

- **No guest OS, no kernel.** Hyperlight runs a *bare* guest — typically
  a Wasm runtime or a tiny C ABI — directly on the hypervisor (KVM on
  Linux, MSHV on Azure, Hyper-V on Windows). VM startup is sub-millisecond
  because there is nothing to boot.
- **Designed to be called like a library.** The host application
  *creates* a sandbox, *calls into* a guest function, gets a value back,
  and *destroys* the sandbox — synchronously, in the same process.
- **Snapshot-per-call.** The sandbox can be reset to a known-good
  snapshot before every call, which is exactly the property an LLM
  loop wants.

[**`hyperlight-sandbox`**][hls] is the layer that turns Hyperlight into
something application developers can actually *use*: a multi-backend
sandboxing framework that ships prebuilt guests (notably a **Python
guest**), a **Python SDK** (`pip install hyperlight-sandbox[wasm,python-guest]`),
and a controlled host-capability bridge — `call_tool(...)` — so guest
code can request a *specific* host operation instead of being given a
syscall surface. The wheel even bundles the compiled `python-sandbox.aot`,
so the Python guest is a single `pip install` away.

Conceptually:

```
+-------------------+        +--------------------------+
| Host Python app   | -----> | Hyperlight microVM       |
| (your agent loop) |   |    |   Wasm Python guest      |
|                   |   |    |   user code runs here    |
| call_tool bridge  | <----- |   guest -> call_tool(...) |
+-------------------+        +--------------------------+
```

The host decides which `call_tool` names exist. The model never sees
sockets, files, or env vars — only the named bridges you opt into.

## 3. What is a Harness Agent?

The agent ecosystem has accumulated a lot of overlapping vocabulary —
"agent," "scaffold," "harness," "tool runtime." Hugging Face's
[*Agent Glossary*][hf-glossary] is the cleanest reference today, and it
draws the line we care about:

> *A **harness** is the runtime that hosts a model, wires it to tools,
> mediates I/O, and enforces the rules of engagement. The model is the
> brain; the harness is the nervous system and the safety belt.*

Concretely a harness owns:

- **The tool registry** the model is allowed to see this turn
- **Skills / progressive context** — what gets pushed into the prompt vs.
  loaded on demand
- **Middleware** — logging, redaction, rate limits, counters
- **Execution policy** — *where* a tool actually runs (in-process? in a
  subprocess? in a microVM?)

This is exactly the seam where Hyperlight earns its keep. If your harness
declares "the only tool the model sees is `execute_code`, and
`execute_code` always runs inside a Hyperlight sandbox," you have just
collapsed the entire LLM-RCE risk surface to one well-defined boundary.

## 4. Microsoft Agent Framework: First-Class Harness Support

[Microsoft Agent Framework][maf] is the OSS evolution of Semantic Kernel
and AutoGen, and as of late 2025 it ships **harness agents** as a
first-class concept — see the team's deep dive
[*Agent Harness in Agent Framework*][maf-harness]. The relevant
primitives for this post:

- **`create_harness_agent(...)`** — produces an agent whose tool surface,
  skills, and middleware are owned by the harness rather than baked into
  a prompt.
- **`SkillsProvider`** — file-based **Agent Skills** (`SKILL.md`
  packages with YAML frontmatter, mirroring the
  [`02-agents/skills`][maf-skills] sample) that the harness advertises
  to the model and lets it `load_skill` on demand. This is *progressive
  disclosure*: the per-turn prompt stays small, and domain rules live in
  versioned files instead of giant system prompts.
- **`HyperlightCodeActProvider`** (a `ContextProvider`) — wires the
  harness's tool execution to a `hyperlight-sandbox` runtime. The model
  sees one tool, `execute_code`, and any extra capability is reachable
  *only* from inside the guest via `call_tool(...)`.
- **`WorkflowBuilder`** — a graph orchestrator (see the
  [`03-workflows`][maf-workflows] samples) that lets you put multiple
  harness agents on a DAG with deterministic adapter/save executors
  between them. No LLM in the persistence step, no LLM at the edges of
  the system.

Put the four together and you get the **CodeAct-on-microVM** pattern:
the harness sets the rules, the workflow shapes the data, the skills
carry the domain knowledge, and Hyperlight enforces the boundary.

## 5. The Project: A Daily World Cup Podcast Pipeline

To make all of the above concrete, the
[harness-agents-sandbox-demo](../README.md) repository builds a
deliberately small but production-shaped workload: every day, generate
a five-minute Mandarin podcast script about the **FIFA World Cup 2026**,
in both **zh-CN** (mainland commentary style, in the spirit of 詹俊) and
**zh-TW** (HK Cantonese broadcast style, in the spirit of 伍晃荣).

### The graph

```
prepare -> SearchAgent -> adapt -> ContentAgent -> adapt
        -> GenScriptAgent -> save_scripts
```

Three harness agents, three adapter executors, one deterministic save:

| Node | Kind | Tools the model sees |
|---|---|---|
| `SearchAgent` | harness + CodeAct | `execute_code` (+ guest `call_tool("fetch_url", ...)`) — BBC-only, returns top 5 stories as JSON |
| `ContentAgent` | harness + CodeAct | `execute_code` (+ guest `fetch_url`) — DeepSearch enrichment over verified URLs |
| `GenScriptAgent` | harness + CodeAct | `execute_code` *only* — writes both scripts and asserts each is 1500–1900 Han characters |
| `save_scripts` | deterministic `Executor` | — | parses fenced blocks, writes PVC, uploads to Azure Blob Storage |

The role prompts and the shared sandbox guardrails live as four
file-based **Agent Skills** under [`skills/`](../skills/):
`hyperlight-sandbox`, `search-bbc-worldcup`, `content-deepsearch`,
`genscript-podcast`. Agents themselves carry only ~10 lines of stub
prompt; the harness's `SkillsProvider` advertises the skills and the
model loads them via `load_skill` when it actually needs the workflow.

### The boundary

This is the part worth reading twice:

- The **only** tool the model directly sees, across all three agents, is
  `execute_code`.
- Networking is reachable solely via a single host bridge,
  `fetch_url`, which is **invoked from inside the guest** through
  `call_tool("fetch_url", url=...)`. The host enforces an HTTP-GET-only
  allow-list (`www.bbc.com`, `bbc.com`), strips HTML, pre-extracts BBC
  sport article URLs into a `LINKS:` header so the model never has to
  invent slugs, and caps the body at 8 KB.
- All three agents share **one** Hyperlight sandbox per workflow run,
  and a **clean snapshot is restored before every `execute_code` call**.
  No state, no globals, no leftover imports leak between agents or turns.
- The `save_scripts` executor is deterministic and runs *outside* the
  sandbox — no LLM in the persistence path.

### Cloud-native shape

The same graph, the same sandbox, the same save executor — wrapped as a
non-root, read-only-rootfs CronJob on AKS:

- **Workload Identity → User-Assigned Managed Identity.** The pod has
  no secrets. `DefaultAzureCredential` flows the SA's federated OIDC
  token to a UAMI that holds *Azure AI Developer* + *Cognitive Services
  User* on the Foundry resource and *Storage Blob Data Contributor* on
  the storage account.
- **Hyperlight device plugin.** The pod requests
  `hyperlight.dev/hypervisor: "1"`; a node-level DaemonSet uses CDI to
  inject `/dev/kvm` into the unprivileged container. No `privileged:
  true`, no host-path mounts.
- **Two sinks.** Each run writes to a PVC (in-cluster cache) and uploads
  to `<container>/<YYMMDD>/` in Azure Blob Storage (durable,
  cross-cluster). The CronJob is stateless; Blob is the source of truth.
- **PyPI-based image.** A single-stage `python:3.12-slim` that
  `pip install`s `hyperlight-sandbox[wasm,python-guest]==0.4.0`. Cold
  builds finish in roughly three minutes via `az acr build` — no Rust
  toolchain, no Docker daemon required on the developer's laptop.

The full architecture diagram (local + cloud-native) and the end-to-end
provisioning runbook live in the [README](../README.md) and
[Infra/README](../Infra/README.md).

## Closing Thought

For a long time, "AI safety" in agent systems meant prompt-engineering
the model into being polite. The microVM-plus-harness pattern reframes
it as an **architecture** problem: assume the model will write something
hostile, then make sure the runtime simply cannot let it matter.

Hyperlight gives us a kernel-grade boundary that's cheap enough to cross
on every tool call. `hyperlight-sandbox` makes that boundary a
`pip install` away. Microsoft Agent Framework's harness pattern lets us
*declare* — in skills, middleware, and a single `execute_code` tool —
exactly which side of the boundary each capability lives on. And a
real workload like the daily World Cup podcast proves the pattern
holds together end-to-end, from a developer laptop all the way to an
unprivileged pod on AKS.

The model writes code. The code runs. We just stopped pretending that
was a problem we could solve with words.

---

### References

- Hyperlight — <https://github.com/hyperlight-dev/hyperlight>
- `hyperlight-sandbox` — <https://github.com/hyperlight-dev/hyperlight-sandbox>
- Why MicroVMs (Docker blog) — <https://www.docker.com/blog/why-microvms-the-architecture-behind-docker-sandboxes/>
- *Agent Harness in Agent Framework* — <https://devblogs.microsoft.com/agent-framework/agent-harness-in-agent-framework/>
- *Harness, Scaffold, and the AI Agent Terms Worth Getting Right* — <https://huggingface.co/blog/agent-glossary>
- Microsoft Agent Framework — <https://github.com/microsoft/agent-framework>

[hl]: https://github.com/hyperlight-dev/hyperlight
[hls]: https://github.com/hyperlight-dev/hyperlight-sandbox
[docker-microvm]: https://www.docker.com/blog/why-microvms-the-architecture-behind-docker-sandboxes/
[maf]: https://github.com/microsoft/agent-framework
[maf-harness]: https://devblogs.microsoft.com/agent-framework/agent-harness-in-agent-framework/
[maf-skills]: https://github.com/microsoft/agent-framework/tree/main/python/samples/02-agents/skills
[maf-workflows]: https://github.com/microsoft/agent-framework/tree/main/python/samples/03-workflows
[hf-glossary]: https://huggingface.co/blog/agent-glossary
