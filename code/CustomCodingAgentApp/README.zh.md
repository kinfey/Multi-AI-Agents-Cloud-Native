# 智能体原型工厂（Agentic Prototype Factory）

> **把一句自然语言的想法，变成在 Azure 上测试通过、即时可访问的应用原型 ——
> 全程无需离开聊天窗口。**
>
> 产品经理在 **Microsoft Teams** 里输入一句 *「做一个 BBC 风格的世界杯专题页」*，
> 几分钟后就能拿到一个**正在运行的 HTTPS 地址**和一份**可下载的源码 ZIP**。
> 幕后是五个专职 **OpenClaw** 智能体（需求 → 编码 → 测试 → 部署 → 保存），
> 由 **Microsoft Foundry `gpt-5.5`** 驱动，在共享沙箱中协作、运行真实的
> **pytest** / **Jest** 用例，并把成果发布到 **Azure Container Apps** ——
> 整个流程封装在 **模型上下文协议（MCP）** 服务之后，任何 MCP 客户端
> （GitHub Copilot、Claude、Teams 机器人）都能驱动它。

> **场景：** 面向产品经理、解决方案工程师、黑客松团队的快速原型 / Demo 构建。
> **技术：** OpenClaw · Microsoft Foundry gpt-5.5 · MCP · Azure（AKS + ACA）·
> Entra ID · Microsoft Teams Bot Framework。
> _仓库：`CustomCodingAgentApp`。_

基于 **OpenClaw** 构建的多智能体编程工作流，底层由 **Microsoft Foundry `gpt-5.5`**
提供模型能力，并通过 **模型上下文协议（MCP）** 服务对外提供接口。该工作流通过五个
专职智能体依次协作，将一句自然语言需求转化为经过评审的项目原型：
**需求 → 编码 → 测试 → 部署 → 保存**。

该方案包含三个可部署组件：

| 组件 | 职责 |
| --- | --- |
| [`acasbxapp_node`](acasbxapp_node) | **OpenClaw 网关** —— 承载五个智能体，连接 Microsoft Foundry，并暴露 OpenAI 兼容的 HTTP API。同时部署在 Azure Container Apps（ACA）**和** AKS 上。 |
| [`acamcp_node`](acamcp_node) | **MCP 服务** —— 将工作流封装为 MCP 工具（`generate_prototype`、`run_agent`、`check_gateway_health`），通过 streamable HTTP 在 `/mcp` 暴露，遵循 [ACA 独立 MCP 托管模型](https://learn.microsoft.com/azure/container-apps/mcp-overview)。 |
| [`teamsbot_app`](teamsbot_app) | **Microsoft Teams 机器人**（Node.js/TypeScript），作为 MCP 客户端 —— 在 Teams 里发一句需求，它就驱动整条工作流，实时回传每个智能体的进度，并返回部署地址 + 源码 ZIP，还可在本地自动打开结果。 |

## 交付内容

- 五智能体 OpenClaw 工作流（需求、编码、测试、部署、保存），支持智能体间编排。
- **确定性的运行前清理**：每次运行前，编排器都会清空复用沙箱中残留的所有智能体
  工作区，因此上一次任务的旧文件绝不会串入（或被打包进）新的产物。
- **带反馈回路的评审门（Review Gates）**（位于 MCP 编排器）：Testing Agent 会用
  **pytest** 跑后端测试、用 **Jest** 跑前端测试，并必须给出 `TESTS_PASSED`；测试
  失败会回传给 Coding Agent 修复（最多 3 轮）。部署完成后会做健康检查，若不可达
  则以全新应用名重建 + 重新部署（最多 2 轮）。
- **稳健的 ACA 部署（拆分为 构建 → 轮询）**：ACA 沙箱 `exec` 接口被硬性限制在约
  120 秒，短于一次 `az acr build` + `containerapp create`。因此编排器先提交构建
  （即使客户端断开，ACR 仍会在**服务端**完成构建），随后**轮询**幂等的
  `deploy-finish` 步骤，直到容器应用报出真实 URL —— 而不是在超时处直接失败。
- **每次运行使用唯一的应用名**（`proto-<hex>`），配合会检查响应体的健康检查
  （即便 HTTP 200 也能识别 Azure `ResourceNotFound`），因此上报的 `deployed_url`
  始终是全新且真正可达的应用 —— 绝不会是旧的或伪造的 URL。
- **如实上报**：工作流输出 `tests_passed` 与经过验证的 `deployed_url`，不再谎报成功。
- **Teams 内的实时进度**：Teams 机器人会逐阶段推送（`[0/5] 🧹 清理`、以 **Markdown**
  展示的项目**架构**、**测试用例 + 通过/失败表格**、`⏳ 轮询部署`、`✓ 已部署 <url>`）。
- **共享沙箱工作区**：Coding、Testing、Deployment、Save 四个智能体操作**同一份**
  项目文件（`app/`）—— Testing Agent 跑的正是 Coding Agent 写的那份代码。
- 受控的 ACA 部署，返回经过验证的 HTTPS 应用地址，随后提供对 Coding/Testing Agent
  全部文件的受 Token 保护 ZIP 下载。
- 通过 **Microsoft Foundry**（`gpt-5.5`）访问模型，使用 **Entra ID** 鉴权 ——
  工作负载中不保存 API Key。
- **OpenAI 兼容的网关 API**（`/v1/chat/completions`、`/v1/models`），其中 `model`
  字段用于指向具体智能体（`openclaw/<agentId>`）。
- **MCP 服务**，使 GitHub Copilot、Claude、Teams 机器人或任意 MCP 客户端都能以工具
  方式调用工作流。
- 两个后端组件的完整 **AKS 部署**，以及网关与 Teams 机器人的 ACA 部署。

## 架构

MCP 服务是对外的公共接口。它通过网关的 OpenAI 兼容 API 调用 OpenClaw 网关；网关在
Microsoft Foundry 上运行智能体，并（可选）在 ACA Sandbox 中执行工具。

```text
        +-------------------------------+      +-------------------------------+
        |          MCP 客户端           |      |     Microsoft Teams（聊天）    |
        | (GitHub Copilot / Claude / …) |      |   经 teamsbot_app（MCP 客户端）|
        +---------------+---------------+      +---------------+---------------+
                        |                                      |
                        +------------------+-------------------+
                                           |  MCP streamable HTTP (JSON-RPC 2.0)
                                           |  POST /mcp
                                           v
              +--------------------------------------------------+
              |  acamcp_node  —  MCP 服务 (FastMCP)              |
              |  端点: /mcp                                      |
              |  工具:                                          |
              |    - generate_prototype  (运行 5 智能体链路)     |
              |    - run_agent           (单个智能体)            |
              |    - check_gateway_health                        |
              +-----------------------+--------------------------+
                                      |  HTTPS + Bearer Token
                                      |  POST /v1/chat/completions
                                      |  model = openclaw/<agentId>
                                      v
              +--------------------------------------------------+
              |  acasbxapp_node  —  OpenClaw 网关               |
              |  端口 18789 (Token 鉴权, 控制台 UI, /v1 API)     |
              |                                                  |
              |  智能体: requirements-agent -> coding-agent      |
              |          -> testing-agent -> deployment-agent    |
              |          -> save-agent                           |
              +------------+----------------------+--------------+
                           |                      |
                           | Entra ID 令牌         | aca sandbox 执行 / 文件
                           v                      v
        +---------------------------+   +----------------------------+
        | Microsoft Foundry         |   | ACA Sandbox（隔离的        |
        | gpt-5.5 部署              |   | 工具执行环境）             |
        +---------------------------+   +----------------------------+
```

### 评审门与反馈回路

`generate_prototype` 并非盲目的线性链路 —— MCP 编排器
（`acamcp_node/app/server.py`）驱动一个**带门控的状态机**，会评审每个智能体的
产出，并在失败时回环：

```text
   [0/5] 确定性工作区清理（清空复用沙箱）
        |
        v
requirements-agent（需求）
        |
        v
   coding-agent（编码）  <----------------------+  (把测试失败回传，最多 3 轮)
        |                                        |
        v                                        |
   testing-agent（测试）  --- TESTS_FAILED ------+
        |  (用 pytest 跑后端测试 + 用 Jest 跑前端测试)
        |  TESTS_PASSED
        v
 deploy-build（提交 ACR 构建；即便超过约 120 秒上限也会在服务端完成）
        |
        v
 轮询 deploy-finish  x12（间隔 20 秒） --- STILL_BUILDING/STILL_DEPLOYING ---+
        |  DEPLOYED_URL=...                                                  |
        v                                                                    |
   健康复查: 对已部署 URL 发起 HTTP GET（检查响应体：即便 HTTP 200 也能         |
        |  识别 ResourceNotFound）                                           |
        |  不可达? -> 修复 + 以全新应用名重建并重新轮询 ---------------------+ (x2)
        |  可达
        v
    save-agent（保存）  -> 受 Token 保护的 ZIP 下载地址
```

门控参数（见 `server.py`）：`_MAX_TEST_ROUNDS = 3`、`_MAX_DEPLOY_REVIEW = 2`、
`_DEPLOY_POLL_ATTEMPTS = 12`、`_DEPLOY_POLL_DELAY_S = 20`。Testing Agent 每轮以
`TESTS_PASSED` / `TESTS_FAILED` 结论结尾；编排器在宣布成功前会用 HTTP 请求对已部署
URL 做健康检查（不仅看状态码，还会检查响应体）。最终汇总中携带 `tests_passed` 与
经过验证的 `deployed_url`。

### 为什么把部署拆分为 构建 + 轮询

ACA **沙箱 `exec` 接口被硬性限制在约 120 秒** —— 短于一次冷启动的 `az acr build`
加 `az containerapp create`。关键在于：这两条命令都在**服务端**运行，即便客户端
`exec` 断开，它们仍会在 Azure 上跑完。因此编排器不会试图在一次调用里做完全部：

1. **`deploy-build <dir> <app>`** —— 安装部署辅助脚本，写入 `.dockerignore` 以缩小
   上下文，并以 `<app>:latest` 标签启动 ACR 构建。即使客户端在约 120 秒断开，镜像
   仍会落入 ACR。Dockerfile 的 `EXPOSE` 端口会被保存供下一步使用。
2. **`deploy-finish <app>`**（幂等，最多轮询 12 次）—— 在镜像出现前报
   `STILL_BUILDING`，随后以 `--no-wait` 触发 `containerapp create`，待应用达到
   `Succeeded` 后报出 `DEPLOYED_URL=https://<fqdn>`。

正是这一改造，把旧有的 `MCP error -32001: Maximum total timeout exceeded` /
`Network issue — retry policy expired` 部署失败，变成了可靠的部署。

### 共享沙箱工作区

Coding、Testing、Deployment、Save 四个智能体运行在**同一个共享 ACA 沙箱工作区**
（`sandbox.scope: "shared"`，且所有智能体在 `acasbxapp_node/docker/entrypoint.sh`
中统一指向 `/state/openclaw/workspaces/project`）。Coding Agent 把项目写入固定的
`app/` 子目录；Testing Agent `cd app` 后运行的正是这份文件；Deployment Agent 部署
`app/`；Save Agent 打包它。若不这样做，各智能体各持独立工作区，Testing Agent 只会
看到空目录 —— 测试永远无法通过。

### 部署拓扑

存在两处运行环境。在 AKS 上，整条链路是自包含的。

```text
Azure Container Apps（已有）                    Azure Kubernetes Service (AKS)
+--------------------------------+             命名空间: openclaw
| azure-openclaw-aca-app         |             +------------------------------------------+
| OpenClaw 网关 (Dockerfile.     |             |  Ingress（托管 NGINX）                   |
| openclaw), Azure Files /state, |             |  http://<公网IP>/mcp  -> acamcp-server   |
| 系统分配标识 -> Foundry        |             |  http://<公网IP>/     -> acasbxapp-gw    |
+--------------------------------+             +-------------------+----------------------+
                                                                   |
                                                +------------------v-----------------------+
                                                |  acamcp-server  (Service :80 -> :8000)   |
                                                |    MCP /mcp                              |
                                                +------------------+-----------------------+
                                                                   | http://acasbxapp-gateway:18789
                                                +------------------v-----------------------+
                                                |  acasbxapp-gateway (Service :18789)      |
                                                |    OpenClaw 网关 + /v1 API               |
                                                |    鉴权: AKS kubelet 托管标识            |
                                                |          -> Microsoft Foundry gpt-5.5    |
                                                +------------------------------------------+
```

### 组件职责

| 组件 | 职责 |
| --- | --- |
| MCP 服务（`acamcp_node`） | 对外的 MCP 接口；通过网关 API 编排智能体链路；执行评审门与「构建→轮询」拆分部署 |
| OpenClaw 网关（`acasbxapp_node`） | 运行智能体，暴露 `/v1/chat/completions`，管理 Token 鉴权与控制台 UI |
| Teams 机器人（`teamsbot_app`） | 面向 Microsoft Teams 的 MCP 客户端；异步/主动回传结果，逐阶段推送进度，可选本地自动打开 |
| Microsoft Foundry | 提供 `gpt-5.5` 模型，使用 Entra ID 访问 |
| ACA Sandbox | 智能体工具执行的隔离运行时；coding/testing/deployment/save 共用**同一个**工作区（`app/`） |
| Azure 容器注册表（ACR） | 存放 `openclaw`、`acamcp-server` 与每次运行生成的原型镜像 |
| AKS kubelet 托管标识 | 通过 IMDS 让集群内网关访问 Foundry |

## 仓库结构

```text
CustomCodingAgentApp/
  acasbxapp_node/            OpenClaw 网关（智能体 + Foundry + 沙箱）
    app/                     Python 工作流模型与测试
    docker/                  Dockerfile.openclaw、entrypoint.sh
    infra/                   ACA 部署的 Bicep
    k8s/                     gateway.yaml（AKS）
    openclaw/                openclaw.json5 与智能体定义
    scripts/                 build-openclaw-image.sh、deploy-infra.sh、
                             deploy-aks-gateway.sh …
  acamcp_node/               MCP 服务（编排器）
    app/                     config、gateway_client、server（5 智能体链路 +
                             评审门 + 拆分部署）、tests
    docker/                  Dockerfile
    k8s/                     namespace、acamcp-server、ingress
    scripts/                 build-images.sh、deploy-aks.sh、smoke-check.sh、
                             mcp-curl-test.sh
  teamsbot_app/              Microsoft Teams 机器人（MCP 客户端）
    src/                     index.ts、teamsBot.ts、mcpClient.ts、
                             localActions.ts、cards.ts、config.ts
    appManifest/             Teams 应用包（manifest.json + 图标）
```

组件文档：

- OpenClaw 网关 + ACA Sandbox：[acasbxapp_node/README.zh.md](acasbxapp_node/README.zh.md)
  （[English](acasbxapp_node/README.md)）
- MCP 服务：[acamcp_node/README.zh.md](acamcp_node/README.zh.md)
  （[English](acamcp_node/README.md)）
- Teams 机器人：[teamsbot_app/README.zh.md](teamsbot_app/README.zh.md)
  （[English](teamsbot_app/README.md)）

## 网关 API 约定

网关暴露 OpenAI 兼容接口（通过 `gateway.http.endpoints.chatCompletions` 启用）。
`model` 字段是 **智能体目标**：

| `model` 取值 | 路由到 |
| --- | --- |
| `openclaw` / `openclaw/default` | 默认智能体 |
| `openclaw/requirements-agent` | 需求智能体 |
| `openclaw/coding-agent` | 编码智能体 |
| `openclaw/testing-agent` | 测试智能体 |
| `openclaw/deployment-agent` | 部署智能体 |
| `openclaw/save-agent` | 保存与下载智能体 |

鉴权使用网关 Token 作为 Bearer 凭据（`Authorization: Bearer <token>`）。

## 部署

### 在 AKS 上部署 OpenClaw 网关（`acasbxapp_node`）

```bash
cd acasbxapp_node
cp .env.example .env               # 设置网关 Token、Foundry 端点、沙箱 ID
./scripts/build-openclaw-image.sh  # 构建并推送 openclaw 镜像到 ACR
./scripts/deploy-aks-gateway.sh    # 授予 Foundry 角色并部署到 AKS
```

网关通过 **AKS kubelet 托管标识** 访问 Microsoft Foundry（该标识在 Foundry 资源上被
授予 `Cognitive Services User` / `Cognitive Services OpenAI User` 角色）。无需重建镜像，
也无需工作负载标识（Workload Identity）。

### 在 AKS 上部署 MCP 服务（`acamcp_node`）

```bash
cd acamcp_node
cp .env.example .env               # 设置 ACR 与集群；网关 Token 从 ../acasbxapp_node/.env 读取
./scripts/build-images.sh          # 构建并推送 MCP 镜像
./scripts/deploy-aks.sh            # 将 Secret 与清单部署到 openclaw 命名空间
./scripts/smoke-check.sh           # 验证 MCP 握手
```

### Teams 机器人（`teamsbot_app`）

```bash
cd teamsbot_app
cp .env.example .env                # 设置 MCP_URL + Basic 鉴权、机器人 appId/secret
npm install && npm run build
npm start                           # 本地运行（Bot Framework Emulator 或 Teams）
```

在 Teams 里发送任意需求，机器人会异步驱动 `generate_prototype`，逐阶段回传进度，
最终推送部署地址 + 源码 ZIP。当 `AUTO_OPEN_LOCAL=true`（仅本地运行）时，它会在浏览器
打开 `deployed_url`，把 ZIP 下载到 `DOWNLOAD_DIR`、解压，并用 VS Code / VS Code
Insiders 打开。完整的 Teams 注册与 ACA 托管步骤见
[teamsbot_app/README.zh.md](teamsbot_app/README.zh.md)。

## 使用方式

将任意 MCP 客户端连接到公共端点（`mcp.json`）：

```json
{
  "servers": {
    "openclaw-workflow": {
      "type": "http",
      "url": "http://<公网IP>/mcp"
    }
  }
}
```

或直接调用：

```bash
conda activate agentdev
python - <<'PY'
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
async def main():
    async with streamablehttp_client("http://<公网IP>/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("generate_prototype",
                {"requirement": "构建一个 Todo 列表的 REST API",
                 "output_dir": "./code"})   # 下载 + 解压位置（可选）
            print(res.structuredContent["summary"])
asyncio.run(main())
PY
```

下载 + 解压已**内置到工具里**：工件会下载到 `output_dir`（默认 `~/Downloads`）并解压
（`summary.saved_dir`）。用编辑器打开结果是**单独的客户端步骤**（MCP server 可能是远程
的）。从 content 中提取 Save Agent 的 `DOWNLOAD_URL`，用 VS Code Insiders（找不到时回退
到普通 VS Code）打开：

```bash
cd acasbxapp_node
export OPENCLAW_GATEWAY_TOKEN='<gateway-token>'
python scripts/download-agent-artifact.py \
  'https://<gateway-fqdn>/api/saveagent/artifacts/<artifact-id>.zip' \
  './code/project'   # --editor insiders|code|auto
```

或者让 MCP 客户端封装脚本自动完成整套流程（握手 → 流式获取 → 解析 `DOWNLOAD_URL`
→ 下载 → 解压到 `code/project-<时间戳>` → 打开编辑器）：

```bash
cd acamcp_node
MCP_BASIC_AUTH_PASSWORD='<mcp-ingress-password>' \
OPENCLAW_GATEWAY_TOKEN='<gateway-token>' \
CODE_DIR="$PWD/code" \
./scripts/mcp-curl-test.sh "为待办清单构建一个 REST API"
```

下载脚本要求目标目录为空，校验 ZIP 路径与符号链接后才解压；优先调用
`code-insiders`，找不到时回退到 `code`，再回退到 macOS 的 `open -a` 启动器。

## 安全说明

- 网关的 OpenAI 兼容端点具备 **操作员级别访问权限**，由网关 Token 保护。生产环境请
  保持在私有 Ingress 后，并在对外暴露前启用 TLS 与鉴权。
- 工作负载中不存放模型 API Key —— 模型访问通过 Entra ID 托管标识代理。
- 网关 Token 以 Kubernetes Secret 形式存放，绝不打包进镜像。

---

For the English version, see [README.md](README.md).
