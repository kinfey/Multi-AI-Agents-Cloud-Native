# Azure Deployment Plan

## Status

In Progress

## Mode And Scope

Modify the existing `acasbxapp_node` OpenClaw gateway and its existing Azure
Container Apps deployment. Do not replace the current architecture or create a
second public service.

- Make `deployment-agent` perform a real Azure Container Apps deployment after
	approved test evidence, wait for readiness, and return the deployed HTTPS URL.
- Add `save-agent` to package the complete coding-agent and testing-agent project
	workspaces into a ZIP archive.
- Expose authenticated ZIP downloads on the existing OpenClaw gateway FQDN.
- Add a local macOS helper that downloads an archive, extracts it to a requested
	destination, and opens that destination with VS Code Insiders.

## Current Architecture

- OpenClaw runs in `azure-openclaw-aca-app` with external TLS ingress on port
	`18789` and token authentication.
- `/state/openclaw` is an Azure Files mount. Agent workspaces are persisted below
	`/state/openclaw/workspaces/<agent-id>`.
- The Container App uses managed identity for Microsoft Foundry and logs in with
	Azure CLI at container startup.
- `deployment-agent` currently returns deployment-plan text only.
- The existing build does not copy the repository's `AGENTS.md` definitions into
	the built OpenClaw image.
- OpenClaw supports authenticated plugin HTTP routes, but ZIP is not an allowed
	native outbound media attachment type.

## Proposed Architecture

### Deployment Agent

- Install an operator-owned `deploy-to-aca` command in the gateway image.
- Accept only validated app names and a project directory under the deployment
	agent workspace. Reject traversal and symlinks escaping that workspace.
- Build a unique image tag in the existing ACR, create or update a Container App
	in the existing managed environment, wait for the revision to become ready,
	run an HTTPS health probe, and query the final ingress FQDN from Azure.
- Print a machine-readable result containing image, revision, status, and
	`https://<fqdn>`. The deployment agent must report success only from this result.
- Use managed identity and explicit least-privilege role assignments for ACA and
	ACR deployment operations; do not place Azure credentials in prompts or files.

### Save Agent And Download

- Register `save-agent` in the static OpenClaw config and generated runtime config,
	with its own workspace and agent instructions.
- Install a `save-agent-pack` command that reads only the coding-agent and
	testing-agent workspaces, rejects symbolic links and unsafe paths, and writes
	an immutable, randomly named ZIP under `/state/openclaw/artifacts`.
- Add an OpenClaw plugin route at `/api/saveagent/artifacts/<artifact-id>.zip` with
	`auth: "gateway"`. The handler validates the identifier, resolves the file under
	the artifact root, streams `application/zip`, and sets `Content-Disposition`.
- Return the authenticated download URL from `save-agent`. The gateway token is
	supplied by the caller as a Bearer token and is never embedded in the URL.
- Add a local script accepting `<download-url> <destination>`. It downloads to a
	temporary file, validates it as a ZIP, refuses unsafe archive entries, extracts
	into the requested destination, and runs `code-insiders <destination>`.

## Security And Operations

- Keep the existing gateway token requirement on all artifact downloads.
- Do not expose Azure Files, storage keys, or a second ingress endpoint.
- Limit archives to the two approved workspace roots, apply a configurable size
	limit, and exclude credential files such as `.env`, Azure CLI state, SSH keys,
	and Git credentials while retaining project source and tests.
- Use random artifact IDs and atomic file creation. Return `404` for invalid or
	missing IDs without disclosing filesystem paths.
- Grant only the Azure roles required to build in ACR and create/update ACA apps.
	Scope ACR roles to the registry and ACA roles to the target resource group.
- Keep rollback data in the deployment result and document restoring the previous
	Container Apps revision/image.

## Implementation Steps

1. Add focused tests for agent registration, archive path validation, secret
	 exclusion, authenticated downloads, deployment result parsing, and URL output.
2. Add `save-agent` instructions, packaging command, and authenticated OpenClaw
	 artifact plugin; copy all agent definitions into the image during builds.
3. Add the controlled ACA deployment command and strengthen deployment-agent
	 instructions so test approval, readiness, smoke checks, URL output, and rollback
	 metadata are mandatory.
4. Update entrypoint/config, Docker packaging, Bicep outputs/environment/RBAC,
	 helper scripts, and Chinese/English documentation.
5. Run Python tests, shell syntax checks, plugin TypeScript tests/type checks,
	 Bicep build/lint, and container configuration smoke checks.
6. Mark this plan `Ready for Validation` and run Azure readiness validation.
7. Deploy only after validation, then verify gateway health, save-agent ZIP download,
	 local extraction, VS Code Insiders launch command, and deployment-agent URL.

## Validation And Success Criteria

- All existing tests remain green and new archive/download/deployment tests pass.
- An unauthenticated artifact request is rejected; an authenticated valid request
	downloads a ZIP containing both coding and testing project trees and no excluded
	credentials.
- The local helper rejects traversal entries, extracts a valid archive to the
	caller-provided path, and invokes the available VS Code Insiders CLI.
- A deployment-agent run creates or updates the target ACA app, reaches a healthy
	revision, and returns a reachable HTTPS URL obtained from Azure rather than a
	predicted hostname.
- The previous revision/image remains available for rollback.

## Confirmed Azure Context

- Subscription: `4498459e-01d5-4a3f-b07e-8f1f36598c16`
- Resource group: `rg-kinfey`
- Region: `swedencentral`
- Existing app prefix: `azure-openclaw-aca`

## Approval Decision

Approved. Repository changes and local validation are authorized. Azure deployment
is still gated on successful readiness validation and confirmation that the active
Azure context matches the values above before any cloud write operation.