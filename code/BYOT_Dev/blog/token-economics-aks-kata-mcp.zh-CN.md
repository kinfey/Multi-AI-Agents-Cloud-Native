# 当每一个 Token 都开始计费：用 AI Runway + AKS + Kata MicroVM 在 AKS 上构建一座有成本意识、硬件级隔离的 MCP 智能体塔

> *一篇技术布道文，把 Token 经济学、模型混合部署、AI Runway、AKS Pod Sandboxing（Kata MicroVM）与 Model Context Protocol 串成一条线 —— 用本仓库 [`BYOT_Dev`](../README.md) 作为可运行的参考实现。*

---

## 1. 账单开始说话的那一刻

2024–2025 大部分时间里，「Agent」还只是 Demo 词。到了 2026 年，它已经是云账单上一行实打实的费用。

OpenAI、Anthropic、Google、Mistral、DeepSeek，乃至集群里你自己跑的开源权重推理栈，**全部按 Token 计费**：输入 Token、输出 Token、缓存 Token、推理 Token、工具调用 Token。单价一直在下降，但一个自主 Agent 一轮会话烧掉的 Token 数量却数量级地涨上来了。

我反复回看 [Enterprise Agent Workshop](https://github.com/kinfey/EnterpriseAgenticWorkshop) 第 02 课 *Token Economics and Cost Control* 的那份幻灯片，核心论点很简单：Agent 系统**不是**聊天应用。聊天应用一次用户输入对应一次模型调用；Agent 一次用户输入背后，往往是「规划一次、选工具一次、读工具输出一次、决定下一步一次、最终总结一次」，然后再循环。再叠加上工具内部自己调用模型、叠加上反思和重试，调用次数被自动放大。

账单已经不再是「这个模型每百万 Token 多少钱」的问题，而是「**我的架构本身**每个用户请求多少钱」的问题。

这篇博客讲的就是一个**有意为之**的架构 —— 它直接对这个问题给出答案，同时不放弃企业真正需要的安全属性。蓝图就是这个仓库 [`BYOT_Dev`](../README.md)：四个 SDLC 角色的 Agent 塔（Requirements → Code → Test → Deploy），跑在 AKS 上，每个 Agent 都装在自己的 Kata MicroVM 里，每个都通过 Model Context Protocol 把工具暴露给 GitHub Copilot Chat，四个 Agent 共用同一个由 **AI Runway** 在集群内提供的小模型 OpenAI 兼容端点。

---

## 2. 为什么 Agent 工作负载会让 Token 账单膨胀

三个力在叠加：

1. **自主性放大调用次数。** 用户在 IDE 里只敲了一句「帮我搭一个短链服务」——**1 次** Prompt。但当 4 个 Agent 把需求理清、把代码写出来、把测试方案给出、把 K8s 清单生成完，背后已经发生了 30–200 次模型调用，绝大部分用户根本看不到。
2. **推理在偷偷吃输出 Token。** 现代「reasoning model」在开口之前要先想，那段隐藏的 chain-of-thought **是计费的**。一段 5 行回答，可能背后已经付了 3000 个推理 Token 的钱。
3. **上下文持续膨胀。** 每次工具结果都会被注入下一次调用的上下文。一段 50 KB 的代码评审结论，就是下一轮重构的上下文。成本随对话深度**超线性增长**。

这不是 Prompt 工程能拯救的问题，唯一可持续的缓解方式是**架构层面**的，并且有三个明确的杠杆：

| 杠杆 | 具体含义 |
|------|----------|
| **模型分层** | 窄任务用小而便宜的模型；编排和判断才用前沿模型。 |
| **部署分层** | 把每个模型放到它最便宜的位置：超小模型在集群 CPU 上；中等模型在集群 GPU 上；前沿推理才走云 API。 |
| **协议分层** | 用 MCP 这样的开放标准，让昂贵的编排者可以把子任务交给便宜的工人，并且不被锁死。 |

本文要描述的架构，**三个杠杆同时拉**。

---

## 3. 心智模型：前沿模型当大脑，小模型当手脚

先看这张图：

```
   ┌─────────────────────────────────────────────────┐
   │  GitHub Copilot Chat (IDE)                      │  ← 前沿模型
   │  用户在这里输入，Copilot 规划                    │     **已经包含在用户的 Copilot 席位里**
   │  用 Copilot 自己的 Token 配额                    │     — 推理这一层没有第二块按 Token 计费的表
   └─────────────────────┬───────────────────────────┘
                         │ MCP（Streamable HTTP）
                         ▼
   ┌─────────────────────────────────────────────────┐
   │  AKS 上 4 个 Kata MicroVM Pod                   │  ← 小 / 中模型，跑在集群里
   │  byot-requirements                              │     （按算力计费，不再按 Token）
   │  byot-code                                      │
   │  byot-test                                      │
   │  byot-deploy                                    │
   └─────────────────────┬───────────────────────────┘
                         │ OpenAI 兼容 HTTP（集群内）
                         ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │   AI Runway — 一个 OpenAI 兼容的统一入口，                       │
   │   两个后端，都在同一个 AKS 集群里                                │
   │                                                                  │
   │   ┌──────────────────┐   ┌──────────────────────┐                │
   │   │ tiny-cpu         │   │ mid-gpu              │                │
   │   │ llama.cpp 1B–3B  │   │ vLLM 7B–14B          │                │
   │   │ D4s_v3 CPU 节点  │   │ AKS GPU 节点池        │                │
   │   │ 一直在跑，         │   │ Cluster Autoscaler   │                │
   │   │ 稳态打底         │   │ 0 → N 节点自动伸缩    │                │
   │   └──────────────────┘   └──────────────────────┘                │
   │                                                                  │
   │   ~85% 的 Agent 调用     ~15%（重构、跨多文件、               │
   │                                长上下文尖刺）                  │
   └──────────────────────────────────────────────────────────────────┘
```

这种排布的经济学：

- **前沿推理**（选哪个工具、什么顺序调用 Agent、读最终结果、判断够不够好）留在最上层 —— 那里 IDE 已经天然有一份计费关系（用户的 Copilot 席位）。这份席位**本身就含**一个前沿模型 Token 配额。你不是在推理这一层加一块新的按 Token 计费的表，而是在复用开发者本来就付钱的那一块。
- **批量产出**（把 REQ-007 展开成清单、根据需求生成 FastAPI 模块、写测试方案、生成 Deployment YAML）跑在**集群内的小模型**上。边际成本是 CPU 秒，不是 API Token。
- **更重的活**（跨多文件重构、长上下文推理、或者会在小 CPU 模型后面排队的任何尖刺）**通过同一个 AI Runway 入口、扩到同一个 AKS 集群上的 GPU 节点池**。没有外部端点，没有第二张按 Token 计费的账单：AI Runway 需要的时候 AKS 拉起一台 GPU 节点，用同一份 Kata 隔离跑更大的模型，流量走低再缩回零。
- **部署位置只是一个开关**。参考实现把 Llama-3.2-1B 跑在一台 `Standard_D4s_v3` CPU 节点上。同一份 Agent 代码，不需要重新编译，明天就可以指向一台 GPU 节点上的 7B 模型 —— 同一个集群、同一个 Kata RuntimeClass、同一套 OpenAI 兼容 URL 模式 —— 只改 ConfigMap 里一个 URL。

Token 经济学架构的精髓就是这两句话：**最便宜的那一次调用，就是你没有把它计到最贵那块表上的那一次** —— **而最重的那一次调用，就是你跑在「一分钟前还不存在、一分钟后也不会存在」的 AKS 算力上的那一次。**

---

## 4. AI Runway：把「放在哪里跑」抽象成一行 YAML

之所以一次 ConfigMap 编辑就能把工作负载在 CPU、GPU、云之间挪动，是因为 Agent **并不直接对接某个模型**，它只对接一个 **OpenAI 兼容 URL**。URL 背后是 [AI Runway](https://github.com/kaito-project/airunway) 的一个 `ModelDeployment` 自定义资源。

在 [`airunway/modeldeployment-qwen-cpu.yaml`](../airunway/modeldeployment-qwen-cpu.yaml)（仓库笔记也有记录），定义大致是：

```yaml
spec:
  image: ghcr.io/kaito-project/aikit/llama3.2:1b
  model: { id: "kaito/llama3.2-1b", source: huggingface }
  engine: { type: llamacpp }
  provider:
    name: kaito
    overrides:
      resource:
        instanceType: Standard_D4s_v3
        preferredNodes: ["aks-nodepool1-21523631-vmss000001"]
  nodeSelector: { agentpool: nodepool1 }
  resources: { cpu: "2", memory: "4Gi" }
  scaling: { replicas: 1 }
```

AI Runway 负责：

- 选 **engine**（CPU 用 `llamacpp`，GPU 用 `vllm` 或 `dynamo`）；
- 选 **provider**（目前 `kaito`，未来还会有更多）；
- 从 AIKit 目录拉取**模型镜像**；
- 暴露一个 OpenAI 兼容的 `Service`：`http://llama3-2-1b-cpu.airunway-models.svc:80/v1`。

> **关于「为什么这个例子只用 CPU」的重要说明。** 本仓库**特意**采用「CPU + Llama-3.2-1B」这套最便宜节点 SKU 的组合，是为了证明这套架构在最低成本节点上也跑得起来。但**生产环境绝不应该默认 CPU 总是对的**。正确做法是**按场景挑选**：
>
> | 场景 | 建议的部署位置 |
> |------|----------------|
> | 高并发、窄任务、对延迟不敏感（例如「把一条需求展开成 bullet」） | 集群内 CPU 小模型（1B–3B）—— 也就是本仓库演示的方案 |
> | 代码生成、重构、跨多文件推理 | 集群内 GPU 中模型（7B–14B），用 KAITO `vllm`，跑在一个从零自动伸缩的 AKS GPU 节点池上 |
> | 敏感企业数据、严禁离开集群 | 集群内 GPU，必要时叠加机密计算 |
> | 前沿推理、规划、对工具输出做判断 | **Copilot 席位里已经包含的前沿模型**，通过 MCP 谨慎调用 —— 而不是另一个要单独搭建、按 Token 计费的云端端点 |
>
> AI Runway 把这件事变成「改 YAML」而不是「重构代码」。这个抽象的真正价值是**保留选择权** —— 让你每个季度都可以重新审视 Token 经济学，并且不用重写任何 Agent。

---

## 5. 混合扩容：所有推理都在 AKS 上，规划用你已经付费的 Copilot Token

企业现阶段在 Token 经济学上犯的最大错误，就是把「模型跑在哪里」当成二选一 —— 「全部进集群」或「全部走按 Token 计费的云 API」。真实的负载两个都不是。**真正能省钱的模式有两个组成部分，而且两个都已经在你的账单上了**：

1. **你本来就开好的 AKS。** 一个小 CPU 节点池跑稳态工作负载，加上一个在小池扛不住时**从零**开始扩的 GPU 节点池。同一个集群、同一份 Kata 隔离、同一行账单条目。
2. **开发者已经付钱的 Copilot 席位。** Copilot Chat 的前沿模型自带一个 Token 配额，就在席位里。用**那一份**配额 —— 不是另一个单独搭建的云端推理端点 —— 来做驱动 AKS 廉价工人的规划，通过 MCP 去触发。

这就是「混合」的全部。没有外部 Foundry 端点，没有第二块按 Token 计费的推理表。只有按需扩容的 AKS 算力 + 一颗你本来就付了钱的前沿大脑。

Agent 流量的大致分布是：

- **~85% 的 Agent 调用**是短、窄、可预测的 —— *「把这条需求展开」*、*「格式化这段 YAML」*、*「总结这段 diff」*。一个 1B–3B 模型在 CPU 节点上几秒就能返结果，账单计的是节点费，不是 Token。
- **~15%** 是更重的活 —— 跨多文件重构、长上下文推理、那种 400 行 FastAPI 生成。它们需要一个 7B–14B 模型跑在 GPU 上。
- **所有这些上面的规划与判断**都由 Copilot 席位的前沿模型完成 —— 不管你建不建 BYOT，用户都已经在为它付费。

### 杠杆 A — 一个跟原集群同一个 AKS 的 GPU 节点池，从 0 弹性伸缩到 N

保留一直在跑的 `tiny-cpu` ModelDeployment 作为稳态打底。加第二个 AI Runway `ModelDeployment`，让中型模型跑在一个**创建时就是 0 节点**、交给 AKS Cluster Autoscaler（或 Karpenter）管理的 GPU 节点池：

```bash
az aks nodepool add \
  --cluster-name $CLUSTER --resource-group $RG \
  --name gpupool \
  --node-vm-size Standard_NC24ads_A100_v4 \
  --node-count 0 --min-count 0 --max-count 4 \
  --enable-cluster-autoscaler \
  --node-taints sku=gpu:NoSchedule \
  --workload-runtime KataVmIsolation
```

```yaml
# airunway/modeldeployment-mid-gpu.yaml（示意）
spec:
  image: ghcr.io/kaito-project/aikit/qwen2.5:7b
  engine: { type: vllm }
  provider:
    name: kaito
    overrides:
      resource:
        instanceType: Standard_NC24ads_A100_v4
  nodeSelector: { agentpool: gpupool }
  tolerations: [{ key: sku, operator: Equal, value: gpu, effect: NoSchedule }]
  resources: { cpu: "4", memory: "32Gi", nvidia.com/gpu: "1" }
  scaling: { replicas: 0, maxReplicas: 4 }
```

关键技巧是 `replicas: 0` 与节点池的 `min-count 0` 捆在一起。**没人问中型模型问题的时候，没有 GPU 节点在跑、也没有 GPU 节点计费。** 第一个请求进来，AI Runway 把副本拉到 1，触发 Cluster Autoscaler 拉起一台 GPU 节点，Kata Pod Sandboxing 依然生效。流量走低后，副本和节点一起回到 0。整个过程都发生在 AKS *内部* —— Agent 从不离开集群去找 GPU。

### 杠杆 B — 复用 Copilot 席位里已经付费的前沿 Token

这一条是大多数 Token 成本分析最容易漏掉的杠杆。**每个用 BYOT 的开发者本来就有一个 Copilot 席位。** 这个席位带着一份前沿模型 Token 配额，用户一在对话框里打字 Copilot Chat 就在消耗它。[`docs/workflow.md`](../docs/workflow.md) 里的编排循环 —— *「规划下一步调哪个工具、读工具的输出、总结结果」* —— 是从**那一份**配额里付的，不是从你新搭的某个推理端点里付的。

这意味着：

- 你**不需要**为「那颗聪明的模型」单独建一个云端 OpenAI / Foundry 部署。聪明的模型已经在用户屏幕上了。
- 你**不需要**在 Agent 调前沿模型这条路径上加一块按 Token 计费的表。前沿模型在你的 Agent 上游 —— 是它通过 MCP 调用 *你的* Agent，不是反过来。
- 整套架构唯一引入的按 Token 计费的支出，是 Copilot 自己在席位里计的那一份 —— 而这份开销跟你架了多少个 BYOT Agent 无关。

净效果是：负载里那些**每 Token 很贵**的部分（规划、判断）用的是公司本来就在买的 Token；那些**算力很便宜**的部分（长文本生成）用的是你本来就在按节点工时付费的 AKS 算力。

### Agent 怎么在两个 AKS 后端之间选择

[`agents/app/airunway_client.py`](../agents/app/airunway_client.py) 里的 Agent Framework 客户端从 ConfigMap 读 `base_url` 和 `model`。三种逐步增强的策略：

1. **按角色静态绑定。** `byot-requirements` 和 `byot-test`（便宜、窄任务）绑 `AIRUNWAY_BASE_URL=tiny-cpu`；`byot-code` 和 `byot-deploy`（生成量大）绑 `AIRUNWAY_BASE_URL=mid-gpu`。一份 ConfigMap，一次发布。
2. **在工具里「先试 tiny，不行再升级」。** 每个工具先调 `tiny-cpu`；如果输出太短、质量检查不过、或者超时，就改去 `mid-gpu` 重试。小模型接下轻松的 85%，GPU 池只干那 15% 真需要的重活。
3. **前面架一个 AI Gateway。** 把 [Azure API Management 作为 AI Gateway](https://learn.microsoft.com/azure/api-management/genai-gateway-capabilities) 架在两个 AI Runway Service 前面。Agent 只看到一个 URL；网关帮你做语义缓存、Token 预算、并根据负载动态在 `tiny-cpu` 与 `mid-gpu` 之间路由。两个后端始终都在你的 AKS 里 —— 网关只做路由。

### 一个保守估算的 Token 节省量

假设一次经过 BYOT 塔的 Copilot Chat 会话向下层 Agent 发出 **30 次模型调用**。如果这 30 次全部走一个外部前沿 API，比如 $5/百万输出 Token、平均每次 2K 输出，那是每会话 **约 $0.30** 的*额外*下层模型支出 —— 还要叠在 Copilot Chat 本来就为席位里的规划计的费用上。

用「AKS + 席位 Token」的混合模式：

- 25–26 次（~85%）→ `tiny-cpu`，跑在本来就为常驻 Agent 在跑的 CPU 节点上 → **边际费用 ≈ $0**
- 4–5 次（~15%）→ `mid-gpu`，只在 AI Runway 把 GPU 节点拉起来的那段时间计费，并被每一个同时落在同一台 GPU 节点上的 BYOT 用户分摊 → **约 $0.02–0.05**
- 规划 / 判断 → 走开发者已经付费的 Copilot 席位配额 → **额外 $0**

原本会花每会话 $0.30 的按 Token 模式，塌缩到 **约 $0.02–0.05 的纯 AKS 算力**，GPU 账单在没人问难问题的时候回到零。这就是那个杠杆。能走通的原因是：**AI Runway 给 Agent 一个集群内的统一入口**，AKS 给集群一份「闲着不付费」的弹性 GPU 算力，而 Copilot Chat 自带一颗预付费的前沿大脑。

---

## 6. Kata MicroVM：给 Agent 生成的代码戴上「硬件级头盔」

Token 经济只是 Agent 工作负载问题的一半，另一半是**你把 Agent 装在什么样的盒子里**。

今年早些时候我在 Microsoft Tech Community 发过 [*Giving the Copilot SDK Agent a "hardware-level helmet" using Kata microVM on AKS*](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/giving-the-copilot-sdk-agent-a-hardware-level-helmet-using-kata-microvm-on-aks/4518668)。论点浓缩成一段话：

> 传统容器是一栋楼里有自己墙、但**共享屋顶**的公寓 —— 共享的就是宿主机的 Linux 内核。对人写的服务来说，租客可预测，可以接受。但对一个 Agent 来说，**租客就是模型本身**，它在运行时自己决定要不要敲 shell、要不要读文件、要不要 `npx` 拉一个第三方包。这是一种新的威胁模型。容器的 namespace 不是为它设计的。你需要**每个 Pod 一个独立的 guest kernel** —— 也就是 microVM。

[Kata Containers](https://katacontainers.io/) 是把 microVM 接入 Kubernetes 的那层胶水。AKS 直接把它打包成 **Pod Sandboxing**，底层 Hypervisor 是 Hyper-V，RuntimeClass 叫 `kata-vm-isolation` —— 只要节点池用 `--workload-runtime KataVmIsolation` 创建出来，AKS 会自动把这一切准备好。

在 [`BYOT_Dev`](../README.md) 里，每个 Agent Pod 都明确指定：

```yaml
spec:
  runtimeClassName: kata-vm-isolation
  containers:
    - name: agent
      securityContext:
        runAsNonRoot: true
        readOnlyRootFilesystem: true
        capabilities: { drop: ["ALL"] }
        seccompProfile: { type: RuntimeDefault }
```

剩下的 AKS 自己搞定 —— 在容器启动**之前**先拉起一台真正的 Hyper-V microVM，带自己的 guest kernel。验证只要一条命令：

```bash
kubectl -n agents exec deploy/byot-requirements -- uname -r
# 和宿主机节点 kernel 一比，版本不同 → microVM 生效
```

仓库还通过 `podAntiAffinity`（`topologyKey: kubernetes.io/hostname`）把**每个 Agent 钉在不同节点上**，于是四个 Agent 物理上落在四台不同的 Kata 宿主机上 —— 一个 Agent 即便发生逃逸，也无法通过「共享的宿主内核」碰到另一个 Agent，**因为根本不存在共享宿主内核**。

它跟 Token 经济的关系是这样的：一旦你开始信任「集群里的便宜小模型」去跑触碰真实客户代码的 Agent loop，安全的封装就必须比普通容器**更强**，而不是更弱。Kata 是让「便宜」和「安全」可以同时成立的那块拼图。而因为 AKS Pod Sandboxing 在 CPU 池、GPU 池、以及你将来为突发加的任何新节点池上应用方式都一样，上面的混合部署问题**不会削弱隔离能力** —— 每一个 Pod、每一层都仍然启动自己的 guest kernel。

---

## 7. MCP：GitHub Copilot Chat 怎么真正驱动这座 Agent 塔

最后一块是协议。Kata MicroVM 里的 Agent 跑得再好，没有东西能调用它们也是白搭。而用户已经打开着的那个东西，就是 **VS Code 里的 GitHub Copilot Chat**。

[Model Context Protocol](https://modelcontextprotocol.io) 是 Copilot Chat（以及几乎所有正经的 Agent IDE）和远端工具服务器之间说的标准协议。仓库里每个角色都用 `FastMCP` 在 Streamable HTTP 上把工具暴露出去 —— 参见 [`agents/app/main.py`](../agents/app/main.py) 和 [`agents/app/roles/`](../agents/app/roles/) 里每个角色的工具集。

Service 暴露方式上有一个看似小、但很重要的决定：仓库的 [`k8s/services.yaml`](../k8s/services.yaml) 把每个角色的 Service 都设成 `type: LoadBalancer`，原因是：

- `kubectl port-forward` **对 Kata Pod 不工作** —— 监听端口活在 microVM 里，而不是宿主 sandbox 的 netns 里；
- `kubectl proxy` 可以工作，但要 Copilot 始终连 `localhost`，还要本地常驻一个进程；
- LoadBalancer 给每个 Agent 一个公网 Azure IP，IDE 直接打过去就行。

把这 4 个 LoadBalancer IP 写进 [`.vscode/mcp.json`](../README.md)，然后打开 Copilot Chat 的 Agent 模式，Copilot 就能看到四个 MCP 服务器 —— `byot-requirements`、`byot-code`、`byot-test`、`byot-deploy` —— 用户只要说：

> *「用 byot 塔把这个想法 —— 一个带点击分析的短链服务 —— 从需求一路走到部署。」*

幕后会发生这些事（[`docs/workflow.md`](../docs/workflow.md)）：

1. Copilot 的前沿模型**规划**整个序列。*花的前沿 Token：少，但聪明。*
2. 通过 MCP 调用 `byot-requirements.gather_requirements({"idea": "URL shortener…"})`。*这一步**不花前沿 Token**，集群里的 Llama-3.2-1B 在干活。*
3. 调用 `byot-code.implement_from_requirements({...})`。*同上，集群小模型。*
4. 调用 `byot-test.generate_test_plan({...})`。*同上。*
5. 调用 `byot-deploy.generate_k8s_manifest({...})`。*同上。*
6. Copilot 的前沿模型**读完**四份结果，给用户一个连贯的总结。*再花一点前沿 Token。*

最贵的模型只**决策**了 5 次「下一步做什么」，便宜的模型做了 4 次真正的长文本生成。这就是 Token 经济学的胜利 —— 而它之所以能在「不被锁定」的前提下成立，正因为 MCP 是一个开放标准。

---

## 8. 把架构图当作一张预算表来读

把上面那张图翻译成一张单位成本表 —— 现在把混合分层写明白：

| 层 | 成本去了哪里 | 由什么控制 |
|----|-------------|------------|
| 用户输入 + IDE 规划 | Copilot 席位（按人订阅） | 已是固定费用 |
| **前沿编排 Token** | **Copilot 席位 Token 配额 —— 已包含、用于 MCP 规划、无单独端点** | Copilot 决定要跑几轮 Agent |
| 工具调用流量 | Azure LoadBalancer 出口流量 | 在这个量级近乎为零 |
| **`tiny-cpu` 推理（稳态～85%）** | AKS CPU 节点工时（1× `D4s_v3`） | 副本数、模型大小、batch |
| **`mid-gpu` 推理（自动伸缩～15%）** | **同一个集群上**的 AKS GPU 节点工时，**仅在副本 > 0 期间计费** | Cluster Autoscaler / Karpenter `min=0 max=N`、缩到零 |
| 硬件隔离 | AKS Pod Sandboxing（Kata）—— 同一份节点工时 | 你有没有把它打开（你应该打开） |
| 切换 Provider | 一行 AI Runway YAML | 一次 `kubectl apply` |

有三点很值得注意。

第一，**绝大部分的每请求可变成本，已经从 Token 表挪到了节点表上**。CPU 工时更可预测、更容易做内部分摊、也更容易设上限。你知道自己付了多少个 `D4s_v3` 核心，但你**不可能事先知道**前沿模型会觉得「这个问题需要多少个 Token」。

第二，**GPU 容量不再是一笔固定押注，而且它从不离开 AKS**。在 AI Runway 不需要之前，GPU 节点池一直是 0 节点；需要的时候，它在**同一个集群里**按同一份 Kata RuntimeClass 扩容 —— 没有第二个 region、没有第二个租户、没有第二张按 Token 计费的账单。

第三，**前沿大脑是复用的，不是重买**。驱动整座塔的规划与判断，跑的是开发者本来就在付费的 Copilot 席位 Token 配额。BYOT 不为「那颗聪明的模型」单独搭建任何云端端点，所以**也没有第二块按 Token 计费的表**要看护。

而因为 AKS Pod Sandboxing 是 AKS 自带能力、在 CPU 池和 GPU 池上打开方式一样，**算力成本之上的安全成本是零**。

这就是这套架构能**同时**做到「成本意识」、「弹性伸缩」与「安全意识」的原因 —— 这三件事过去是 trade-off，现在不是了。

---

## 9. 六条命令，从零到 Demo

仓库的 [`README.md`](../README.md) 里完整的运行顺序：

```bash
# 0. 一次性前置：az login、kubectl、helm、docker、aks-preview
az login

# 1. 创建带 Kata + ACR + AzureLinux 的 AKS
bash infra/01-create-aks-kata.sh

# 2. 安装 AI Runway controller + KAITO provider（固定到 v0.5.0）
bash infra/02-install-airunway.sh

# 3. 通过 AI Runway ModelDeployment 在 CPU 上部署 Llama-3.2-1B
bash infra/03-deploy-qwen.sh

# 4. 构建并推送单个 agent 镜像到 ACR
bash infra/04-build-push-agents.sh

# 5. 部署 4 个 Kata 隔离的 MCP Agent
bash infra/05-deploy-agents.sh

# 6. 输出公网 MCP 端点，喂给 GitHub Copilot
bash infra/06-show-mcp-endpoints.sh
```

把第 6 步打印出来的 IP 填进 [`.vscode/mcp.json`](../README.md)，打开 Copilot Chat，你就拥有了一个完整可用、硬件级隔离、成本可控的 Agent 塔 —— 上面跑的是用户已经付过钱的前沿模型，下面跑的是 CPU 节点上的小模型。等流量起来，随时可以在旁边加上一个 `mid-gpu` ModelDeployment，跑在一个从零自动伸缩的 AKS GPU 节点池上；Agent 和 Copilot 集成都不需要变，而且全部东西都仍然在 AKS 里。

---

## 10. 收尾：把这条线再走一遍

让我再把整条线串一次：

1. **Token 经济学是新的 SLO。** Agent 工作负载会自动放大模型调用；每一次调用都有价格。是**架构**而不是 Prompt 在弯曲这条曲线。
2. **模型分层 + 部署分层。** 顶层留给前沿推理；批量生成交给小模型；集群 CPU 扫稳态、集群 GPU 接那重活的 15%。
3. **把 AKS 算力和你已经付费的 Copilot Token 混着用。** 不要再加第二个按 Token 计费的云端推理端点。重算力该跑在一个从零伸缩的 AKS GPU 池上；规划该跑在开发者本来就有的 Copilot 席位配额上。这个组合同时能**省 Token**（不多一张按 Token 计费的表）和**弹性扩容**（集群只在 AI Runway 开口要的时候才长大）。
4. **AI Runway 让「放在哪里跑」变成改 YAML。** 今天的 CPU Llama，就是同一个集群上明天的 GPU Qwen。同一份 Agent 代码。
5. **Kata MicroVM 对 Agent 不是可选项。** 租客就是模型，屋顶必须是你自己的。AKS Pod Sandboxing 让它变成开箱即用 —— 而且它在 CPU 池和 GPU 池上一样生效。
6. **MCP 是桥。** GitHub Copilot Chat 本身就是 MCP 客户端，把集群里的廉价工人暴露成 MCP 工具，前沿大脑就能用**席位里已经付过费的 Token**去调用它们，而不是额外的 Token。
7. **参考实现就在这个仓库。** 六条命令、四个 Agent、一个最小的 CPU 模型、完整的 microVM 隔离、真的能接到 Copilot Chat —— 而且随时可以在不动 Agent、也不走出 AKS 的前提下叠上混合扩容能力。

在 Agent 时代，容器不只是装应用的盒子，它装的是**不确定性，还有 Token**。microVM 让盒子更硬，AI Runway 让你能随时把模型在同一个集群的 CPU 与 GPU 之间滑进滑出而不重写 Agent，MCP 让用户那台昂贵 IDE 从外部驱动这个便宜盒子 —— 而且驱动它用的是 Copilot 席位账单上本来就在计的 Token，不是新的 Token。这就是整条线。

把塔搭起来。看着账单变好看。

---

### 延伸阅读

- *Token 经济学与成本控制* —— [Enterprise Agent Workshop](https://github.com/kinfey/EnterpriseAgenticWorkshop/blob/main/ppt/pdf/en/02.token-economics-cost-control.pdf) 第 02 课幻灯片。
- [Giving the Copilot SDK Agent a "hardware-level helmet" using Kata microVM on AKS](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/giving-the-copilot-sdk-agent-a-hardware-level-helmet-using-kata-microvm-on-aks/4518668)。
- [AI Runway](https://github.com/kaito-project/airunway) 与 [KAITO](https://github.com/kaito-project/kaito)。
- [Kata Containers](https://katacontainers.io/) · [AKS Pod Sandboxing](https://learn.microsoft.com/azure/aks/use-pod-sandboxing)。
- [Model Context Protocol](https://modelcontextprotocol.io) · [VS Code Copilot Chat MCP 支持](https://code.visualstudio.com/docs/copilot/mcp)。
- 本参考实现：[`BYOT_Dev`](../README.md) · 架构图见 [`docs/architecture.md`](../docs/architecture.md) · 端到端流程见 [`docs/workflow.md`](../docs/workflow.md)。
