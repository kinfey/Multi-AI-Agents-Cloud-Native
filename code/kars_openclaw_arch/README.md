# KARS Finance Short Video

A multi-agent finance short-video system built on KARS and OpenClaw. Media-Claw-Agent collects ten China/US market stories every day at 08:00 Beijing time, generates a consistent 9:16 image set and narration in both Chinese and English, and composes one video per language with FFmpeg. Two Container Apps serve the video SPA and the red-team report SPA.

[中文文档](README.zh.md)

## Layout

- `agents/media-claw-agent`: OpenClaw workspace, finance content pipeline and media composition
- `agents/media-app-agent`: video catalogue, playback and reaction API
- `agents/media-testing-agent`: KARS red-team report API
- `apps`: two plain HTML/CSS/JavaScript SPAs
- `kars`: `KarsSandbox`, `InferencePolicy` and five `KarsEval` kinds
- `infra`: AZD/Bicep Azure infrastructure
- `scripts`: KARS install, AKS configuration, smoke and red-team operations

## Local development

Requires Python 3.12, Docker, FFmpeg, Azure CLI, AZD, kubectl, Helm and Kustomize.

```sh
cp .env.example .env
python3.12 -m venv .venv
. .venv/bin/activate
make install
make validate-local
```

`make validate-local` runs pytest, ruff, `compileall`, a full Kustomize + envsubst render of the KARS overlay, `az bicep build`, and a syntax check of every shell script. Treat it as the gate before any deployment.

Start Media-Claw and both apps locally. Set `AZURE_OPENAI_ENDPOINT` and
`AZURE_OPENAI_API_KEY` in `.env` so the local KARS inference router can run the
scheduled agent turn:

```sh
make compose-up
make smoke
```

Docker Compose gives exported shell variables precedence over `.env`. After
rotating a local API key, unset any stale `AZURE_OPENAI_API_KEY` export before
recreating `media-claw-agent`.

- Video app: http://localhost:8000
- Red-team report: http://localhost:8001
- Media-Claw Gateway: http://localhost:18789

The local default Storage URL is intentionally invalid, so health checks run but real catalogue data needs an Azure identity and Blob configuration.

## Azure workflow

Create an AZD environment for the target Azure tenant, subscription, resource group and region. Keep deployment-specific identifiers outside version control, and rotate any credential that may have been exposed before configuring the environment:

```sh
az login --tenant '<tenant-id>'
az account set --subscription '<subscription-id-or-name>'
azd env new dev
azd env set AZURE_LOCATION '<azure-region>'
azd env set AZURE_RESOURCE_GROUP '<resource-group-name>'
azd env set AZURE_IMAGE_API_KEY '<rotated-image-key>'
azd env set AZURE_SPEECH_KEY '<rotated-speech-key>'
```

Validation is mandatory before deploying:

```sh
make validate-local
azd provision --preview
```

Only run `azd up` once both pass. Its post-provision hook installs KARS, builds the real OpenClaw-derived image, applies the KARS resources and injects runtime credentials. To ship a change to one service only, use `azd deploy media-app` or `azd deploy redteam-app` — both images bundle their SPA from `apps/`, so a frontend edit still requires a redeploy.

See [deployment](docs/deployment.md), [runbook](docs/runbook.md), [security](docs/security.md) and [red team](docs/red-team.md) for details.

## Cloud Red-team testing

The Red-team suite runs five KARS corpora against the deployed target sandbox: jailbreak, prompt injection, banned tools, prohibited egress and cross-session memory isolation. Run it only after the target `KarsSandbox` is `Running` and the generated evaluation CronJobs exist:

```sh
export AZURE_RESOURCE_GROUP="$(azd env get-value AZURE_RESOURCE_GROUP)"
export AKS_CLUSTER_NAME="$(azd env get-value AKS_CLUSTER_NAME)"
export AZURE_CONTAINER_REGISTRY_ENDPOINT="$(azd env get-value AZURE_CONTAINER_REGISTRY_ENDPOINT)"
export KARS_NAMESPACE='<kars-control-namespace>'
export KARS_TARGET_SANDBOX='<target-sandbox-name>'

az aks get-credentials \
	--resource-group "$AZURE_RESOURCE_GROUP" \
	--name "$AKS_CLUSTER_NAME" \
	--overwrite-existing

kubectl get karssandbox,karseval -n "$KARS_NAMESPACE"
make redteam
```

`make redteam` prints `[1/5]` through `[5/5]`, creates five restricted one-shot Jobs, reports status changes every five seconds and exits nonzero when any suite fails or times out. A passing infrastructure check or a blocked request is not automatically a passing security result: review every suite's `total`, `passed`, `failed` and failure reason. Keep the release gate closed when a dangerous case is allowed or when a transport, RBAC or missing-resource error prevents the intended control from being evaluated.

An Entra-enabled AKS cluster requires `kubelogin` for local `kubectl`. If it is unavailable, execute the same repository script through AKS Run Command without enabling local cluster accounts:

```sh
export AZURE_RESOURCE_GROUP="$(azd env get-value AZURE_RESOURCE_GROUP)"
export AKS_CLUSTER_NAME="$(azd env get-value AKS_CLUSTER_NAME)"
export AZURE_CONTAINER_REGISTRY_ENDPOINT="$(azd env get-value AZURE_CONTAINER_REGISTRY_ENDPOINT)"
export KARS_NAMESPACE='<kars-control-namespace>'
export KARS_TARGET_SANDBOX='<target-sandbox-name>'

az aks command invoke \
	--resource-group "$AZURE_RESOURCE_GROUP" \
	--name "$AKS_CLUSTER_NAME" \
	--file scripts/run-redteam.sh \
	--command "AZURE_CONTAINER_REGISTRY_ENDPOINT=$AZURE_CONTAINER_REGISTRY_ENDPOINT KARS_NAMESPACE=$KARS_NAMESPACE KARS_TARGET_SANDBOX=$KARS_TARGET_SANDBOX KARS_EVAL_TIMEOUT_SECONDS=300 sh run-redteam.sh"
```

AKS Run Command success only confirms that the remote operation ran; inspect its logs for the per-suite counts and final `Red-team run completed` message. The runner does not automatically publish a dashboard report. Normalize the results to the `EvalReport` schema, upload them as `reports/<run-id>.json` in the configured private report container, then verify `$(azd env get-value REDTEAM_APP_URI)/api/reports`. See [Red-team testing](docs/red-team.md) for release criteria and KARS `v0.1.25` compatibility notes.

## Data layout

Daily artefacts live under `yymmdd/`, with images, audio and video separated per language:

```text
yymmdd/imgs/{cn,en}/{cover,01..10,end}.png
yymmdd/audio/{cn,en}/{cover,01..10,end}.wav
yymmdd/video/final_{cn,en}.mp4
yymmdd/manifest.json
yymmdd/reactions/{like,star}/{voter}.json
```

`manifest.json` is the single index of the catalogue, citations, scripts, durations and media URLs. The container scheduler invokes the agent at or after 08:00 Asia/Shanghai, and the agent skips generation when the manifest for the day already exists.

`GET /api/videos` returns every manifest whose `status` is `ready`, each carrying a `languages` map with a short-lived read SAS for that language's video and cover. `playback_url` and `cover_url` remain on the response pointing at the Chinese assets, so the catalogue stays readable without inspecting the map. Episodes published before the bilingual pipeline store a flat `assets` block and are served as Chinese-only rather than failing on a missing key.

## Reactions

Likes and star ratings are persisted per episode. Every address gets one like and one rating per episode.

```text
GET  /api/videos/{yymmdd}/reactions
POST /api/videos/{yymmdd}/reactions   {"kind": "like" | "star", "value": 1..5}
```

Both return the live totals plus what this caller already contributed:

```json
{"likes": 2, "stars": {"votes": 2, "total": 9, "average": 4.5}, "mine": {"like": true, "star": 4}}
```

`POST` adds an `accepted` field and answers `201` for a recorded vote or `409` when this address already voted. A rejected vote still returns the current totals, so the UI renders them instead of discarding a useful response. `value` is forced to `1` for `like` and must be `1..5` for `star` (`422` otherwise); the date must be six digits (`400` otherwise). The client never increments locally — every render is driven by the summary the server just returned.

Each vote is a single blob at `yymmdd/reactions/{kind}/{voter}.json` uploaded with `overwrite=False`, which the SDK sends as `If-None-Match: *`. One vote per address is therefore an atomic create-if-absent in storage, so two concurrent taps cannot both win the way a read-then-write check would. Star scores are mirrored into blob metadata so a summary totals them from one list call instead of downloading every vote.

The voter id is a salted SHA-256 of the caller address truncated to 32 hex characters, so no raw IP is ever written to storage; override the salt with `REACTION_VOTER_SALT`. Container Apps terminates TLS at its ingress, so the caller only survives in `X-Forwarded-For` — a client-supplied and therefore spoofable header. This is a civility guard against accidental double taps, not an authentication boundary.
