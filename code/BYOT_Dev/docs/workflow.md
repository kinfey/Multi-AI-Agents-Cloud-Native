# Example end-to-end workflow

Below is a happy-path conversation showing how Copilot Chat orchestrates the four MCP agents to take an idea from requirements to deployment artifacts. All four servers are configured in [`../.vscode/mcp.json`](../.vscode/mcp.json) and reachable on their public Azure LoadBalancer IPs (printed by `infra/06-show-mcp-endpoints.sh`).

---

**User (in Copilot Chat, agent mode):**
> Use the byot tower to turn this idea — *a URL shortener with click analytics* — into requirements, then code, then a test plan, then a Kubernetes deploy plan.

**Copilot picks `byot-requirements` → `gather_requirements`:**
```json
{ "idea": "URL shortener with click analytics" }
```
The `requirements-agent` builds an Agent Framework agent against AI Runway's Llama-3.2-1B endpoint, returns a numbered list of functional + non-functional requirements (REQ-001 … REQ-NNN).

**Copilot picks `byot-code` → `implement_from_requirements`:**
```json
{ "requirements": "REQ-001 … REQ-009 …", "language": "python", "framework": "fastapi" }
```
The `code-agent` returns one fenced code block per file (e.g. `app/main.py`, `app/store.py`, `tests/test_redirect.py`) plus a short "How to run" section.

**Copilot picks `byot-test` → `generate_test_plan`:**
```json
{ "code": "<the generated source tree>", "requirements": "<reqs doc>" }
```
The `test-agent` returns a layered test plan: unit, integration, contract, e2e — plus example test case IDs.

**Copilot picks `byot-deploy` → `generate_k8s_manifest`:**
```json
{ "code": "<the generated source tree>", "target": "AKS" }
```
The `deploy-agent` returns ready-to-apply Deployment + Service + Ingress YAML for each component.

---

## Calling an individual MCP tool by hand

If you want to validate one MCP endpoint without Copilot, you can use any MCP-aware HTTP client. For a quick smoke test from `curl` (showing the JSON-RPC initialize handshake):

```bash
curl -s -X POST \
  http://<byot-requirements-LB-IP>/mcp \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
```

A real conversation will go `initialize` → `tools/list` → `tools/call` — that's exactly what Copilot's MCP client does internally, so once `tools/list` returns the tool set you know the agent is healthy.

## Verifying microVM isolation

After the agents are running:

```bash
# Kernel inside the Kata microVM
kubectl -n agents exec deploy/byot-requirements -- uname -r

# Compare with one of the node kernels
NODE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
kubectl debug node/$NODE -it --image=busybox -- chroot /host uname -r
```

The two kernel versions should differ — proof that the agent code is running on the microVM guest kernel rather than the host node kernel.
