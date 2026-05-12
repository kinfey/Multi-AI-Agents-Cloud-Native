# GitHub Copilot SDK Agent on AKS with Kata microVM Isolation

> Run an Agent service based on **Microsoft Agent Framework + GitHub Copilot SDK**
> on AKS with **Kata Containers microVM isolation (`kata-vm-isolation`)**
> to reduce the blast radius of container escape against shared nodes and co-located workloads.

## Why run a Copilot SDK Agent with Kata microVM?

At runtime, a GitHub Copilot SDK Agent can:

- Start a `copilot` CLI child process (Node.js)
- Execute **shell commands, file read/write, URL fetch, and MCP servers** after `on_permission_request`
- Load third-party MCP servers (`npx @modelcontextprotocol/server-*`), which may pull arbitrary npm packages

In other words, the Agent container can naturally run **untrusted, model-generated code**.
This is a typical high-risk workload and a standard target for the
[Hardening AI/agent CLI on AKS with Kata microVM Isolation] scenario.

In a traditional `runc` container model, pods share the host Linux kernel.
A kernel 0-day or a misconfigured capability/mount can lead to container escape,
impacting the entire node and other pods on the same node.

With **Kata microVM isolation**, each pod runs inside an isolated Microsoft Hyper-V (mshv)
lightweight VM with its own guest kernel. Even if an escape happens, it is contained in the
ephemeral microVM rather than reaching the node host kernel.
See the Kata Containers project: <https://github.com/kata-containers>.

## Solution Architecture

```
                ┌──────────────────────────────────────────────────────────┐
                │                AKS Cluster (Azure Linux)                │
                │                                                          │
                │  ┌────────────────────────────────────────────────────┐  │
   HTTPS        │  │ Node Pool: workload-runtime = KataVmIsolation     │  │
  ───────────►  │  │                                                    │  │
                │  │  ┌─────────────────────────────────────────────┐   │  │
                │  │  │ Pod (runtimeClassName:                     │   │  │
                │  │  │      kata-vm-isolation)                    │   │  │
                │  │  │ ┌─────────────────────────────────────────┐ │   │  │
                │  │  │ │ Microsoft Hyper-V microVM (Cloud HV)   │ │   │  │
                │  │  │ │ ┌─────────────────────────────────────┐ │ │   │  │
                │  │  │ │ │ FastAPI (uvicorn)                   │ │ │   │  │
                │  │  │ │ │ └─ GitHubCopilotAgent               │ │ │   │  │
                │  │  │ │ │    └─ Copilot CLI (Node.js)         │ │ │   │  │
                │  │  │ │ │       └─ MCP servers / tools        │ │ │   │  │
                │  │  │ │ └─────────────────────────────────────┘ │ │   │  │
                │  │  │ │ Isolated guest kernel + seccomp + cgroup│ │   │  │
                │  │  │ └─────────────────────────────────────────┘ │   │  │
                │  │  │ Egress restricted by NetworkPolicy          │   │  │
                │  │  └─────────────────────────────────────────────┘   │  │
                │  └────────────────────────────────────────────────────┘  │
                └──────────────────────────────────────────────────────────┘
```

Key defense layers:

| Layer | Protection |
| --- | --- |
| Pod sandbox | `runtimeClassName: kata-vm-isolation` -> microVM + isolated guest kernel |
| Container | `runAsNonRoot`, `readOnlyRootFilesystem`, drop ALL caps, `seccompProfile: RuntimeDefault` |
| Network | `NetworkPolicy`: only allow required egress for Copilot/GitHub/MCP |
| Secrets | `GH_COPILOT_TOKEN` via Kubernetes Secret (can be replaced with CSI + Key Vault) |
| Agent tools | `on_permission_request` defaults to deny; only allowlisted operations are approved |

## File Structure

```
.
├── app/                       # GitHub Copilot SDK Agent service (Python)
│   ├── main.py                # FastAPI HTTP API
│   ├── agent.py               # GitHubCopilotAgent wrapper
│   ├── tools.py               # Example function tools
│   └── requirements.txt
├── Dockerfile                 # Python 3.12 + Node 20 + Copilot CLI
├── k8s/
│   ├── namespace.yaml
│   ├── runtimeclass.yaml      # Reference RuntimeClass manifest (AKS auto-creates kata-vm-isolation)
│   ├── secret.example.yaml    # Secret example (replace with real token)
│   ├── deployment.yaml        # Enforce kata-vm-isolation
│   ├── service.yaml
│   └── networkpolicy.yaml     # Restrict egress; allow ingress from copilot-agent / kube-system
└── infra/
    ├── 01-create-aks.sh       # Create AKS with Kata support
    ├── 02-build-push.sh       # Build and push image to ACR (auto-discovers ACR in RG)
    └── 03-deploy.sh           # Deploy manifests
```

## Deployment Steps (Overview)

1. `bash infra/01-create-aks.sh` - Create an AKS cluster with `KataVmIsolation` enabled.
2. `kubectl get runtimeclass kata-vm-isolation` - Verify the RuntimeClass is automatically created by AKS.
3. `bash infra/02-build-push.sh` - Build the image and push it to ACR. The script auto-discovers the ACR in the resource group if `ACR` is not set.
4. Put your GitHub Copilot token in `k8s/secret.example.yaml`, then rename it to `secret.yaml` (do not commit).
5. `bash infra/03-deploy.sh` - Apply all manifests.
6. Call the agent via the API server proxy (see [Calling the Agent](#calling-the-agent) below).

## Calling the Agent

> **Note**: `kubectl port-forward` does **not** work with Kata pods. The application
> listens inside the microVM, while `port-forward` enters the empty sandbox netns on
> the host and gets `connection refused`. Use one of the patterns below instead.

### Option A: API server proxy (recommended for local dev)

```bash
kubectl proxy --port=8001 &

# Health checks
curl -s http://localhost:8001/api/v1/namespaces/copilot-agent/services/copilot-agent:80/proxy/healthz
curl -s http://localhost:8001/api/v1/namespaces/copilot-agent/services/copilot-agent:80/proxy/readyz

# Chat (synchronous JSON)
curl -s -X POST \
  http://localhost:8001/api/v1/namespaces/copilot-agent/services/copilot-agent:80/proxy/chat \
  -H 'content-type: application/json' \
  -d '{"message":"Briefly introduce Kata Containers."}'
# => {"reply":"Kata Containers is an open-source container runtime ..."}

# Chat (token streaming, plain text)
curl -N -X POST \
  http://localhost:8001/api/v1/namespaces/copilot-agent/services/copilot-agent:80/proxy/chat/stream \
  -H 'content-type: application/json' \
  -d '{"message":"List 3 Linux kernel hardening tips","stream":true}'
```

### Option B: in-cluster client (for CI / smoke tests)

```bash
kubectl -n copilot-agent run curl --rm -it --restart=Never \
  --image=curlimages/curl -- \
  -s -X POST http://copilot-agent/chat \
  -H 'content-type: application/json' \
  -d '{"message":"ping"}'
```

### Option C: expose via Ingress / LoadBalancer

For production, change `k8s/service.yaml` to `type: LoadBalancer` (or front it with
Application Gateway / NGINX Ingress + TLS) and call the public endpoint directly.

## Verify microVM Isolation

```bash
kubectl -n copilot-agent exec deploy/copilot-agent -- uname -r
# Expected: kernel version differs from node kernel (from kubectl debug node), proving it runs in a guest kernel.
```

## Notes

- In AKS, Kata support is called *Pod Sandboxing*. The RuntimeClass is `kata-vm-isolation`
  (the underlying containerd handler is `kata`), and the hypervisor is Microsoft Hyper-V (mshv), not QEMU.
  It requires `--os-sku AzureLinux` and a VM series that supports nested virtualization
  (for example, `Standard_D4s_v3`; `Standard_D4s_v5` also works where subscription quota permits).
- Upstream Kata Containers also provides other runtime classes such as `kata-qemu` and `kata-clh`
  (this project also includes a generic `kata-qemu` sample for self-managed Kubernetes outside AKS).
- The `copilot` CLI requires a valid GitHub Copilot subscription and token; verify before deployment.
- The Copilot CLI / SDK reads its token from `GH_TOKEN` (or `GITHUB_TOKEN`), **not** from
  arbitrary names like `GH_COPILOT_TOKEN`. The deployment sources `GH_TOKEN` and `GITHUB_TOKEN`
  from the same Kubernetes Secret value so the SDK can authenticate.
- `GITHUB_COPILOT_MODEL` must be a model available on your Copilot plan (for example
  `gpt-4.1`, `claude-3.5-sonnet`, `o4-mini`). Leave it empty (`""`) to fall back to the SDK
  default model. Unsupported model names produce `Model "..." is not available` at session create.
- The `copilot` CLI extracts its bundled package into `/home/agent/.cache` at startup. Because the
  container runs with `readOnlyRootFilesystem: true`, the deployment mounts an `emptyDir` volume at
  `/home/agent/.cache` (alongside `/home/agent/.copilot` and `/tmp`) so the CLI can start.
- The `NetworkPolicy` allows ingress from the `copilot-agent` namespace **and** from
  `kube-system` (so `kubectl proxy` / API server proxy and in-cluster Ingress controllers can
  reach the Service). Tighten this for production if you front the agent with a dedicated
  Ingress namespace.
- **Kata caveat:** `kubectl port-forward` does not work against Kata pods because the listener
  lives inside the microVM, not in the sandbox netns. Use the patterns in
  [Calling the Agent](#calling-the-agent).
- `on_permission_request` uses a deny-by-default allowlist strategy; do not switch to approve-all in production.
