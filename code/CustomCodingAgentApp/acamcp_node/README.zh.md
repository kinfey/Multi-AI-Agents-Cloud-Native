# acamcp_node —— OpenClaw 工作流 MCP 服务

> For the English version, see [README.md](README.md).

一个 **MCP（Model Context Protocol）** 服务器，把 OpenClaw 多代理编程工作流以工具的
形式暴露给 AI 客户端（GitHub Copilot、Claude、自定义客户端）。它遵循 Azure Container
Apps 的
[standalone MCP server](https://learn.microsoft.com/azure/container-apps/mcp-overview)
模式（官方 SDK，`/mcp` 上的 streamable HTTP），并部署到 **AKS**。

工作流后端是 **acasbxapp_node**，即已经运行在 **Azure Container Apps** 上的 OpenClaw
gateway（由 `acasbxapp_node/docker/Dockerfile.openclaw` 构建，应用名
`azure-openclaw-aca-app`）。MCP 服务器调用 gateway 的 OpenAI 兼容 HTTP API —— 它并不
重新实现工作流。

## 架构

```text
+---------------------+     MCP (streamable HTTP, JSON-RPC 2.0)
|   MCP 客户端        | ------------------------------------------+
| (Copilot / Claude)  |                                           |
+---------------------+                                           v
                                              +-----------------------------------+
                                              | acamcp-server (本仓库) 运行于 AKS |
                                              | FastMCP, endpoint /mcp:8000       |
                                              | tools: generate_prototype,        |
                                              |        run_agent,                 |
                                              |        check_gateway_health       |
                                              +------------------+----------------+
                                                                 | HTTPS + ******
                                                                 | POST /v1/chat/completions
                                                                 | model = openclaw/<agentId>
                                                                 v
                                              +-----------------------------------+
                                              | acasbxapp_node = OpenClaw gateway |
                                              | 运行于 Azure Container Apps       |
                                              | agents: requirements/coding/      |
                                              |         testing/deployment/save   |
                                              | model: Microsoft Foundry gpt-5.5  |
                                              | 工具在 ACA Sandbox 中执行         |
                                              +-----------------------------------+
```

gateway 的 OpenAI 兼容端点把 `model` 字段当作*代理目标*（`openclaw/<agentId>`），并用
gateway token 作为 bearer 凭据鉴权。参见 gateway 文档 `docs/gateway/openai-http-api.md`。

## MCP 能力

| 类型 | 名称 | 说明 |
| --- | --- | --- |
| Tool | `generate_prototype` | 运行完整的**带门控**工作流（需求 → 编码 → 测试 → 部署 → 保存），带有评审门与反馈回路（见下文）。返回的汇总包含 `tests_passed` 与经过健康检查的 `deployed_url`。 |
| Tool | `run_agent` | 向单个代理发送一条消息并返回其回复。 |
| Tool | `check_gateway_health` | 报告 OpenClaw gateway 的健康状态。 |
| Resource | `workflow://info` | 有序的工作流阶段与 gateway 信息。 |
| Prompt | `prototype_request` | 请求原型的模板。 |

## 评审门与反馈回路

`generate_prototype` 不是盲目的线性链路。`app/server.py` 驱动一个**带门控的状态机**，
会评审每个代理的产出，并在失败时回环，从而让“绿灯”结果真正对应可运行、已部署的代码：

```text
   [0/5] 确定性工作区清理（清空复用沙箱）
      |
      v
requirements-agent（需求）
      |
      v
  coding-agent（编码）  <-------------------+  (把测试失败回传，最多 _MAX_TEST_ROUNDS 轮)
      |                                      |
      v                                      |
  testing-agent（测试）  --- TESTS_FAILED ---+
      |  (用 pytest 跑后端测试 + 用 Jest 跑前端测试)
      |  TESTS_PASSED
      v
 deploy-build（提交 ACR 构建；即便超过约 120 秒的 exec 上限也会在服务端完成）
      |
      v
 轮询 deploy-finish  x_DEPLOY_POLL_ATTEMPTS --- STILL_BUILDING/STILL_DEPLOYING --+
      |  DEPLOYED_URL=...                                                        |
      v                                                                         |
  健康复查: 对已部署 URL 发起 HTTP GET（检查响应体：即便 HTTP 200 也能              |
      |  识别 ResourceNotFound）                                                 |
      |  不可达? -> 修复 + 以全新应用名重建并重新轮询 --------------------------+ (_MAX_DEPLOY_REVIEW)
      |  可达
      v
   save-agent（保存）  -> 受 token 保护的 ZIP 下载地址
```

门控参数（`app/server.py` 中的常量）：

| 门 | 常量 | 值 | 行为 |
| --- | --- | --- | --- |
| 测试门 | `_MAX_TEST_ROUNDS` | `3` | Testing Agent 每轮以 `TESTS_PASSED` / `TESTS_FAILED` 结尾；失败时把错误回传给 Coding Agent，最多 N 轮。 |
| 部署轮询 | `_DEPLOY_POLL_ATTEMPTS` / `_DEPLOY_POLL_DELAY_S` | `12` / `20` | 提交构建后，编排器对幂等的 `deploy-finish` 步骤轮询最多 N 次（每次间隔若干秒），直到它报出真实的 `DEPLOYED_URL=`。由于 ACR 构建与 `containerapp create` 会在服务端完成，这套机制可容忍 ACA 沙箱 `exec` 约 120 秒的硬上限。 |
| 健康复查 | `_MAX_DEPLOY_REVIEW` | `2` | 编排器对已部署 URL 发 HTTP GET（会检查响应体，因此 HTTP 200 背后的旧 `ResourceNotFound` 也算不可达）；不可达则修复并以全新的 `proto-<hex>` 应用名重建、重新轮询，再宣布成功。 |

最终汇总携带 `tests_passed` 与经过验证的 `deployed_url`，并**如实**报告（例如部署始终
不可达时给出 `deployed_url: null`），而不是掩盖失败。

### 拆分为 构建 → 轮询 的部署

ACA 沙箱 `exec` 接口被硬性限制在约 120 秒，短于一次冷启动的 `az acr build` +
`az containerapp create`。由于这两条命令都在**服务端**运行并能在客户端断开后继续
完成，部署被拆分为两个幂等辅助脚本（通过一条 base64 自愈命令安装到沙箱）：

- **`deploy-build <dir> <app>`** —— 写入 `.dockerignore`，在 ACR 构建 `<app>:latest`，
  并保存 Dockerfile 的 `EXPOSE` 端口。可容忍约 120 秒的断开。
- **`deploy-finish <app>`** —— 轮询：`STILL_BUILDING` → 以 `--no-wait` 触发
  `containerapp create` → 达到 `Succeeded` 后报出 `DEPLOYED_URL=https://<fqdn>`。

**确定性的运行前清理**（`[0/5]` 阶段）会先清空复用沙箱中残留的所有智能体工作区，
因此上一次运行的旧文件不会串入或被打包进新产物。

coding/testing/deployment/save 代理运行在**共享沙箱工作区**里（在
`acasbxapp_node/docker/entrypoint.sh` 中配置为 `scope: "shared"`），全部操作固定的
`app/` 项目目录 —— 这正是测试门能够校验到 Coding Agent 真实产出的前提。参见
[acasbxapp_node README](../acasbxapp_node/README.zh.md#共享沙箱工作区多代理文件共享)。

## 目录结构

```text
acamcp_node/
  app/
    __init__.py
    config.py           # 环境驱动配置 (gateway URL/token, agents)
    gateway_client.py   # OpenClawGatewayClient (/v1/chat/completions)
    server.py           # FastMCP 服务器 + 工具 (build_server 工厂)
    __main__.py         # 运行 streamable HTTP transport
    tests/test_server.py
  docker/Dockerfile
  k8s/
    namespace.yaml
    acamcp-server.yaml  # Deployment + Service (通过 secret 注入 gateway token)
    ingress.yaml        # 托管 NGINX (app routing) ingress
  scripts/
    build-images.sh     # 构建并推送 MCP 镜像
    deploy-aks.sh       # 创建 gateway-token secret + 应用清单
    smoke-check.sh
  requirements.txt
  pyproject.toml
```

## 配置

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `OPENCLAW_GATEWAY_URL` | ACA app URL | OpenClaw gateway 基础 URL |
| `OPENCLAW_GATEWAY_TOKEN` | _(空)_ | Gateway bearer token（AKS 中为 K8s secret） |
| `GATEWAY_TIMEOUT_SECONDS` | `300`（config 默认；AKS 清单设为 `1800`） | 每个代理调用的超时时间 |
| `OPENCLAW_LOCAL_SAVE_DIR` | `~/Downloads` | 省略 `output_dir` 时的默认下载/解压目录 |
| `MCP_HOST` | `0.0.0.0` | MCP 绑定地址 |
| `MCP_PORT` | `8000` | MCP 绑定端口 |

## 前置条件：启用 gateway HTTP API

gateway 的 OpenAI 兼容端点默认关闭。本仓库在
`acasbxapp_node/docker/entrypoint.sh` 和 `openclaw/openclaw.json5` 中启用它：

```json5
gateway: { http: { endpoints: { chatCompletions: { enabled: true } } } }
```

改动之后，重建 OpenClaw 镜像并滚动新的 ACA revision：

```bash
cd ../acasbxapp_node && ./scripts/build-openclaw-image.sh
az containerapp update -n azure-openclaw-aca-app -g rg-kinfey \
  --image <acr>/openclaw@<new-digest>
```

> 安全提示：这个端点是由 gateway token 保护的运维级访问。生产中请放在私有 ingress
> 后，并为公开暴露加上 TLS 与鉴权。

## 本地开发

```bash
conda activate agentdev
cd acamcp_node
pip install -r requirements.txt

OPENCLAW_GATEWAY_URL=https://azure-openclaw-aca-app.bluedune-876fc257.swedencentral.azurecontainerapps.io \
OPENCLAW_GATEWAY_TOKEN=<token> \
MCP_HOST=127.0.0.1 MCP_PORT=8010 python -m app
# MCP endpoint: http://127.0.0.1:8010/mcp
```

### 运行测试

```bash
conda activate agentdev
cd acamcp_node
python -m pytest -q
```

## 部署到 AKS

```bash
cd acamcp_node
cp .env.example .env    # 填入 AZURE_RESOURCE_GROUP, AKS_CLUSTER_NAME, ACR_NAME
# 若 OPENCLAW_GATEWAY_TOKEN 留空，会从 ../acasbxapp_node/.env 读取。

./scripts/build-images.sh   # 构建并推送 acamcp-server
./scripts/deploy-aks.sh      # 把 secret + 清单应用到 openclaw namespace
./scripts/smoke-check.sh     # port-forward + MCP 握手
```

## 连接 MCP 客户端

已部署到 AKS（`kinfey-aks-openclaw-cluster`，namespace `openclaw`），并通过 app
routing ingress 发布。公网端点：

```
http://74.241.158.87/mcp
```

VS Code `mcp.json`：

```json
{
  "servers": {
    "openclaw-workflow": {
      "type": "http",
      "url": "http://74.241.158.87/mcp"
    }
  }
}
```

## 在本机下载并打开结果

当 `generate_prototype` 完成后，Save Agent 会在其 content 中输出 `DOWNLOAD_URL=...`，
工具汇总也会以 `download_url` 字段返回它（同时还有 `deployed_url` 和 `tests_passed`）。
**下载 + 解压已内置到工具里** —— 工件会自动下载到 `output_dir`（默认 `~/Downloads`，
或 `OPENCLAW_LOCAL_SAVE_DIR`）并解压；`summary.saved_zip` 和 `summary.saved_dir`
指向结果。

用编辑器打开项目是**单独的客户端步骤** —— MCP server 可能运行在远程 pod 里，所以它
不会启动你本地的 VS Code。内置的客户端封装脚本会完成整套流程（握手 → 流式获取 → 解析
`DOWNLOAD_URL` → 下载 → 解压到 `code/project-<时间戳>` → **在你的机器上**打开编辑器）：

```bash
MCP_BASIC_AUTH_PASSWORD='<mcp-ingress-password>' \
OPENCLAW_GATEWAY_TOKEN='<gateway-token>' \
CODE_DIR="$PWD/code" \
./scripts/mcp-curl-test.sh "构建一个世界杯单页应用 (FastAPI + HTML/CSS/JS)"
```

`CODE_DIR` 默认为 `<repo>/code`。脚本优先使用 `code-insiders`，不存在时回退到 `code`，
再回退到 macOS 的 `open -a` 启动器。如果你已经下载了 ZIP，可用
[`acasbxapp_node/scripts/download-agent-artifact.py`](../acasbxapp_node/scripts/download-agent-artifact.py)
（`--editor insiders|code|auto`）打开。
