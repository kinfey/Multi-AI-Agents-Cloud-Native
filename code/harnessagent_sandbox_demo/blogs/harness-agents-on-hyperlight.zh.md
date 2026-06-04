# 在 Hyperlight 上跑 Harness 智能体：让模型生成的不可信代码在 MicroVM 中执行

> 一篇技术布道视角的走读：用一个虽小但接近生产形态的真实工作负载——
> 一档每日生成的 **2026 FIFA 世界杯** 中文 5 分钟播客脚本流水线——
> 把 [Microsoft Agent Framework][maf] 的 harness 智能体和
> [Hyperlight][hl] microVM（通过 [`hyperlight-sandbox`][hls]）串到一起。
> 文章按"为什么需要 microVM → Hyperlight → harness 模式 → 真实项目"
> 一路展开。

---

## 1. MicroVM 在智能体中的意义

LLM 智能体有一个不太方便的事实：**模型会写代码，而代码会被执行**。
你只要给智能体一个 `python` 工具、一个 `bash` 工具，甚至只是一个
"帮我抓这个 URL"的工具，你实际上就拥有了一个**作者是概率文本生成器
的远程代码执行入口**。Prompt injection、jailbreak、以及单纯被幻觉
出来的 `rm -rf` 不再是 paper 里的事 —— 是周二下午会发生的事。

进程级别的沙箱（subprocess + seccomp、Python `exec`、单独的 Docker）
本来就不是为这种威胁模型设计的：它们和宿主共享同一个内核，内核
syscall 表面上的任何一个 CVE，都是模型和你的笔记本之间的一个 CVE。
容器把门槛抬高了，但**边界**没动。

**MicroVM 改变的是边界。** microVM 是一种被裁掉了所有不必要东西的
虚拟机 —— 没有固件、没有 PCI 总线、只有工作负载真正需要的硬件 ——
跑在 KVM、Hyper-V 或 MSHV 之上，启动只要毫秒级。Firecracker 把这套
模型在 AWS Lambda 上证明了一遍，gVisor、Kata 把它推得更远，而
[Docker 团队最近自己也写文章承认][docker-microvm]：之所以
Docker Sandboxes 现在的默认架构是 microVM，正是因为
*"当里面的代码不可信时，内核级边界才是正确的边界。"*

对于智能体来说，microVM 解锁了三件真正重要的事：

1. **硬件级隔离。** 哪怕 guest 里发生了内核漏洞，也只到 hypervisor
   为止。宿主内核与宿主文件系统不在模型的影响半径里。
2. **毫秒级 snapshot/restore。** 每一次工具调用都可以从干净状态出发：
   秘密、环境变量、socket、上一轮记得 `import os` —— 通通在调用之间
   蒸发。
3. **极小、可嵌入的体积。** 没有 QEMU、没有 init、没有
   `/usr/lib/...`。VM 是宿主进程里的一次函数调用，意味着智能体的
   运行时可以**自己拥有**沙箱生命周期，而不是 shell 出去跑 Docker。

最后这条把我们带到了 Hyperlight。

## 2. Hyperlight 与 `hyperlight-sandbox`

[**Hyperlight**][hl] 是 Microsoft 的一个开源轻量级 VMM，定位是
**被嵌入到宿主应用进程里**。它 README 上写得很直接：

> *"Hyperlight 是一个轻量级 VMM，被设计为嵌入到应用程序内。它能以
> 极低延迟和极小开销，在 microVM 中安全地执行不可信代码。"*

它和经典 microVM 栈相比的差异点：

- **没有 guest OS、没有 kernel。** Hyperlight 跑的是一个**裸**的
  guest —— 通常是一个 Wasm 运行时或者一个很小的 C ABI —— 直接坐在
  hypervisor 上（Linux 上是 KVM，Azure 上是 MSHV，Windows 上是
  Hyper-V）。VM 启动是亚毫秒级，因为根本没有什么东西要启动。
- **像调用库一样调用它。** 宿主应用**创建**沙箱、**调用**guest
  函数、拿回结果、**销毁**沙箱 —— 同步、同进程，一次完成。
- **每次调用 snapshot。** 沙箱可以在每一次调用之前被还原到一个
  已知良好的快照 —— 这正是 LLM 循环最想要的属性。

[**`hyperlight-sandbox`**][hls] 则是把 Hyperlight 包装成应用开发者
真的能用的层：一个多后端沙箱框架，自带一组预构建的 guest（特别是
**Python guest**）、一个 **Python SDK**
（`pip install hyperlight-sandbox[wasm,python-guest]`），以及一个
受控的宿主能力桥 —— `call_tool(...)`。guest 代码不能拿到 syscall，
只能向宿主请求**一个具名的桥操作**。Wheel 里甚至已经把编好的
`python-sandbox.aot` 一并发了出来，所以 Python guest 真就是一行
`pip install` 的事。

概念上：

```
+-------------------+        +--------------------------+
|  宿主 Python 应用 | -----> |  Hyperlight microVM      |
|  （智能体循环）   |   |    |   Wasm Python guest      |
|                   |   |    |   用户代码在这里运行     |
|  call_tool 桥    | <----- |   guest -> call_tool(...) |
+-------------------+        +--------------------------+
```

宿主决定**哪些** `call_tool` 名字存在。模型看不到 socket、看不到
文件、看不到环境变量 —— 只能看到你显式开放的那些桥。

## 3. 什么是 Harness Agent（驾驭式智能体）

智能体生态这两年攒了一堆互相重叠的词 —— "agent"、"scaffold"、
"harness"、"tool runtime"。Hugging Face 的
[*Agent Glossary*][hf-glossary] 是目前画线画得最干净的一篇，它的
切法和我们关心的完全一致：

> *Harness 是承载模型的运行时：把模型接到工具上、调度 I/O、
> 强制执行规则。模型是大脑，harness 是神经系统加安全带。*

具体地，一个 harness 拥有：

- **本轮模型可见的工具注册表**
- **Skills / 渐进式上下文** —— 哪些内容塞进 prompt、哪些按需加载
- **Middleware** —— 日志、脱敏、限流、计数器
- **执行策略** —— 工具**到底在哪里**跑：进程内？子进程？microVM？

这正是 Hyperlight 价值兑现的位置。如果你的 harness 声明：
"模型唯一能看到的工具是 `execute_code`，且 `execute_code` 永远在
Hyperlight 沙箱里跑"，那 LLM-RCE 的整个风险面就被压缩到了一个
良定义的边界上。

## 4. Microsoft Agent Framework：把 Harness 做成一等公民

[Microsoft Agent Framework][maf] 是 Semantic Kernel 与 AutoGen 的
开源演进版，2025 年底起把 **harness agent** 做成了一等概念 ——
团队的深度文章是
[*Agent Harness in Agent Framework*][maf-harness]。本文用到的几个
核心原语：

- **`create_harness_agent(...)`** —— 产出一个智能体，它的工具表面、
  skills 与 middleware 由 harness 拥有，而不是塞在 prompt 里。
- **`SkillsProvider`** —— 文件化的 **Agent Skills**（带 YAML
  frontmatter 的 `SKILL.md` 包，对齐
  [`02-agents/skills`][maf-skills] 示例）。harness 把 skills 列表
  推给模型，模型按需通过 `load_skill` 把正文拉进上下文 —— 这就是
  *progressive disclosure*：每轮 prompt 保持精简，领域规则放在
  版本化的文件里，而不是膨胀的 system prompt 里。
- **`HyperlightCodeActProvider`**（一种 `ContextProvider`）—— 把
  harness 的工具执行接到一个 `hyperlight-sandbox` 运行时上：模型
  只看到一个工具 `execute_code`，所有额外能力**只能从 guest 里**
  通过 `call_tool(...)` 触达。
- **`WorkflowBuilder`** —— 一个图编排器（见
  [`03-workflows`][maf-workflows] 示例），允许把多个 harness
  agent 排在 DAG 上，节点之间用确定性的 adapter / save executor
  做形状变换与落盘。**LLM 不出现在持久化路径上、也不出现在系统
  边界上。**

四件事拼起来，就是 **CodeAct-on-microVM** 模式：harness 立规矩、
workflow 整数据、skills 装领域知识、Hyperlight 执行边界。

## 5. 项目介绍：每日世界杯播客流水线

为了把上面这些落到地上，
[harness-agents-sandbox-demo](../README.zh.md) 仓库刻意构造了一个
**虽然小、但形状接近生产**的工作负载：每天为 **2026 FIFA 世界杯**
生成一档 5 分钟中文播客脚本，且**同时输出**两个版本 —— **zh-CN**
（央视/詹俊式解说节奏）与 **zh-TW**（TVB/伍晃荣式港式粤语播报）。

### 工作流图

```
prepare -> SearchAgent -> adapt -> ContentAgent -> adapt
        -> GenScriptAgent -> save_scripts
```

三个 harness 智能体、三个 adapter executor、一次确定性落盘：

| 节点 | 类型 | 模型可见的工具 |
|---|---|---|
| `SearchAgent` | harness + CodeAct | `execute_code`（+ guest `call_tool("fetch_url", ...)`），仅 BBC，输出当日 Top 5 JSON |
| `ContentAgent` | harness + CodeAct | `execute_code`（+ guest `fetch_url`），对验证过的 URL 跑 DeepSearch |
| `GenScriptAgent` | harness + CodeAct | **仅** `execute_code`，写出两版脚本并强制校验汉字数 1500–1900 |
| `save_scripts` | 确定性 `Executor` | — | 拆分两个 fenced 块、写 PVC、上传 Azure Blob |

三个 Agent 的角色提示词与共享的沙箱守卫规则，作为四份文件化的
**Agent Skills** 放在 [`skills/`](../skills/)：`hyperlight-sandbox`、
`search-bbc-worldcup`、`content-deepsearch`、`genscript-podcast`。
Agent 自身只携带十几行 stub 提示词，harness 的 `SkillsProvider`
把 skills 推给模型，由模型在真正需要时通过 `load_skill` 取正文。

### 边界（值得读两遍）

- 三个 Agent 模型**唯一**直接看到的工具就是 `execute_code`。
- 网络只能通过**一个**宿主桥 `fetch_url` 触达，而它**只能从
  guest 里**通过 `call_tool("fetch_url", url=...)` 调用。宿主端
  强制 HTTP GET、白名单（`www.bbc.com`、`bbc.com`）、正文剥 HTML、
  在剥之前预先把 BBC 体育文章 URL 放到 `LINKS:` 头里（让模型不必
  瞎猜 slug）、整体压缩到 ≤ 8 KB。
- 三个 Agent 共享**同一个** Hyperlight 沙箱，**每次 `execute_code`
  之前都还原到干净快照**。状态、全局变量、上轮的 `import` 都不会在
  Agent 之间或前后两轮之间泄漏。
- `save_scripts` 是确定性的、跑在沙箱**外面**的宿主侧 executor ——
  落盘路径上没有 LLM。

### 云原生形态

同一张图、同一个沙箱、同一个 save executor，被包成 AKS 上一个
**非特权、只读根文件系统** 的 CronJob：

- **Workload Identity → 用户分配托管标识（UAMI）。** Pod 内不放任何
  密钥。`DefaultAzureCredential` 通过 ServiceAccount 的联合 OIDC
  token 取到 UAMI 的 token，UAMI 上挂着 Foundry 范围的
  *Azure AI Developer* + *Cognitive Services User* 与存储账户范围的
  *Storage Blob Data Contributor*。
- **Hyperlight device plugin。** Pod 申请
  `hyperlight.dev/hypervisor: "1"`，节点上的 DaemonSet 通过 CDI
  把 `/dev/kvm` 注入到这个非特权容器里。**没有** `privileged: true`，
  也**没有** hostPath 挂载。
- **双下沉。** 每次运行先写 PVC（集群内缓存），再尽力上传到
  Azure Blob Storage 的 `<container>/<YYMMDD>/`（跨集群可持久化）。
  CronJob 本身无状态，Blob 才是真实数据源。
- **基于 PyPI 的镜像。** 单阶段 `python:3.12-slim` + `pip install
  hyperlight-sandbox[wasm,python-guest]==0.4.0`，通过 `az acr build`
  在 ACR 内编译，冷构建约 3 分钟 —— 不需要 Rust 工具链，也不需要
  本机 Docker daemon。

完整的本地架构图、云原生架构图、以及端到端的资源置备 runbook 见
[README.zh.md](../README.zh.md) 与
[Infra/README.md](../Infra/README.md)。

## 收尾

很长一段时间里，智能体里的"AI 安全"被理解成"通过 prompt
engineering 把模型劝得礼貌一点"。**MicroVM + Harness** 模式把这件事
从"措辞问题"重新框定为"**架构问题**"：假设模型会写出敌意代码，然后
让运行时**在物理上让它无所谓**。

Hyperlight 给了我们一条**便宜到每次工具调用都能跨**的内核级边界。
`hyperlight-sandbox` 把这条边界变成 `pip install` 一句话的距离。
Microsoft Agent Framework 的 harness 模式让我们能在 skills、
middleware 与一个 `execute_code` 工具里，**显式声明**每个能力位于
边界的哪一侧。一个真实的工作负载 —— 每日世界杯播客 —— 把这套模式
从开发者笔记本一路验证到 AKS 上的非特权 Pod。

模型会写代码，代码会跑。我们只是不再假装这件事可以靠"措辞"解决。

---

### 参考链接

- Hyperlight — <https://github.com/hyperlight-dev/hyperlight>
- `hyperlight-sandbox` — <https://github.com/hyperlight-dev/hyperlight-sandbox>
- Why MicroVMs（Docker 博客）— <https://www.docker.com/blog/why-microvms-the-architecture-behind-docker-sandboxes/>
- *Agent Harness in Agent Framework* — <https://devblogs.microsoft.com/agent-framework/agent-harness-in-agent-framework/>
- *Harness, Scaffold, and the AI Agent Terms Worth Getting Right* — <https://huggingface.co/blog/agent-glossary>
- Microsoft Agent Framework — <https://github.com/microsoft/agent-framework>

[hl]: https://github.com/hyperlight-dev/hyperlight
[hls]: https://github.com/hyperlight-dev/hyperlight-sandbox
[docker-microvm]: https://www.docker.com/blog/why-microvms-the-architecture-behind-docker-sandboxes/
[maf]: https://github.com/microsoft/agent-framework
[maf-harness]: https://devblogs.microsoft.com/agent-framework/agent-harness-in-agent-framework/
[maf-skills]: https://github.com/microsoft/agent-framework/tree/main/python/samples/02-agents/skills
[maf-workflows]: https://github.com/microsoft/agent-framework/tree/main/python/samples/03-workflows
[hf-glossary]: https://huggingface.co/blog/agent-glossary
