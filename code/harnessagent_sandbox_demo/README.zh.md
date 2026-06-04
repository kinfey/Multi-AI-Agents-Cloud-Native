# 《FIFA 2026 世界杯 5 分钟》Podcast Pipeline — 基于 Hyperlight 沙箱的 Harness Agents

![arc](./imgs/arch.png)

一个本地运行的 **图编排多 Agent 工作流**，每天为 **2026 FIFA 世界杯**
生成一档中文播客脚本。三个 LLM Agent 都基于
[Microsoft Agent Framework](https://github.com/microsoft/agent-framework)
的 `create_harness_agent` + `FoundryChatClient` 构建，再通过
`WorkflowBuilder`（参见
[`03-workflows`](https://github.com/microsoft/agent-framework/tree/main/python/samples/03-workflows)）
组成有向图；模型生成的不可信代码全部跑在同一个
[Hyperlight Wasm 沙箱](https://github.com/hyperlight-dev/hyperlight-sandbox)
里。

每个 Agent 都使用
[`02-agents/context_providers/code_act/code_act.py`](https://github.com/microsoft/agent-framework/blob/main/python/samples/02-agents/context_providers/code_act/code_act.py)
中的 **CodeAct** 模式：模型只能看到一个工具 — `execute_code`，其他能力
（这里只有 `fetch_url`）必须由 guest 内部通过 `call_tool(...)` 调用。

三个 Agent 的角色提示词以及共享的沙箱 / CodeAct 守卫规则，全部以
**文件化 Agent Skills** 的形式组织在 [`skills/`](skills/) 目录下，对齐
[`02-agents/skills`](https://github.com/microsoft/agent-framework/tree/main/python/samples/02-agents/skills)
示例。Agent 本身只携带一个十几行的 stub 提示词；harness 内置的
`SkillsProvider` 负责向模型加载并推送 SKILL.md 包，模型在需要时
调用 `load_skill` 获取完整内容。

## 架构

每期运行都同时跳动两个平面：宝主端的 **编排平面**（工作流图 +
LLM 客户端 + 确定性落盘）与 Hyperlight Wasm 沙箱内部的 **执行平面**
（LLM 生成代码唯一被允许运行的地方）。两者之间唯一的桥就是
guest 里的 `call_tool("fetch_url", ...)`。

```
+----------------------------------------------------------------------------+
|  宝主进程  (main.py / workflow_pipeline.py)                                |
|                                                                            |
|  WorkflowBuilder 图                                                        |
|    prepare -> SearchAgent -> adapt -> ContentAgent -> adapt                |
|             -> GenScriptAgent -> save_scripts                              |
|                                                                            |
|  +-------------------+   +-------------------+   +-------------------+     |
|  |   SearchAgent     |   |   ContentAgent    |   |   GenScriptAgent  |     |
|  | (harness/CodeAct) |   | (harness/CodeAct) |   | (harness/CodeAct) |     |
|  +---------+---------+   +---------+---------+   +---------+---------+     |
|            |                       |                       |               |
|            +-----------+-----------+-----------+-----------+               |
|                        |                       |                           |
|                        v                       v                           |
|              FoundryChatClient        function_middleware                  |
|              (AzureCliCredential)     make_tool_call_recorder              |
|                        |                       |                           |
|                        v                       v 计数 execute_code           |
|                  Azure AI Foundry                                          |
|                                                                            |
|  save_scripts（确定性 Executor，不走 LLM）                                |
|        -> ./outputs/<YYMMDD>/<YYMMDD>.simple.zh.txt                        |
|        -> ./outputs/<YYMMDD>/<YYMMDD>.tranditional.zh.txt                  |
+--------------------------------+-------------------------------------------+
                                 |
                                 |  HyperlightCodeActProvider（每个 Agent 一个）
                                 |  模型只看到一个工具：execute_code
                                 v
+----------------------------------------------------------------------------+
|  HYPERLIGHT WASM 沙箱（每轮 1 个，每次调用前还原快照）                  |
|                                                                            |
|  Python guest 在 execute_code 内部运行 LLM 生成的代码                       |
|                                                                            |
|         guest 代码:  result = call_tool("fetch_url", url="...")            |
|                                          |                                 |
+------------------------------------------+---------------------------------+
                                           |
                                           |  call_tool 调度器
                                           v
+----------------------------------------------------------------------------+
|  宝主侧工具（sandbox/podcast_tools.py）                                    |
|                                                                            |
|   fetch_url  ->  urllib + 域名白名单（仅 BBC）                              |
|                  返回：STATUS / URL / TITLE / DESCRIPTION /                |
|                        LINKS / BODY  (<= 8 KB)                             |
|                                                                            |
|   make_call_tool_counter (on_call=)                                        |
|     guest 每调一次就累加 state["tool_call_counts"][<agent>]["fetch_url"]   |
|     （middleware 看不到这些调用）。                                       |
+----------------------------------------------------------------------------+
```

该图护航了几个关键不变式：

- **模型看不到网络。** 它唯一的工具是 `execute_code`；只有当 guest
  自己调用 `call_tool("fetch_url", ...)` 时才会产生网络访问。
- **一轮一个沙箱，每次调用还原快照。** 三个 Agent 共享同一个
  `HyperlightRuntime`；每次 `execute_code` 前 guest 都会被重置为干净
  快照。
- **双路径计数。** middleware 看到的是 “模型直接调用” 的
  `execute_code`；`make_fetch_url_tool` 上的 `on_call=` 看到的是 guest
  内部发起的 `fetch_url`——Hyperlight 会直接把后者派发到 FunctionTool，
  绕过 middleware。
- **确定性落盘。** `GenScriptAgent` 只负责输出文本；文件是由
  `save_scripts` 执行器拆解两个 fenced 块后写入的，落盘环节不介入 LLM。

## 云原生架构（AKS）

跑到集群里时，前面那张本地架构图本身**不变** —— 还是同一个
`WorkflowBuilder` 图、同一个 Hyperlight 沙箱、同一个确定性
`save_scripts`。变化的只是 **身份、虚拟化访问、持久化存储如何接线**：
宿主进程现在是 AKS 上的一个 CronJob Pod，模型 token 通过 Workload
Identity 从一个用户分配托管标识（UAMI）取得，`/dev/kvm` 由 Hyperlight
device plugin 注入，两份 `.txt` 同时落到 PVC（集群内缓存）和 Azure Blob
Storage（跨集群、持久）。

```
+----------------------------------------------------------------------------+
|                         Azure 订阅 / 资源组                                |
|                                                                            |
|  +----------------------+      +-----------------------+                   |
|  | Azure AI Foundry     |      | 存储账户              |                   |
|  | （project + 模型）   |      |   容器：              |                   |
|  | - Azure AI Developer |      |   podcast-scripts     |                   |
|  | - Cognitive Services |      | - Storage Blob Data   |                   |
|  |   User               |      |   Contributor         |                   |
|  +----------+-----------+      +-----------+-----------+                   |
|             ^                              ^                               |
|             | OAuth token                  | OAuth token                   |
|             | (DefaultAzureCredential)     | (DefaultAzureCredential)      |
|             |                              |                               |
|  +----------+------------------------------+-----------+                   |
|  |        用户分配托管标识（UAMI）                     |                   |
|  |  联合凭据 subject：                                 |                   |
|  |    system:serviceaccount:podcast-pipeline:          |                   |
|  |                          podcast-pipeline           |                   |
|  +----------+------------------------------------------+                   |
|             ^ Workload Identity（OIDC token 交换）                         |
|             |                                                              |
|  +----------+----------------------------------------------------------+   |
|  | AKS 集群  （启用 OIDC issuer + Workload Identity 插件）              |   |
|  |                                                                     |   |
|  |  Namespace：podcast-pipeline  （PodSecurity: restricted）           |   |
|  |                                                                     |   |
|  |  +-------------------+   +---------------------+   +-------------+ |   |
|  |  | ServiceAccount    |   | ConfigMap           |   | PVC          | |   |
|  |  | + azure.workload. |   |  FOUNDRY_*          |   | outputs/     | |   |
|  |  |   identity/       |   |  AZURE_STORAGE_*    |   | (ReadWrite)  | |   |
|  |  |   client-id       |   |  AZURE_CREDENTIAL_  |   |              | |   |
|  |  +---------+---------+   |  KIND=default       |   +------+-------+ |   |
|  |            |             +----------+----------+          |         |   |
|  |            |                        |                     |         |   |
|  |            v                        v                     v         |   |
|  |  +------------------------------------------------------------+    |   |
|  |  | CronJob：podcast-pipeline（每日） / Job：podcast-once       |    |   |
|  |  |                                                            |    |   |
|  |  |  Pod（非 root、只读根文件系统）                            |    |   |
|  |  |   image: <acr>.azurecr.io/fifa-2026-podcast:<tag>          |    |   |
|  |  |   resources.limits:                                        |    |   |
|  |  |     hyperlight.dev/hypervisor: "1"   <-- device plugin     |    |   |
|  |  |                                          注入 /dev/kvm     |    |   |
|  |  |   command: python main.py                                  |    |   |
|  |  |     -> WorkflowBuilder 图（见上文「架构」一节）            |    |   |
|  |  |     -> Hyperlight Wasm 沙箱使用 /dev/kvm                   |    |   |
|  |  |     -> save_scripts：                                      |    |   |
|  |  |          写入 /outputs/<YYMMDD>/*.txt   --> PVC            |    |   |
|  |  |          BlobServiceClient.upload_blob() --> Storage       |    |   |
|  |  +------------------------------------------------------------+    |   |
|  |                                                                     |   |
|  |  节点：  label hyperlight.dev/hypervisor=kvm                        |   |
|  |          DaemonSet：hyperlight device plugin（CDI）                 |   |
|  |          /dev/kvm                                                    |   |
|  +---------------------------------------------------------------------+   |
|                                                                            |
|  ACR：<acr>.azurecr.io   （镜像源，已 attach 到 AKS，不需要 imagePullSecret）|
+----------------------------------------------------------------------------+
```

相对本地版本，这一层带来：

- **集群内不放任何密钥。** UAMI 在 ServiceAccount 的 OIDC subject 上
  联邦；`DefaultAzureCredential` 通过 Workload Identity webhook 自动拿
  token。没有客户端密钥，没有 SP 密码，Pod 里也不需要 `az login`。
- **硬件隔离不变。** Pod 仍然非特权（`runAsNonRoot`、只读根文件系统、
  drop caps）；当 Pod 申请 `hyperlight.dev/hypervisor: "1"` 时，device
  plugin 通过 CDI 注入 `/dev/kvm`。
- **跨集群可持久化的输出。** `save_scripts` 先写 PVC（即便上传失败也
  能保留本地产物），再尽力上传到 `<container>/<YYMMDD>/`。CronJob 本身
  无状态，Blob 账户才是真实数据源。

完整的资源、身份、角色分配流程见
[Infra/README.md](Infra/README.md)。

## 工作流图

工作流在 [workflow_pipeline.py](workflow_pipeline.py) 中通过
`WorkflowBuilder` 组装：三个 LLM Agent 是图节点，之间用小型 adapter
执行器塑形数据，落盘是一个确定性的宝主端 `Executor`（保存步骤不再走
LLM）。

```
prepare_search_prompt
        |
        v
   SearchAgent
        |
        v
adapt_search_to_content
        |
        v
   ContentAgent
        |
        v
adapt_content_to_genscript
        |
        v
  GenScriptAgent
        |
        v
   save_scripts  --> yield_output
```

| 节点 | 类型 | 模型可见的工具 | 职责 |
|---|---|---|---|
| `prepare_search_prompt` | adapter | — | 根据目标日期构造 SearchAgent 的 prompt。 |
| `SearchAgent` | harness agent (CodeAct) | `execute_code`（+ guest 的 `call_tool("fetch_url", ...)`） | 抓取 BBC 世界杯列表页，从返回记录的 `LINKS:` 段中解析文章 URL，逐个验证后输出 **当日 5 条 Top 新闻** 的 JSON。**只用 BBC**。 |
| `adapt_search_to_content` | adapter | — | 把 SearchAgent 的 JSON 包装为 ContentAgent 的 prompt。 |
| `ContentAgent` | harness agent (CodeAct) | `execute_code`（+ guest 的 `call_tool("fetch_url", ...)`） | 生成 5 段播客大纲，并对验证过的 URL 跑一轮 DeepSearch，为每段补充事实和引语。 |
| `adapt_content_to_genscript` | adapter | — | 把研究 brief 包装为 GenScriptAgent 的 prompt。 |
| `GenScriptAgent` | harness agent (CodeAct) | 仅 `execute_code`（不带宿主桥） | 写出 *《FIFA 2026 世界杯 5 分钟》* 主播 **Kinfey Lo** 的口播稿，**同时** 输出 zh-CN（央视/詹俊式解说节奏）和 zh-TW（TVB/伍晃荣式港式粤语播报）两个版本，包在两个 fenced 代码块内；**强制** 通过 `execute_code` 校验每个版本的汉字数在 1500–1900 之间。 |
| `save_scripts` | 确定性 `Executor` | — | 拆分两个 fenced 块、规范化 `host : ...` 行格式，并写入 `./outputs/<YYMMDD>/<YYMMDD>.simple.zh.txt` 与 `.tranditional.zh.txt`；当 `AZURE_STORAGE_ACCOUNT` + `AZURE_STORAGE_CONTAINER` 设置后，同步上传到 Azure Blob Storage 的 `<container>/<YYMMDD>/` 下。 |

> 文件名里的 **`tranditional`** 拼写是刻意保留的 —— 与项目规格保持一致。

三个 LLM Agent 在每次工作流运行时**共享一个** Hyperlight Wasm 沙箱
（通过 `HyperlightCodeActProvider`）。每次 `execute_code` 之前都会还原
干净快照，因此状态不会在 Agent 之间或前后两轮之间泄漏。

## 工具模型

Hyperlight 的 Python guest 自身无法发起网络请求（`urllib` 会卡死），
所以宿主侧只暴露一个桥接工具 `fetch_url`，由 guest 通过
`call_tool("fetch_url", url=...)` 调用。它在宿主侧用 `urllib` 抓取，
受白名单域限制（仅 `www.bbc.com`、`bbc.com`），
返回一份压缩到 ≤ 8 KB 的紧凑记录：

```
STATUS: 200
URL: https://www.bbc.com/...
TITLE: ...
DESCRIPTION: ...
LINKS:
  - https://www.bbc.com/sport/football/articles/<不透明 id>
  - ...

BODY:
<去除 HTML 标签后的正文，长度受 16 KB guest 输出缓冲限制>
```

宿主在剥离标签**之前**就从原始 HTML 中提取 BBC 体育文章/视频链接，
放到 `LINKS:` 字段下，因此 SearchAgent 永远不需要"猜" slug —— 不在
`LINKS:` 列表里的 URL 就当它不存在。

`GenScriptAgent` **没有**任何宿主桥接工具，只见到 `execute_code`，并仅
将其用于稿件长度校验。

工具调用计数分两条路径，因为 Hyperlight 把 guest 内部的
`call_tool(...)` 直接派发到 FunctionTool，**绕过**了 Agent 的
`function_middleware`：

- `make_tool_call_recorder(...)`（`@function_middleware`）—— 计数模型
  直接发起的 `execute_code` 调用。
- `make_call_tool_counter(...)` —— 作为 `on_call=` 传入
  `make_fetch_url_tool(...)`，让 guest 内部触发的 `fetch_url` 也能被
  正确计数。

## 项目结构

```text
.
├── main.py                       # 流式打印工作流事件，输出工具清单与命中状态
├── workflow_pipeline.py          # WorkflowBuilder 图（agents + adapters + SaveScripts）
├── requirements.txt
├── .env.sample
├── agents/
│   ├── __init__.py
│   ├── common.py                 # build_harness、FoundryChatClient、skill_path()、计数器/recorder 工具
│   ├── search_agent.py           # 薄 stub → 加载 search-bbc-worldcup + hyperlight-sandbox 两个技能
│   ├── content_agent.py          # 薄 stub → 加载 content-deepsearch + hyperlight-sandbox 两个技能
│   ├── genscript_agent.py        # 薄 stub → 加载 genscript-podcast + hyperlight-sandbox 两个技能
├── skills/                       # 文件化 Agent Skills（SKILL.md 包）
│   ├── hyperlight-sandbox/SKILL.md   # 共享的 CodeAct + call_tool("fetch_url", ...) 守卫规则
│   ├── search-bbc-worldcup/SKILL.md  # 仅 BBC 的 listing → LINKS → 验证流程与 JSON 输出
│   ├── content-deepsearch/SKILL.md   # 大纲 + DeepSearch 富化流程
│   └── genscript-podcast/SKILL.md    # zh-CN + zh-TW 脚本风格与强制长度检查
├── sandbox/
│   ├── __init__.py
│   ├── hyperlight_runtime.py     # HyperlightRuntime + workspace_root 初始化
│   ├── codeact.py                # build_codeact_provider（HyperlightCodeActProvider 包装）
│   └── podcast_tools.py          # make_fetch_url_tool（宿主 urllib + LINKS 提取）
└── outputs/                      # 首次运行时自动创建
    └── 260603/
        ├── 260603.simple.zh.txt
        └── 260603.tranditional.zh.txt
```

### Agent Skills 一览

每个 SKILL.md 都是一个自包含的 Agent Skill（YAML frontmatter +
Markdown 正文），由 harness 的 `SkillsProvider` 向模型推送并在需要
时通过 `load_skill` 加载——逐步揭示（progressive disclosure）让
每轮的 prompt 足够精简。

| 技能 | 使用者 | 包含内容 |
|---|---|---|
| `hyperlight-sandbox` | 三个 Agent | `execute_code` 模型 + `call_tool("fetch_url", ...)` 桥接格式 + BBC 白名单 + 沙箱守卫 |
| `search-bbc-worldcup` | SearchAgent | 信源策略、listing → LINKS 提取 → 逐 URL 验证、JSON 输出结构 |
| `content-deepsearch` | ContentAgent | 5 段大纲 + DeepSearch 取数计划、Markdown 输出结构 |
| `genscript-podcast` | GenScriptAgent | 《FIFA 2026 世界杯 5 分钟》风格指南（zh-CN 詹俊 / zh-TW 伍晃荣）、两个 fenced 块的输出格式、强制 1500–1900 汉字长度校验 |

接线逻辑集中在 [agents/common.py](agents/common.py)：`skill_path(*names)`
负责把 `skills/` 下的技能名解析为绝对路径；`build_harness` 负责构造
`SkillsProvider.from_paths([...])` 并以 `skills_provider=` 形式传给
`create_harness_agent`。

## 安装

### 1. Python 依赖

```powershell
conda create -n agentdev python=3.12 -y
conda activate agentdev
pip install -r requirements.txt
```

### 2. Hyperlight Wasm 沙箱

Hyperlight Python SDK 与 Python guest 模块需要从源码构建：

```powershell
git clone https://github.com/hyperlight-dev/hyperlight-sandbox.git
cd hyperlight-sandbox
just build           # Rust 后端 + Wasm guest + Python SDK
pip install src/sdk/python
```

Windows 上沙箱使用 Hyper-V 后端（需启用 Hyper-V）；Linux 上需要 KVM；
Azure/云 VM 上可能需要 MSHV。

### 3. 环境变量

复制 `.env.sample` 为 `.env` 并填入：

```ini
FOUNDRY_PROJECT_ENDPOINT=https://<your>.services.ai.azure.com/api/projects/<name>
FOUNDRY_MODEL=gpt-5.5
HYPERLIGHT_PYTHON_MODULE_PATH=C:\path\to\hyperlight-sandbox\src\wasm_sandbox\guests\python\python-sandbox.aot
PODCAST_OUTPUT_DIR=./outputs

# 可选：同时将生成的脚本上传到 Azure Blob Storage。
# 留空则跳过上传（本地 PVC / outputs 目录始终会写）。
AZURE_STORAGE_ACCOUNT=
AZURE_STORAGE_CONTAINER=podcast-scripts
```

### 4. Azure 登录

`FoundryChatClient` 使用 `AzureCliCredential`：

```powershell
az login
```

## 运行

```powershell
# 今天的一期
python main.py

# 指定日期回填
python main.py --date 2026-06-01

# 覆盖输出根目录
python main.py --output-dir D:\podcasts
```

## 部署到 Kubernetes

所有部署资产都集中在 [`Infra/`](Infra/)，并遵循
[hyperlight-dev/hyperlight-on-kubernetes](https://github.com/hyperlight-dev/hyperlight-on-kubernetes)
的上游模式：一个**非特权** Pod 申请
`hyperlight.dev/hypervisor: "1"`，节点上的 device plugin 通过 CDI 将
`/dev/kvm`（或 `/dev/mshv`）注入到容器里。

- [Infra/Dockerfile](Infra/Dockerfile) — 单阶段镜像，基于
  `python:3.12-slim`，从 PyPI 安装
  `hyperlight-sandbox[wasm,python-guest]`（wheel 已内置预编译的
  `python-sandbox.aot`），以非 root 用户 + 只读根文件系统运行。
- [Infra/k8s/](Infra/k8s/) — Namespace（Pod Security `restricted`）、
  Workload Identity ServiceAccount、ConfigMap（Foundry + Blob 目标）、
  PVC、每日 CronJob 以及一次性 Job，由
  [Infra/k8s/kustomization.yaml](Infra/k8s/kustomization.yaml) 缝合。
- [Infra/scripts/](Infra/scripts/) — `build-and-push`（bash + PowerShell）
  以及 `deploy.sh`。

当 ConfigMap 里同时设置了 `AZURE_STORAGE_ACCOUNT` 与
`AZURE_STORAGE_CONTAINER` 时，`save_scripts` 会在写完 PVC 后额外将
两个 `.txt` 文件上传到
`https://<account>.blob.core.windows.net/<container>/<YYMMDD>/`，使用
`DefaultAzureCredential`（Workload Identity → 拥有
`Storage Blob Data Contributor` 的 UAMI）。PVC 拷贝作为集群内缓存，
Blob 拷贝是跨集群的持久化归宿。

完整流程（前置条件、身份接线、部署、提取输出）参见
[Infra/README.md](Infra/README.md)。

`main.py` 的执行流程：

1. 初始化 `HyperlightRuntime`。
2. 通过 `build_pipeline(runtime, target_date, state)` 构建工作流图。
3. 打印 **Agent Tool Inventory**（每个节点配置的工具）。
4. 流式消费 `workflow.run(..., stream=True)` 事件：`executor_invoked` /
   `executor_completed` / `agent_run_update` / 工具事件 / `output`。
5. 结束时打印 **Agent Tool Status** 表 —— 绿色表示该工具至少被实际
   调用过一次，红色表示未调用。
6. 调用 `os._exit(0)` 跳过 atexit，避免 `WasmSandbox` 的 Rust drop
   不可 `Send` 在 Windows 上产生 "unsendable" / 临时目录
   `PermissionError` 报错栈。

## 安全模型

- LLM 生成的全部代码都跑在 **Hyperlight Wasm** 内，硬件级隔离于宿主。
- 模型只能看到一个工具 `execute_code`；联网仅通过单一宿主桥
  `fetch_url`，且受白名单域限制、仅允许 HTTP GET。
- 沙箱只能在宿主的 `PODCAST_OUTPUT_DIR` 下写文件；真正落盘的只有
  确定性的 `save_scripts` 执行器。
- 每次 `execute_code` 前都还原干净快照，状态、密钥、全局变量都不会在
  Agent 之间或前后两轮之间泄漏。

## 参考

- [microsoft/agent-framework — `02-agents/context_providers/code_act/code_act.py`](https://github.com/microsoft/agent-framework/blob/main/python/samples/02-agents/context_providers/code_act/code_act.py)
- [microsoft/agent-framework — `03-workflows/_start-here/step2_agents_in_a_workflow.py`](https://github.com/microsoft/agent-framework/blob/main/python/samples/03-workflows/_start-here/step2_agents_in_a_workflow.py)
- [microsoft/agent-framework — `03-workflows/control-flow/sequential_executors.py`](https://github.com/microsoft/agent-framework/blob/main/python/samples/03-workflows/control-flow/sequential_executors.py)
- [microsoft/agent-framework — `02-agents/providers/foundry`](https://github.com/microsoft/agent-framework/tree/main/python/samples/02-agents/providers/foundry)
- [microsoft/agent-framework — `02-agents/skills`](https://github.com/microsoft/agent-framework/tree/main/python/samples/02-agents/skills)
- [hyperlight-dev/hyperlight-sandbox](https://github.com/hyperlight-dev/hyperlight-sandbox)
