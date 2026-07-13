# Azure Deployment Plan: OpenClaw ACA Five-Agent App

Status: Approved

## 1. User Request

Deploy the OpenClaw workflow to Azure Container Apps and return its public HTTPS address. The workflow must run Requirement, Coding, Testing, Deployment, and Save agents. Deployment Agent publishes generated applications to ACA, while Save Agent packages all Coding and Testing output into an authenticated ZIP that can be safely extracted and opened with VS Code Insiders.

## 2. Target Architecture

- Subscription: `CloudNative` (`4498459e-01d5-4a3f-b07e-8f1f36598c16`)
- Resource group: `rg-kinfey`
- Region: `swedencentral`
- Container App: `azure-openclaw-aca-app`
- Registry: `azureopenclawaca20260706.azurecr.io`
- Image: `azureopenclawaca20260706.azurecr.io/openclaw:latest`
- Foundry deployment: `gpt-5.5` using managed identity
- Persistent Azure Files storage for OpenClaw state and agent workspaces
- User-assigned identity with `AcrPull`; deployment operations use scoped managed-identity RBAC
- Bearer-authenticated gateway and artifact download endpoints

## 3. Agent Workflow

1. `requirements-agent` defines and reviews the requested behavior.
2. `coding-agent` implements the reviewed requirements.
3. `testing-agent` validates the generated code and reports evidence.
4. `deployment-agent` invokes the gateway-scoped ACA deployment tool and verifies health.
5. `save-agent` packages Coding and Testing workspaces and returns an authenticated artifact URL.

Each transition is blocked until the preceding review is approved. Privileged Azure deployment and filesystem packaging execute in the trusted gateway, not in ACA Sandbox.

## 4. Security Controls

- Secrets remain in Container App secrets or deployment parameters and are never committed.
- Artifact routes require the gateway bearer token.
- Package input identifiers are validated; symlinks and secret paths are rejected.
- ZIP count and size limits are enforced, and local extraction rejects path traversal.
- ACR access uses a user-assigned managed identity and least-privilege RBAC.
- Storage anonymous blob access is disabled.
- Known base-image OS vulnerabilities without an available upstream fix are accepted as residual risk; fixable application dependency findings must be remediated before rollout.

## 5. Deployment Recipe

Recipe type: Azure CLI with Bicep and ACR remote build.

1. Stage the pinned OpenClaw source and custom files.
2. Build `openclaw:latest` in ACR from `Dockerfile.azure`.
3. Validate Bicep and parameter files.
4. Run resource-group `what-if` and confirm there are no unintended deletes.
5. Verify ACR/Container App managed-identity connectivity and role assignments.
6. Apply the Bicep deployment.
7. Verify the ready revision, health endpoint, agent configuration, tool scopes, and authenticated artifact flow.

## 6. Validation Checklist

- [ ] Save Agent focused tests pass.
- [ ] Application workflow tests pass in Conda environment `agentdev`.
- [ ] Shell scripts pass syntax validation.
- [ ] ACR remote image build completes successfully.
- [ ] Trivy scan contains no unaccepted fixable critical/high findings.
- [ ] Bicep and bicepparam compile successfully.
- [ ] Azure `what-if` completes with no unintended deletes.
- [ ] Static and live RBAC checks confirm the Container App identity can pull from ACR.

## 7. Validation Proof

Pending Azure validation. Commands, timestamps, image digest, scan summary, and `what-if` result will be recorded here before status is changed to `Validated`.

## 8. Post-Deployment Verification

- Report the fully qualified `https://` gateway URL only after the new revision is ready.
- Confirm all five agents are loaded and privileged tools are agent-scoped.
- Confirm unauthenticated ZIP access is rejected.
- Download a real artifact with authentication, safely extract it locally, and launch the extracted folder with VS Code Insiders.
