# Deploying the FIFA 2026 podcast pipeline on Kubernetes

This folder packages everything required to run the pipeline as a daily
batch workload on a Kubernetes cluster, using
[**hyperlight-on-kubernetes**](https://github.com/hyperlight-dev/hyperlight-on-kubernetes)
to expose the hypervisor to the pod and
[**hyperlight-sandbox**](https://github.com/hyperlight-dev/hyperlight-sandbox)
as the in-pod execution backend for LLM-generated code.

```
Infra/
├── Dockerfile               # Single-stage image: Python 3.12 + hyperlight-sandbox PyPI bundle + app
├── .dockerignore
├── k8s/
│   ├── namespace.yaml       # `podcast-pipeline` ns, Pod Security: restricted
│   ├── serviceaccount.yaml  # Workload Identity SA (DefaultAzureCredential → Foundry + Blob)
│   ├── configmap.yaml       # Foundry endpoint / model / module path / Blob target
│   ├── pvc.yaml             # outputs volume
│   ├── cronjob.yaml         # daily run @ 02:00 UTC, requests hyperlight.dev/hypervisor
│   ├── job-once.yaml        # ad-hoc smoke run
│   └── kustomization.yaml
└── scripts/
    ├── build-and-push.sh    # `docker build` → ACR
    ├── build-and-push.ps1   # PowerShell variant
    └── deploy.sh            # `kubectl apply -k`
```

## Architecture in cluster

```
┌─────────────────────────────────────────────────────────────────────┐
│ Kubernetes node (KVM or MSHV / Hyper-V capable)                    │
│                                                                     │
│  hyperlight-on-kubernetes device plugin (DaemonSet, kube-system)   │
│   - advertises  hyperlight.dev/hypervisor=kvm  (or mshv)           │
│   - injects /dev/kvm or /dev/mshv via CDI when a pod requests it   │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │ Pod  podcast-pipeline/podcast-daily-*                         │ │
│  │  - SA: podcast-pipeline (Azure Workload Identity)             │ │
│  │  - runAsNonRoot, readOnlyRootFilesystem, drop ALL caps        │ │
│  │  - resources.limits.hyperlight.dev/hypervisor: "1"            │ │
│  │                                                                │ │
│  │  ┌─────────────────────────────────────────────────────────┐  │ │
│  │  │ Container fifa-2026-podcast:<tag>  (entry: main.py)     │  │ │
│  │  │  ├─ workflow_pipeline.py  (Search→Content→GenScript)    │  │ │
│  │  │  └─ HyperlightRuntime  ──►  python-sandbox.aot guest    │  │ │
│  │  │       (uses /dev/kvm projected by the device plugin)    │  │ │
│  │  └─────────────────────────────────────────────────────────┘  │ │
│  │                                                                │ │
│  │  /var/podcast/outputs  ◄── PVC (RWO)                          │ │
│  │   └─ also uploaded to Azure Blob (podcast-scripts/<YYMMDD>/)  │ │
│  └───────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                       │                  │                  │
                       ▼                  ▼                  ▼
            Azure AI Foundry      BBC.com (allow-     Azure Blob Storage
            (Workload Identity)   listed in           (Workload Identity,
                                  fetch_url)          podcast-scripts/)
```

The pod itself is **unprivileged**; the device plugin is the only privileged
component on the node. This matches the upstream pattern from
`hyperlight-on-kubernetes`.

## Prerequisites on the cluster (one-time)

1. **Hyperlight device plugin** must be installed first. Follow the
   upstream guide: <https://github.com/hyperlight-dev/hyperlight-on-kubernetes>.

   Quick sanity check:
   ```bash
   kubectl get nodes -L hyperlight.dev/hypervisor
   kubectl apply -f https://raw.githubusercontent.com/hyperlight-dev/hyperlight-on-kubernetes/main/deploy/manifests/examples/test-pod-kvm.yaml
   kubectl logs hyperlight-test-kvm   # expects "✓ /dev/kvm exists"
   ```

2. **Workload Identity** must be enabled on the cluster (AKS:
   `--enable-oidc-issuer --enable-workload-identity`).

3. **A user-assigned managed identity** that has access to the Foundry
   project **and** the Blob Storage account where scripts land. See the
   comment block at the top of
   [k8s/serviceaccount.yaml](k8s/serviceaccount.yaml) — it lists the exact
   `az identity` / `az role assignment` / `az identity federated-credential`
   commands.

4. **A storage account + container for the generated scripts** (RWO PVC
   stays in the cluster; the Blob copy is the durable cross-cluster
   destination). One-time setup:

   ```powershell
   az storage account create -g $RG -n <stgName> -l <region> \
       --sku Standard_LRS --kind StorageV2 \
       --allow-blob-public-access false --min-tls-version TLS1_2
   az storage container create --account-name <stgName> -n podcast-scripts --auth-mode login

   # Grant the UAMI Storage Blob Data Contributor on the storage account.
   $STG_ID = az storage account show -g $RG -n <stgName> --query id -o tsv
   az role assignment create --assignee-object-id $PRINCIPAL_ID \
       --assignee-principal-type ServicePrincipal \
       --role 'Storage Blob Data Contributor' --scope $STG_ID
   ```

## Configuration knobs

Edit [k8s/configmap.yaml](k8s/configmap.yaml):

| Key | Description |
|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | Your Foundry project endpoint URL. |
| `FOUNDRY_MODEL` | Deployment name, e.g. `gpt-5.5`. |
| `HYPERLIGHT_PYTHON_MODULE_PATH` | `/opt/hyperlight/python-sandbox.aot` — already correct for this image. |
| `PODCAST_OUTPUT_DIR` | `/var/podcast/outputs` — backed by the PVC. |
| `AZURE_CREDENTIAL_KIND` | `default` (Workload Identity) — leave as-is. |
| `AZURE_STORAGE_ACCOUNT` | Storage account that receives the generated scripts. Leave empty to skip the upload step. |
| `AZURE_STORAGE_CONTAINER` | Container under that account, e.g. `podcast-scripts`. |

Edit [k8s/serviceaccount.yaml](k8s/serviceaccount.yaml) and replace
`REPLACE-WITH-USER-ASSIGNED-IDENTITY-CLIENT-ID` with your UAMI client ID.

Edit [k8s/cronjob.yaml](k8s/cronjob.yaml) /
[k8s/job-once.yaml](k8s/job-once.yaml) /
[k8s/kustomization.yaml](k8s/kustomization.yaml) and replace
`REPLACE-ME.azurecr.io/fifa-2026-podcast` with your registry path
(or use `kustomize edit set image` — the `deploy.sh` script does that for you).

If your cluster uses MSHV / Hyper-V instead of KVM, change the
`nodeSelector` from `hyperlight.dev/hypervisor: kvm` to
`hyperlight.dev/hypervisor: mshv`.

## Build & push the image

The image installs `hyperlight-sandbox[wasm,python-guest]==0.4.0` from
PyPI — the published wheel already ships the compiled
`python-sandbox.aot`, which the Dockerfile extracts to
`/opt/hyperlight/python-sandbox.aot`. There is no Rust toolchain or
`just build` step in the image build, so cold builds finish in roughly
3 minutes.

Both wrapper scripts call `az acr build` (server-side build inside ACR),
so a local Docker daemon is **not** required.

Linux / macOS / WSL2:
```bash
export ACR_NAME=myregistry
export RG=my-rg
export IMAGE_TAG=v0.1.0
./Infra/scripts/build-and-push.sh
```

Windows PowerShell (also sets the UTF-8 console encoding that works
around the `az acr build` cp1252 log-streaming bug on Windows):
```powershell
$env:ACR_NAME  = "myregistry"
$env:RG        = "my-rg"
$env:IMAGE_TAG = "v0.1.0"
.\Infra\scripts\build-and-push.ps1
```

## Deploy

```bash
export ACR_NAME=myregistry
export IMAGE_TAG=v0.1.0
./Infra/scripts/deploy.sh
```

This applies the Kustomize bundle (namespace + RBAC + ConfigMap + PVC +
CronJob). The CronJob fires daily at **02:00 UTC**.

### Trigger a one-shot run

Either apply the standalone Job:
```bash
kubectl apply -f Infra/k8s/job-once.yaml
kubectl logs -n podcast-pipeline -l job-name=podcast-once -f
```

Or kick the CronJob immediately:
```bash
kubectl -n podcast-pipeline create job \
    --from=cronjob/podcast-daily podcast-manual-$(date +%s)
```

### Inspect outputs

The pipeline writes scripts under `/var/podcast/outputs/<YYMMDD>/` on the
PVC **and** uploads the same two files to
`https://<stg>.blob.core.windows.net/podcast-scripts/<YYMMDD>/` when
`AZURE_STORAGE_ACCOUNT` is set in the ConfigMap.

Check the blob copy (no in-cluster shell needed):

```powershell
az storage blob list --account-name <stgName> -c podcast-scripts --auth-mode login -o table
az storage blob download --account-name <stgName> -c podcast-scripts \
    -n 260603/260603.simple.zh.txt -f .\260603.simple.zh.txt --auth-mode login
```

Or pull from the PVC with a debug pod:

```bash
kubectl -n podcast-pipeline run dump --rm -it --restart=Never \
    --image=mcr.microsoft.com/cbl-mariner/busybox:2.0 \
    --overrides='{"spec":{"containers":[{"name":"dump","image":"mcr.microsoft.com/cbl-mariner/busybox:2.0","command":["sh"],"stdin":true,"tty":true,"volumeMounts":[{"name":"o","mountPath":"/o"}]}],"volumes":[{"name":"o","persistentVolumeClaim":{"claimName":"podcast-outputs"}}]}}' \
    -- sh
# inside: ls /o ; cat /o/260603/260603.simple.zh.txt
```

## Cleanup

```bash
kubectl delete -k Infra/k8s
```
The PVC (and its data) is deleted along with the namespace. **Blobs in
the storage account are not touched** — delete them explicitly if you
need a clean slate:

```powershell
az storage blob delete-batch --account-name <stgName> -s podcast-scripts --auth-mode login
```

## References

- [hyperlight-dev/hyperlight-on-kubernetes](https://github.com/hyperlight-dev/hyperlight-on-kubernetes) — device plugin + node setup
- [hyperlight-dev/hyperlight-sandbox](https://github.com/hyperlight-dev/hyperlight-sandbox) — sandbox runtime built into the image
- [Azure Workload Identity on AKS](https://learn.microsoft.com/azure/aks/workload-identity-overview)
- [Pod Security Standards — restricted](https://kubernetes.io/docs/concepts/security/pod-security-standards/#restricted)
