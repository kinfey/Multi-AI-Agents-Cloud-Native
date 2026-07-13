# teamsbot_app — 面向 OpenClaw MCP 工作流的 Microsoft Teams 机器人

> English docs: [README.md](README.md)。

一个 **Microsoft Teams** 聊天机器人（Node.js + TypeScript，基于 Bot Framework），
作为 [`acamcp_node`](../acamcp_node) OpenClaw 工作流 MCP 服务的 **MCP 客户端**。
在 Teams 里发送一句自然语言需求，机器人就会驱动完整的多智能体工作流
（需求 → 编码 → 测试 → 部署 → 保存），完成后**主动**回传已部署地址与源码下载链接。

## 架构

```text
+-------------------+   Bot Framework    +--------------------------+  MCP (streamable HTTP)
|   Microsoft Teams | <----------------> |  teamsbot_app（本仓库）  | ----------------------+
|   聊天客户端      |   /api/messages    |  restify + CloudAdapter  |                       |
+-------------------+                    |  @modelcontextprotocol   |                       v
                                         +--------------------------+        +---------------------------+
                                                     ^                       |  acamcp_node MCP 服务器   |
                                                     |  主动推送结果         |  (FastMCP, AKS 上 /mcp)   |
                                                     +---- 卡片推送 --------|  generate_prototype,      |
                                                                             |  run_agent,               |
                                                                             |  check_gateway_health     |
                                                                             +-------------+-------------+
                                                                                           | HTTPS + 网关令牌
                                                                                           v
                                                                             +---------------------------+
                                                                             |  acasbxapp_node 网关      |
                                                                             |  (ACA 上的 OpenClaw)      |
                                                                             +---------------------------+
```

机器人**不重复实现**工作流，只调用 MCP 工具。所有模型编排、评审门（review
gates）与沙箱执行都保留在 `acamcp_node` / `acasbxapp_node` 中。

## 为什么用异步 / 主动推送

`generate_prototype` 会运行整套带评审门的工作流，耗时约 **8–10 分钟**，远超过
Teams 约 15 秒的单轮超时。因此机器人会：

1. **立即确认**，回一张「🚀 已开始生成原型」自适应卡片，并保存会话引用
   （conversation reference）。
2. 在**后台**运行 `generate_prototype`。
3. 完成后通过 `CloudAdapter.continueConversationAsync` **主动推送**一张结果卡片
   （已部署地址、源码 ZIP、`tests_passed`、各阶段 Token 用量）。

## 命令

| 你输入 | 机器人执行 |
| --- | --- |
| 任意需求，例如 `做一个 BBC 风格的世界杯专题页` | 运行 `generate_prototype`（异步，主动回传结果）。 |
| `health` | 调用 `check_gateway_health` 并返回 JSON。 |
| `agent <agentId> <message>` | 调用 `run_agent` 单独运行某个智能体（`requirements-agent`、`coding-agent`、`testing-agent`、`deployment-agent`、`save-agent`）。 |
| `help` | 显示命令列表。 |

## 关于产物（为什么不自动打开 VS Code）

MCP 服务器运行在**远端 AKS Pod** 中，而 Teams 是托管的聊天界面——两者都无法打开
你本机的 VS Code 或写入你的磁盘。因此机器人会把 **`download_url`**（源码 ZIP）与
**`deployed_url`** 以按钮形式放在结果卡片上。若要在本地打开代码，请使用仓库里的
客户端脚本：`acamcp_node/scripts/mcp-curl-test.sh`（下载并解压到 `code/` 再打开
VS Code）或 `acasbxapp_node/scripts/download-agent-artifact.py`。

### 自动打开结果（仅本地运行）

当你在**自己的机器上**运行 bot（`npm start`）时，可让它自动打开结果。在 `.env`
里设置 `AUTO_OPEN_LOCAL=true`（并填入 `OPENCLAW_GATEWAY_TOKEN`），工作流完成后
机器人会：

1. 在**默认浏览器**打开 `deployed_url`；
2. 把 `download_url`（需网关鉴权的 ZIP）下载到 `DOWNLOAD_DIR`（默认 `./Downloads`）；
3. 解压；
4. 用 **VS Code / VS Code Insiders** 打开解压目录
   （`EDITOR_PREFERENCE=auto|insiders|code`，会回退到 macOS 应用包与 `open -a`）。

随后会把「🖥️ 本地动作」执行摘要回推到 Teams。该开关对 **ACA 托管的 bot 无效**
（容器无法访问你的浏览器/编辑器），云端部署请保持 `false`。

## 目录结构

```text
teamsbot_app/
├── package.json          # 依赖：botbuilder、@modelcontextprotocol/sdk、restify
├── tsconfig.json
├── .env.example          # Bot 与 MCP 配置
├── src/
│   ├── index.ts          # restify 服务 + CloudAdapter + /api/messages
│   ├── config.ts         # 基于环境变量的配置（含自签名 TLS 放行开关）
│   ├── mcpClient.ts       # MCP streamable-HTTP 客户端封装
│   ├── localActions.ts    # 本地浏览器/下载/VS Code 打开（可选开启）
│   ├── teamsBot.ts        # ActivityHandler：命令 + 异步工作流
│   └── cards.ts          # 自适应卡片构建（确认 / 结果 / 错误）
└── appManifest/          # Teams 应用包（manifest.json + 图标）
```

## 配置

复制 `.env.example` → `.env` 并填入取值。

| 变量 | 用途 |
| --- | --- |
| `MICROSOFT_APP_ID` / `MICROSOFT_APP_PASSWORD` | Azure Bot 注册凭据。本地 Emulator 测试可留空。 |
| `MICROSOFT_APP_TYPE` / `MICROSOFT_APP_TENANT_ID` | `MultiTenant`（默认）/ `SingleTenant` / `UserAssignedMSI`。 |
| `PORT` | 消息端点端口（默认 `3978`）。 |
| `MCP_URL` | MCP 端点（默认 `https://74.241.158.87.nip.io/mcp`）。 |
| `MCP_BASIC_AUTH_USER` / `MCP_BASIC_AUTH_PASSWORD` | 入口的 Basic 认证。 |
| `MCP_TLS_INSECURE` | 对自签名的 nip.io 主机跳过 TLS 校验时设为 `true`。 |
| `MCP_TOOL_TIMEOUT_MS` | 单次工具调用超时（默认 `900000` = 15 分钟）。 |
| `AUTO_OPEN_LOCAL` | `true`（仅本地运行）→ 自动打开浏览器 + 下载 + 用 VS Code 打开。 |
| `DOWNLOAD_DIR` | 源码保存/解压目录（默认 `./Downloads`）。 |
| `EDITOR_PREFERENCE` | `auto` / `insiders` / `code`。 |
| `OPENCLAW_GATEWAY_TOKEN` | 下载产物 ZIP 所需的网关令牌（取自 `acasbxapp_node/.env`）。 |

## 本地运行

```bash
cd teamsbot_app
cp .env.example .env          # 按需调整
npm install
npm run build
npm start                     # 或：npm run dev（ts-node，免构建）
```

用 [Bot Framework Emulator](https://github.com/microsoft/BotFramework-Emulator)
测试消息管道：指向 `http://localhost:3978/api/messages`（App ID/Password 留空）。
健康探针：`curl http://localhost:3978/healthz`。

### 仅验证 MCP 连通性

```bash
node -e 'require("dotenv").config();require("./dist/mcpClient.js").mcpService.checkGatewayHealth().then(h=>console.log(h))'
```

## 部署到 Teams

下面的步骤与本仓库实际使用的部署流程一致（Entra 应用 → 服务主体 → Azure Bot →
Azure Container Apps → Teams 应用包）。

### 1. 注册身份（Entra 应用 + 服务主体）

```bash
# 应用注册（必须单租户——多租户 Bot 创建已被弃用）。
APP_ID=$(az ad app create \
  --display-name "OpenClaw Teams Bot" \
  --sign-in-audience AzureADMyOrg \
  --query appId -o tsv)

# ⚠️ 必做：为 APP_ID 创建服务主体（企业应用）。
# 仅 `az ad app create` 不会创建它；没有 SP，机器人无法获取 Bot Framework 令牌，
# 会在 Teams 里“静默地永远不回复”
# （令牌报错 AADSTS7000229 “missing service principal in the tenant”）。
az ad sp create --id "$APP_ID"

# 客户端密钥 → .env 的 MICROSOFT_APP_PASSWORD。
az ad app credential reset --id "$APP_ID" --display-name "teamsbot-secret" --years 1 --query password -o tsv
```

把 `APP_ID`、密钥、`MICROSOFT_APP_TYPE=SingleTenant` 和你的租户 id 写入 `.env`
（`MICROSOFT_APP_ID` / `MICROSOFT_APP_PASSWORD` / `MICROSOFT_APP_TENANT_ID`）。

### 2. 创建 Azure Bot + Teams 频道

```bash
az bot create -g rg-kinfey -n openclaw-teams-bot \
  --app-type SingleTenant --appid "$APP_ID" \
  --tenant-id "$MICROSOFT_APP_TENANT_ID" \
  --endpoint "https://REPLACE_WITH_PUBLIC_HOST/api/messages"
az bot msteams create -g rg-kinfey -n openclaw-teams-bot   # 启用 Teams 频道
```

### 3. 部署到 Azure Container Apps

```bash
cd teamsbot_app
az containerapp up -n acateams-app -g rg-kinfey \
  --environment azure-openclaw-aca-env \
  --source . --ingress external --target-port 3978

# 先存密钥，再配置环境变量（密码 / MCP 密码用 secretref 引用）。
az containerapp secret set -g rg-kinfey -n acateams-app \
  --secrets app-password="$MICROSOFT_APP_PASSWORD" mcp-pass="$MCP_BASIC_AUTH_PASSWORD"
az containerapp update -g rg-kinfey -n acateams-app --set-env-vars \
  MICROSOFT_APP_ID="$MICROSOFT_APP_ID" MICROSOFT_APP_TYPE=SingleTenant \
  MICROSOFT_APP_TENANT_ID="$MICROSOFT_APP_TENANT_ID" \
  MICROSOFT_APP_PASSWORD=secretref:app-password \
  MCP_URL="$MCP_URL" MCP_BASIC_AUTH_USER="$MCP_BASIC_AUTH_USER" \
  MCP_BASIC_AUTH_PASSWORD=secretref:mcp-pass \
  MCP_TLS_INSECURE=true MCP_TOOL_TIMEOUT_MS=900000 PORT=3978
```

### 4. 把 Bot 端点指向 ACA

```bash
az bot update -g rg-kinfey -n openclaw-teams-bot \
  --endpoint "https://acateams-app.<env-fqdn>/api/messages"
# 检查：应用的 GET /healthz 应返回 {"status":"ok",...}
```

### 5. 打包并上传 Teams 应用

`manifest.json` 必须使用合法 schema——**从 manifest v1.17 起不允许 `packageName`**
（`additionalProperties: false`），故此处已省略该字段。

```bash
# 替换 ${{MICROSOFT_APP_ID}} 后，把 manifest + 图标打成 zip。
zip -j teamsAppPackage.zip appManifest/manifest.json appManifest/color.png appManifest/outline.png
```

上传该 zip 到 Teams（*应用 → 管理你的应用 → 上传自定义应用*）。

### 排障：机器人不回复

| 现象 | 原因 | 修复 |
| --- | --- | --- |
| 无回复、无入站日志、获取令牌报 **AADSTS7000229** | 应用注册**没有服务主体** | `az ad sp create --id <APP_ID>` |
| 真实 Teams 流量下 `POST /api/messages` 返回 401 | `MICROSOFT_APP_PASSWORD` 错误 / 密钥过期（新的 `credential reset` 会使旧密钥失效） | 重置密钥并更新 ACA 的 `app-password` 密钥 |
| 多租户 Bot 创建失败 | `az bot create --app-type MultiTenant` 已弃用 | 改用 `SingleTenant`（并把应用 `--sign-in-audience` 设为 `AzureADMyOrg`） |

> `appManifest/` 里的 `color.png` / `outline.png` 只是自动生成的占位图，正式发布前
> 请替换为真实的 192×192 与 32×32 图标。
