# KARS Finance Short Video

基于 KARS 和 OpenClaw 的多 Agent 财经短视频方案。Media-Claw-Agent 每天北京时间 08:00 汇总 10 条中美股市动态，生成统一风格的 9:16 图片与中英文配音，并通过 FFmpeg 按语言各合成一条视频；两个 Container Apps 分别提供视频播客 SPA 和红队报告 SPA。

[English](README.md)

## 项目结构

- `agents/media-claw-agent`: OpenClaw 工作区、财经内容流水线和媒体合成
- `agents/media-app-agent`: 视频目录、播放与互动 API
- `agents/media-testing-agent`: KARS 红队报告 API
- `apps`: 两个原生 HTML/CSS/JavaScript SPA
- `kars`: `KarsSandbox`、`InferencePolicy` 和五类 `KarsEval`
- `infra`: AZD/Bicep Azure 基础设施
- `scripts`: KARS 安装、AKS 配置、冒烟和红队操作

## 本地开发

需要 Python 3.12、Docker、FFmpeg、Azure CLI、AZD、kubectl、Helm 和 Kustomize。

```sh
cp .env.example .env
python3.12 -m venv .venv
. .venv/bin/activate
make install
make validate-local
```

`make validate-local` 会依次执行 pytest、ruff、`compileall`、KARS overlay 的完整 Kustomize + envsubst 渲染、`az bicep build`，以及所有 shell 脚本的语法检查。把它当作部署前的准入门槛。

启动两个本地应用：

```sh
make compose-up
make smoke
```

- 视频应用: http://localhost:8000
- 红队报告: http://localhost:8001

本地默认使用无效的 Storage URL，因此健康检查可运行，但真实目录数据需要 Azure 身份与 Blob 配置。

## Azure 工作流

为目标 Azure tenant、subscription、资源组和区域创建 AZD 环境。部署专用标识不写入版本库；配置环境前，先轮换任何可能已经暴露的凭据：

```sh
az login --tenant '<tenant-id>'
az account set --subscription '<subscription-id-or-name>'
azd env new dev
azd env set AZURE_LOCATION '<azure-region>'
azd env set AZURE_RESOURCE_GROUP '<resource-group-name>'
azd env set AZURE_IMAGE_API_KEY '<rotated-image-key>'
azd env set AZURE_SPEECH_KEY '<rotated-speech-key>'
```

部署前必须完成验证：

```sh
make validate-local
azd provision --preview
```

两者都通过后才执行 `azd up`。其 post-provision hook 会安装 KARS、构建真正的 OpenClaw 派生镜像、应用 KARS 资源并注入运行时凭据。只更新单个服务时使用 `azd deploy media-app` 或 `azd deploy redteam-app`——两个镜像都把 `apps/` 下的 SPA 打包进去，所以改前端同样需要重新部署。

详细说明见 [部署](docs/deployment.md)、[运行手册](docs/runbook.md)、[安全](docs/security.md) 和 [红队测试](docs/red-team.md)。

## 云端 Red-team 测试

Red-team 会针对已部署的目标 Sandbox 运行五套 KARS 语料：越狱、提示注入、禁用工具、非法出口和跨会话记忆隔离。仅在目标 `KarsSandbox` 为 `Running` 且评估 CronJob 已生成后执行：

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

`make redteam` 会依次输出 `[1/5]` 到 `[5/5]`，创建五个符合 Restricted 安全策略的一次性 Job，每五秒展示一次状态变化；任一 suite 失败或超时都会以非零状态退出。基础设施检查通过或请求被阻止，不代表安全测试自然通过：必须检查每套测试的 `total`、`passed`、`failed` 和失败原因。危险用例被允许，或者传输、RBAC、资源缺失导致目标控制未被真正验证时，都必须保持发布 gate 关闭。

启用 Entra 的 AKS 在本地使用 `kubectl` 时需要 `kubelogin`。如果本机没有该插件，可通过 AKS Run Command 执行同一仓库脚本，无需启用 local cluster accounts：

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

AKS Run Command 成功只表示远程操作已执行，仍须查看日志中的各 suite 计数和最终 `Red-team run completed` 信息。runner 不会自动发布仪表盘报告；需要把结果归一化为 `EvalReport` schema，上传至配置的私有报告容器的 `reports/<run-id>.json`，再访问 `$(azd env get-value REDTEAM_APP_URI)/api/reports` 验证。发布判定和 KARS `v0.1.25` 兼容说明见 [Red-team 测试](docs/red-team.md)。

## 数据约定

每日产物位于 `yymmdd/`，图片、音频、视频按语言分开存放：

```text
yymmdd/imgs/{cn,en}/{cover,01..10,end}.png
yymmdd/audio/{cn,en}/{cover,01..10,end}.wav
yymmdd/video/final_{cn,en}.mp4
yymmdd/manifest.json
yymmdd/reactions/{like,star}/{voter}.json
```

`manifest.json` 是目录、引用、脚本、时长与媒体 URL 的唯一索引；存在当日 manifest 时，heartbeat 不重复生成。

`GET /api/videos` 返回所有 `status` 为 `ready` 的 manifest，每条带一个 `languages` 映射，内含该语言视频与封面的短时效只读 SAS。`playback_url` 与 `cover_url` 仍保留在响应中并指向中文资源，因此不解析该映射也能读取目录。双语流水线之前发布的节目使用扁平的 `assets` 结构，会按纯中文节目返回，而不是因缺字段报错。

## 点赞与评分

点赞和星级评分按节目持久化，每个地址每期只能点一次赞、评一次分。

```text
GET  /api/videos/{yymmdd}/reactions
POST /api/videos/{yymmdd}/reactions   {"kind": "like" | "star", "value": 1..5}
```

两者都返回实时总数，以及当前调用方已投的内容：

```json
{"likes": 2, "stars": {"votes": 2, "total": 9, "average": 4.5}, "mine": {"like": true, "star": 4}}
```

`POST` 额外返回 `accepted` 字段：成功记录返回 `201`，该地址已投过则返回 `409`。被拒绝的请求同样带回当前总数，因此前端直接渲染即可，不必丢弃这份有用的响应。`like` 的 `value` 会被强制为 `1`，`star` 必须在 `1..5` 之间（否则 `422`）；日期必须是 6 位数字（否则 `400`）。前端从不在本地自增——每次渲染都由服务端刚返回的汇总驱动。

每一票都是一个独立 blob `yymmdd/reactions/{kind}/{voter}.json`，以 `overwrite=False` 上传，SDK 会将其发送为 `If-None-Match: *`。因此"每地址一票"是存储层的原子 create-if-absent，两次并发点击不会像"先读后写"那样双双成功。星级分数同时写入 blob metadata，汇总时一次 list 调用即可累加，无需下载每一票的正文。

投票者标识是调用方地址加盐后的 SHA-256 截取 32 位十六进制，所以存储中不会写入任何原始 IP；可通过 `REACTION_VOTER_SALT` 覆盖盐值。Container Apps 在 ingress 终结 TLS，真实调用方只保留在 `X-Forwarded-For` 中——该请求头由客户端提供，因而可被伪造。这是防止误触重复投票的礼貌性约束，不是鉴权边界。
