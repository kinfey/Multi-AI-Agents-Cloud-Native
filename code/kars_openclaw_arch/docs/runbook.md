# Runbook

## Daily checks

```sh
kubectl get karssandbox,karseval -n finance-media
kubectl logs -n kars-media-claw-agent deployment/media-claw-agent --since=24h
kubectl logs -n kars-media-claw-agent deployment/media-claw-agent -c openclaw --since=24h | grep 'media scheduler:'
az storage blob show --container-name media --name "$(date +%y%m%d)/manifest.json" --auth-mode login --account-name '<storage-account>'
```

A successful run has exactly 12 images, 12 WAV files, one `video/final.mp4`, and one manifest under the same `yymmdd` prefix. The MP4 should be 1080x1920 H.264/AAC. The manifest makes reruns idempotent.

The image entrypoint runs a persistent scheduler that invokes the agent locally at
`08:00 Asia/Shanghai`. If a container starts after 08:00, it runs immediately. The
agent checks the daily manifest before doing any production work. A successful run
creates a persistent `/sandbox/.openclaw/media-scheduler/YYYY-MM-DD.success` marker;
failed runs do not create the marker and retry every 15 minutes. Inspect the state with:

```sh
kubectl exec -n kars-media-claw-agent deployment/media-claw-agent -c openclaw -- \
	sh -c 'ls -la /sandbox/.openclaw/media-scheduler'
```

## Audio and video adjustment / 音视频二次调整

Run the refresh Job for a date that already has a manifest. It preserves stories and images, then replaces only the generated WAV and MP4 objects.

对已有 manifest 的日期运行刷新任务。任务会保留 stories 和 images，只重新生成并替换 WAV 与 MP4。

```sh
make refresh-media DATE=2026-07-27
kubectl get jobs -n kars-media-claw-agent -w
kubectl logs -f -n kars-media-claw-agent -l run-date=2026-07-27 -c openclaw
```

The launcher refuses to create a duplicate while another refresh for the same date is active. The batch Job converts `inference-router` to a Kubernetes native sidecar (`initContainers[*].restartPolicy: Always`), so Kubernetes stops it after `openclaw` exits and the Job reaches `Complete`. Finished Jobs remain available for logs for one hour.

当同一天已有刷新任务运行时，启动器会拒绝创建重复任务。批处理 Job 会把 `inference-router` 转换为 Kubernetes 原生 sidecar，因此 `openclaw` 退出后 Kubernetes 会自动停止 router，Job 正常进入 `Complete`。完成后的 Job 会保留一小时供日志检查。

Do not change the KARS-managed Deployment to use this lifecycle. Its router must remain long-running; the transformation applies only to one-off Jobs.

不要修改 KARS 管理的 Deployment sidecar 生命周期。长期运行的 Deployment 仍需保持 router 常驻，此转换仅用于一次性 Job。

## Manual recovery

1. Inspect the OpenClaw and KARS controller logs.
2. Confirm the news, Foundry, Image, Speech, Storage, and identity endpoints are reachable under Strict egress.
3. Delete only incomplete objects for the affected date. Preserve a complete manifest unless deliberately rebuilding the whole day.
4. Invoke the `media-finance-video` skill in the Media-Claw OpenClaw session.
5. Verify the manifest and final video before exposing them through the catalog.

## Application health

```sh
curl -fsS "$(azd env get-value MEDIA_APP_URI)/health"
curl -fsS "$(azd env get-value REDTEAM_APP_URI)/health"
```

For a failed Container App revision, inspect `az containerapp revision list` and Log Analytics before restarting. For a failed sandbox, compare the `KarsSandbox` status, generated deployment events, and controller logs; do not manually replace KARS-owned ServiceAccounts or federated credentials.

## Rollback

Container Apps keep revisions, so redirect traffic to the previous healthy revision. For Media-Claw, rebuild the prior image tag, update `MEDIA_CLAW_OPENCLAW_IMAGE`, and reapply the overlay. Keep the pinned official OpenClaw base version synchronized with the KARS controller release.
