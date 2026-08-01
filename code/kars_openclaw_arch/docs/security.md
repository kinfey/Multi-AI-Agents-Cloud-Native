# Security

## Immediate action

If an Azure Speech key has appeared in source, an issue, chat export, or local notes, treat it as compromised. Replace the placeholders below with your resource names, regenerate the active key before provisioning, and store the replacement only in AZD or Key Vault. Never commit either the old or replacement key.

```sh
az cognitiveservices account keys regenerate \
  --resource-group '<resource-group-name>' \
  --name '<speech-account-name>' \
  --key-name key1
```

Retrieve the replacement only in a trusted shell and set `AZURE_SPEECH_KEY` in the AZD environment. Clear the shell variable afterward. Repeat deployment so Key Vault and the generated credentials Secret receive the new value.

## Controls

- OIDC Workload Identity is used for Storage, Key Vault, ACR, and Foundry access where supported.
- Image and Speech keys live in Key Vault and the runtime-only Kubernetes Secret; they are absent from Git and image layers.
- KARS creates each sandbox ServiceAccount and federated identity credential.
- Sandboxes run non-root with a read-only root filesystem, no privilege escalation, strict seccomp, writable `/sandbox` and `/tmp` only, and default-deny Strict egress.
- Inference policies enable Prompt Shields, content safety thresholds, and request/daily/monthly token budgets.
- Blob containers are private. Browser playback uses short-lived read-only delegation URLs.

## Operational rules

Do not print Kubernetes Secrets, Key Vault secret values, AZD secure values, or environment dumps into logs. Rotate keys after operator exposure, suspected compromise, staff transitions, and on the organization schedule. Audit role assignments and federated credentials regularly; KARS-owned resources should be changed through KARS configuration, not patched manually.
