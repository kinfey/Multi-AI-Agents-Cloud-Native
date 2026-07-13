# Azure OpenClaw ACA Sandbox 演示

> For the English version, see [README.md](README.md).

这个工作区把 **OpenClaw** 部署到 **Azure Container Apps (ACA)**，并把工具执行隔离在 **ACA Sandbox** 中，而不是退回到 Docker、SSH 或关闭 sandbox。最终形态是一个基于 **Microsoft Foundry `gpt-5.5`** 的多代理编程工作流，对外提供 Control UI，并把代理工具执行委托给预创建的 ACA Sandbox。

## 这个仓库交付什么

- 运行在 **Azure Container Apps** 中的 OpenClaw gateway
- 已切换为 **`backend: "aca"`** 的 OpenClaw sandbox 后端
- 通过 Entra ID 访问 **Microsoft Foundry / Azure AI Foundry**
- 通过 **Azure Files** 持久化配置、工作区和浏览器配对状态
- 一个带审核门禁的 Python 多代理流程：
  1. Requirement Agent
  2. Coding Agent
  3. Testing Agent
  4. Deployment Agent
  5. Save Agent

## 当前已验证状态

- 在线应用：`azure-openclaw-aca-app`
- 在线区域：`swedencentral`
- 公网地址：`https://azure-openclaw-aca-app.bluedune-876fc257.swedencentral.azurecontainerapps.io`
- 当前健康 revision：`azure-openclaw-aca-app--0000013`
- 当前镜像：`azureopenclawaca20260706.azurecr.io/openclaw@sha256:b115feb3ddd37cb8ca18b9bdb3598bf543a1d47a2367dec8e4ba8c488ad89623`

虽然最初的请求目标是 `rg-kinfey` / `westus`，但这个工作区里已经验证通过的实际部署运行在 **Sweden Central**。

## 为什么这里强调 ACA Sandbox

这个项目**没有**关闭 sandbox 模式，而是把 OpenClaw 和 ACA Sandbox 串起来：

- OpenClaw gateway 运行在 **ACA**
- OpenClaw 的工具调用走自定义 **ACA sandbox backend**
- 后端通过 `aca sandbox exec` 和文件同步工作
- 通过 `OPENCLAW_ACA_SANDBOX_ID` 复用一个预先创建的 ACA Sandbox
- coding/testing/deployment/save 四个代理**共用同一个** sandbox 工作区
  （`sandbox.scope: "shared"`），从而操作同一份项目文件

也就是说，真正的执行隔离边界仍然是 **ACA Sandbox**，而不是本地 Docker、原始 SSH 或无隔离执行。

## 共享沙箱工作区（多代理文件共享）

五个代理必须把**同一个**项目目录沿链路传递下去：Coding Agent 写入、Testing Agent
运行、Deployment Agent 部署、Save Agent 打包。只有当这些代理共用一个工作区时，这条
链路才成立。

配置见 [docker/entrypoint.sh](docker/entrypoint.sh)：

```json5
agents: {
  defaults: {
    workspace: "/state/openclaw/workspaces",
    sandbox: { mode: "all", backend: "aca", scope: "shared", workspaceAccess: "rw", … },
  },
  list: [
    { id: "requirements-agent", workspace: "/state/openclaw/workspaces/project", … },
    { id: "coding-agent",       workspace: "/state/openclaw/workspaces/project", … },
    { id: "testing-agent",      workspace: "/state/openclaw/workspaces/project", … },
    { id: "deployment-agent",   workspace: "/state/openclaw/workspaces/project", … },
    { id: "save-agent",         workspace: "/state/openclaw/workspaces/project", … },
  ],
}
```

- **`scope: "shared"`** —— 所有代理解析到同一个 sandbox scope key（`shared`），
  因而落在同一个 sandbox 工作区，而不是 `scope: "agent"` 那样按代理隔离。
  （支持的取值：`session` | `agent` | `shared`。）
- **统一的 `workspace` 路径** —— 每个代理都指向
  `/state/openclaw/workspaces/project`，配合 `workspaceAccess: "rw"`，各代理的
  有效工作目录完全一致。
- **固定的 `app/` 项目目录** —— 编排器的代理提示词要求 Coding Agent 在 `app/`
  里构建（首轮先清理），Testing Agent 先 `cd app` 再跑测试，Deployment Agent
  部署 `app/`。

> **为什么重要：** 早前用 `scope: "agent"` + 各自工作区时，Coding Agent 写到
> `.../coding-agent`，而 Testing Agent 却去 `.../testing-agent`（空目录）里找 ——
> 于是每一轮测试都以“文件不存在”失败。正是共享工作区，才让 MCP 编排器里的评审门
> 能够真正校验到真实代码。

## 架构

### Markdown 架构图（非 Mermaid）

```text
+---------------------------+        +--------------------------------------+
| 浏览器 / 远程 CLI         |        | Microsoft Foundry / Azure AI Foundry |
| - Control UI              |        | - gpt-5.5 部署                      |
| - 外部运维操作员          |        | - Entra ID 鉴权                     |
+-------------+-------------+        +------------------+-------------------+
              |                                       ^
              | HTTPS / WSS                           |
              v                                       |
+--------------------------------------------------------------------------+
| Azure Container Apps Ingress                                             |
| - 公网 FQDN                                                              |
| - 转发 HTTP Control UI 与 Gateway WebSocket                              |
+----------------------------------+---------------------------------------+
                                   |
                                   v
+--------------------------------------------------------------------------+
| Azure Container App: OpenClaw Gateway                                    |
|                                                                          |
|  OpenClaw Gateway                                                        |
|  - token 鉴权                                                            |
|  - Control UI 静态资源                                                   |
|  - allowedOrigins 自动推导                                               |
|  - 多代理编排                                                            |
|                                                                          |
|  自定义 ACA Sandbox Backend                                              |
|  - backend: \"aca\"                                                       |
|  - 调用 `aca sandbox exec`                                               |
|  - 把 workspace 文件同步进 sandbox                                       |
|                                                                          |
|  Azure CLI / ACA CLI                                                     |
|  - managed identity 登录                                                 |
|  - 获取 Foundry token                                                    |
+----------------------+----------------------------+----------------------+
                       |                            |
                       | 挂载                        | sandbox exec / fs cp
                       v                            v
+----------------------------------+    +----------------------------------+
| Azure Files Share                |    | ACA Sandbox Group / Sandbox      |
| - openclaw.json5                 |    | - 预创建 sandbox 实例            |
| - 共享 `project` 工作区          |    | - 隔离命令执行                   |
|   (app/ 由 coding 构建、testing  |    | - scope: "shared" 跨代理共享     |
|    运行、deploy 部署)            |    | - /tmp 下的远端 workspace        |
| - devices/pending.json           |    +----------------------------------+
| - devices/paired.json            |
| - 只持久化 pairing 状态          |
+----------------------------------+
                       ^
                       |
                       |
+----------------------------------+
| Azure Container Registry         |
| - OpenClaw 镜像                  |
| - 支持 digest 固定发布           |
+----------------------------------+
```

### 组件职责

| 组件 | 职责 |
| --- | --- |
| Azure Container App | 托管 OpenClaw gateway 和 Control UI |
| OpenClaw gateway | 提供 UI、鉴权、代理编排 |
| 自定义 ACA backend | 在 ACA Sandbox 内执行代理工具 |
| ACA Sandbox | 提供隔离执行环境；各代理共用一个工作区（`scope: "shared"`） |
| Azure Files | 持久化配置、共享的 `project` 工作区和设备配对文件 |
| Microsoft Foundry | 提供 `gpt-5.5` 模型 |
| ACR | 存放部署镜像 |

## 已经落地的关键调整

### 运行时与配置修复

- 修正 Foundry endpoint 处理，旧的 `/models` 输入会在运行时规范化为 `/openai/v1`
- 将 Foundry provider API 固定为 `openai-responses` 以兼容 `gpt-5.5`
- 移除无效的 `agents.defaults.tools` 配置形状
- 持久化 workspace 保留在 Azure Files，agent 私有目录保留在 `/tmp`
- 将各代理切换为**共享沙箱工作区**（`sandbox.scope: "shared"`，统一到
  `/state/openclaw/workspaces/project`），使 coding/testing/deployment/save 链路
  操作同一份 `app/` 项目（见上文“共享沙箱工作区”）
- 在 AKS 上，sandbox 的 `aca` CLI 通过 gateway secret 注入 `ACA_SUBSCRIPTION` /
  `ACA_RESOURCE_GROUP` / `ACA_REGION` / `ACA_SANDBOX_GROUP`，并把 kubelet 托管身份
  授予 **Container Apps SandboxGroup Data Owner**（均在
  [scripts/deploy-aks-gateway.sh](scripts/deploy-aks-gateway.sh) 中配置），
  使 `aca sandbox exec` 无需订阅参数即可工作

### 镜像与打包修复

- 镜像构建流程现在执行 `pnpm build:docker` + `pnpm ui:build`
- Control UI 资源直接打进镜像，不再依赖首次启动时构建
- Docker context 中重新包含了 fallback templates 与 `tool-display.json`

### Control UI 修复

- `gateway.controlUi.allowedOrigins` 会从 `OPENCLAW_GATEWAY_URL` 或 ACA 注入的 hostname 环境变量自动推导
- 外部浏览器 origin 校验已通过
- 浏览器 pairing 状态通过 `OPENCLAW_PAIRING_STATE_DIR=/state/openclaw` 持久化到 Azure Files
- 这里只持久化 **pairing 文件**，而不是整个 OpenClaw runtime state；这样可以避免把 SQLite 运行时状态放到 Azure Files 后导致 startup probe 失败

### ACA 部署与代码下载

- Deployment Agent 从共享沙箱出发，使用两个幂等 Shell 辅助脚本部署 —— `deploy-build`（在 ACR 构建 `<app>:latest`）与 `deploy-finish`（创建 Container App 并返回真实 HTTPS URL）。MCP 编排器先提交构建、再轮询 `deploy-finish`；由于 ACR 构建与 `containerapp create` 会在服务端完成，这套机制可容忍 ACA 沙箱 `exec` 约 120 秒的上限。每次运行使用唯一的 `proto-<hex>` 应用名。
- Save Agent 只能调用 `package_agent_workspaces`，打包 Coding/Testing Agent 工作区并排除 `.env`、SSH、Azure 与凭据文件
- ZIP 下载路由为 `/api/saveagent/artifacts/<artifact-id>.zip`，复用 gateway Bearer Token 鉴权
- 本机 `scripts/download-agent-artifact.py` 会安全下载、解压到指定空目录，并用 VS Code Insiders（或回退到普通 VS Code）打开

## 项目结构

- [app](app)：Python 工作流与测试
- [openclaw](openclaw)：参考 OpenClaw 配置
- [docker](docker)：Docker 包装、entrypoint 与镜像定制
- [infra](infra)：ACA、ACR、Storage、身份与挂载的 Bicep
- [scripts](scripts)：sandbox 初始化、构建、部署、RBAC 和 smoke-check 辅助脚本
- [.work/openclaw](.work/openclaw)：本地 OpenClaw 源码 overlay，包含 ACA sandbox backend

## 本地工作流测试

```bash
PYTHONPATH=app python3 -m unittest discover -s app/tests
PYTHONPATH=app python3 app/main.py "Build a Python CLI calculator" --json
```

## 所需环境变量

复制 [.env.example](.env.example) 为 `.env`，然后补齐：

```bash
AZURE_RESOURCE_GROUP=rg-kinfey
AZURE_LOCATION=swedencentral
AZURE_OPENCLAW_PREFIX=azure-openclaw-aca
ACR_NAME=<globally-unique-acr-name>
STORAGE_ACCOUNT_NAME=<globally-unique-storage-name>
OPENCLAW_IMAGE=<acr-name>.azurecr.io/openclaw:latest
DOCKER_PLATFORM=linux/amd64
OPENCLAW_UPDATE_SOURCE=0
AZURE_AI_FOUNDRY_ENDPOINT=https://<foundry-resource>.services.ai.azure.com/openai/v1
AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT=gpt-5.5
OPENCLAW_GATEWAY_TOKEN=<generated-token>
OPENCLAW_GATEWAY_URL=https://<container-app-fqdn>
OPENCLAW_ACA_SANDBOX_ID=<sandbox-id>
```

## ACA Sandbox 初始化

先登录 Azure：

```bash
az login
```

初始化 sandbox group 并创建 sandbox：

```bash
scripts/setup-aca-sandbox.sh
scripts/create-sandbox.sh azure-openclaw-aca
```

这套流程会：

- 在缺失时安装 `aca` CLI
- 创建 sandbox group
- 为当前登录用户授予 `Container Apps SandboxGroup Data Owner`
- 用 `aca doctor` 验证环境
- 把 `OPENCLAW_ACA_SANDBOX_ID` 写回 `.env`

## 构建并推送 OpenClaw 镜像

```bash
export ACR_LOGIN_SERVER="<acr-name>.azurecr.io"
scripts/build-openclaw-image.sh
```

脚本会：

- 把 `https://github.com/openclaw/openclaw.git` 克隆到 `.work/openclaw`
- 覆盖本仓库里的 Dockerfile、dockerignore 和 entrypoint
- 构建定制镜像
- 在配置好 ACR 后推送 `openclaw:latest`

## 部署基础设施

```bash
scripts/deploy-infra.sh
```

重新构建后，为了避免 ACA 拉到旧的 `:latest`，更建议使用 **digest 固定发布**。

## 赋予 Foundry RBAC

```bash
# 先在 .env 中设置：
# AZURE_AI_FOUNDRY_RESOURCE_ID=/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<foundry-resource>
scripts/assign-foundry-rbac.sh
```

Container App 的 managed identity 需要对 Foundry 资源具有 **Cognitive Services OpenAI User** 角色。

## Smoke Check

```bash
scripts/smoke-check.sh
```

已验证的行为包括：

- gateway 可以成功启动
- Control UI 资源能够正常提供
- 外部浏览器 origin 可以被接受
- OpenClaw 代理执行能进入 ACA Sandbox
- 基于 `microsoft-foundry/gpt-5.5` 的 sandboxed turn 能完成

## 如何访问 Control UI

1. 打开公开的 ACA URL
2. 使用 gateway token 化访问流程
3. 远程浏览器首次访问时，批准对应的设备 pairing 请求

由于 pairing 文件现在持久化在 Azure Files：

- 待审批请求在 `devices/pending.json`
- 已批准设备在 `devices/paired.json`
- 以后即使浏览器刷新，也不再必须依赖脆弱的 ACA `containerapp exec` 才能恢复

## 下载并在本机打开 Agent 代码

完整流程成功后，Save Agent 会在其 content 中输出 `DOWNLOAD_URL=...`。提取这个带鉴权
的地址，把 ZIP 下载到本地 `code` 目录、解压，并用 VS Code Insiders（或普通 VS Code）
打开：

```bash
export OPENCLAW_GATEWAY_TOKEN='<gateway-token>'
python scripts/download-agent-artifact.py \
  'https://<gateway-fqdn>/api/saveagent/artifacts/<artifact-id>.zip' \
  './code/project'
```

脚本会安全地解压到（空的）目标目录，然后用 `code-insiders` 打开，若不存在则回退到
`code`。可用 `--editor insiders|code|auto`（默认 `auto`）显式选择编辑器，或用
`--no-open` 跳过打开。

> MCP 客户端封装脚本 `acamcp_node/scripts/mcp-curl-test.sh` 会自动完成整套流程：
> 从流式 content 中解析 `DOWNLOAD_URL`，下载、解压到 `code/project-<时间戳>`，并为你
> 打开编辑器。

## 备注

- `azure openclaw_aca` 不是合法 Azure 资源名，所以部署时统一规范化为 `azure-openclaw-aca`
- OpenClaw provider 路径为 `microsoft-foundry/<deployment-name>`，所以这里使用 `microsoft-foundry/gpt-5.5`
- 这个工作区包含一个本地 OpenClaw 源码 overlay，用来增加 ACA sandbox backend
- ACA backend 复用预创建 sandbox，不依赖 SSH 透传
- 不要提交 gateway token、device token 或 Foundry 凭据
