# 用 AKS 上的 Kata microVM,为 Copilot SDK Agent 戴上一顶"硬件级头盔"

*一位开发者布道师关于 microVM、Kata Containers,以及在 AKS 上为 AI Agent 加固的实战笔记*

> 示例代码:[Multi-AI-Agents-Cloud-Native / AKS_MicroVM](https://github.com/kinfey/Multi-AI-Agents-Cloud-Native/tree/main/code/AKS_MicroVM)

---

## 让我停下来思考的一个瞬间

最近我在用 **GitHub Copilot SDK** 构建一个 Agent 服务。跑起来之后,我回头翻执行日志,有件事一下子抓住了我的注意力:

> 在一次对话回合中,Agent 自己执行了一条 shell 命令、读取了几个文件、还通过 `npx` 从 npm 拉取了一个第三方 MCP server —— 全都是它自己决定做的。

这些我一行都没硬编码。是模型在运行时自己决定要执行哪些命令、读哪些文件、装哪个包。

那一刻我意识到:**这个容器里运行的相当一部分代码,是模型现写的,而不是我写的。**

这跟传统 Web 服务有本质区别。传统应用里每一行代码都是人写的,经过 review 和测试才会上线。但 AI Agent 不一样——它的部分行为是运行时生成的。你根本不知道它接下来要执行什么。

**所以问题来了:我们给它装的这个容器,真的够结实吗?**

## 容器隔离到底是怎么工作的(以及它不够用的地方)

打个比方。

把传统容器想象成**一栋楼里的一间公寓**。每间公寓都有自己的墙——namespace 和 cgroup 把它们隔开。从屋里看,确实像有自己的小天地。

**但每一间公寓,共用的是同一个屋顶——也就是宿主机的 Linux 内核。**

大多数时候这没问题。可一旦有人在屋顶上发现了一道裂缝——某个内核漏洞——他就能从自己的公寓爬上屋顶,走过去,然后掉进**这栋楼里任何一间公寓**。这就是容器逃逸。

对普通 Web 服务来说,这个风险还能接受——容器里跑的代码是可预测的。但 AI Agent 不一样。**容器里运行的代码本质上就是不可预测的**——你担心的不是外面来的攻击者,是住户自己。

Docker 在 [*Comparing Sandboxing Approaches for AI Agents*](https://www.docker.com/blog/comparing-sandboxing-approaches-ai-agents/) 中讲得很清楚:

> **AI Agent 是一类天然需要更强沙箱的工作负载。传统容器的共享内核模型不够用。**

那什么才够用?

## 认识 microVM:给每间公寓配一个独立屋顶

继续用公寓的比喻——既然问题是共用屋顶,那解决方法就很直接:**给每间公寓配一个自己的屋顶。**

你还是住在公寓里(容器);整栋楼的管理方式也没变(Kubernetes)。但你头顶上的那块天花板现在只属于你。哪怕你把它砸穿,你也只是穿到自己的屋顶,而不是邻居的。

**这就是 microVM 的核心思想。**

Koyeb 写过一篇很好的科普 [*What Is a microVM*](https://dev.to/koyeb/what-is-a-microvm-1a2b)。要点是:

1. **它是一个虚拟机** —— 有自己独立的 guest kernel,完全和宿主机内核隔离。安全性就来自这里。
2. **但是一台精简过的虚拟机** —— 只保留最基本的:CPU、内存、网络、块存储。没有 USB 控制器、没有声卡、没有 GPU 直通。
3. **所以它又快又轻** —— 毫秒级启动、内存占用小、体验上接近容器。

一句话总结:**microVM = 虚拟机级别的隔离 + 接近容器级别的轻量。**

## Kubernetes 怎么用 microVM?Kata Containers 登场

知道 microVM 好是一回事——但 Kubernetes 调度的是 Pod 和容器,不是虚拟机。这两个世界怎么对接?

这正是 [Kata Containers](https://katacontainers.io/) 在做的事。它的 slogan 一针见血:

> **"容器的速度,虚拟机的安全。"**

Kata 充当 Kubernetes 和 microVM 之间的翻译层:

- **从 Kubernetes 看**,它就是一个标准 Pod —— 调度、管理、监控都正常。
- **底层实际上**,这个 Pod 是跑在一台带独立内核的轻量虚拟机里。

你不用改应用代码、不用改 CI/CD,只要告诉 Kubernetes:"用 Kata 的 RuntimeClass 来跑这个 Pod。"剩下的 Kata 全部搞定。

**在 AKS 上,微软把 Kata 开箱即用地集成了进来**,起了个名字叫 **Pod Sandboxing**。底层 hypervisor 是 **Microsoft Hyper-V**(不是 QEMU),RuntimeClass 名字叫 `kata-vm-isolation`。你创建一个特殊的节点池,AKS 就会自动配好一切。

## 看一个真实的例子

理论说够了——下面带你看个具体的。我做了一个示例叫 [`AKS_MicroVM`](https://github.com/kinfey/Multi-AI-Agents-Cloud-Native/tree/main/code/AKS_MicroVM),只做一件事:

**在 AKS 上跑一个 GitHub Copilot SDK Agent 服务,强制它运行在 `kata-vm-isolation` —— 一个 microVM 沙箱里。**

架构是这样的:

```
HTTPS 请求进来
  └─ AKS 节点池(已开启 KataVmIsolation)
      └─ Pod (runtimeClassName: kata-vm-isolation)
          └─ 专属 Hyper-V microVM
              └─ FastAPI 服务 (Python / uvicorn)
                  └─ GitHubCopilotAgent
                      └─ Copilot CLI (Node.js)
                          └─ MCP servers / tools
              独立 guest kernel + seccomp + cgroup
          NetworkPolicy 限制出站流量
```

**从外面看,它就是一个普通的 AKS Pod。从里面看,应用跑在自己的微型虚拟机里,带着自己专属的内核。**

### 项目结构

整个示例就这些文件:

```
app/                       ← Agent 服务 (Python)
  main.py                  ← FastAPI 接口
  agent.py                 ← Copilot Agent 封装
  tools.py                 ← 示例 function tools
  requirements.txt

Dockerfile                 ← Python 3.12 + Node 20 + Copilot CLI

k8s/                       ← Kubernetes 清单
  namespace.yaml
  runtimeclass.yaml        ← 参考用(AKS 会自动创建)
  secret.example.yaml      ← Token 占位符
  deployment.yaml          ← 关键文件:强制 kata-vm-isolation
  service.yaml
  networkpolicy.yaml       ← 限制入站/出站流量

infra/                     ← 基础设施脚本
  01-create-aks.sh         ← 创建集群
  02-build-push.sh         ← 构建镜像并推送到 ACR
  03-deploy.sh             ← 一键部署
```

**三个 shell 脚本搭基础设施,六个 YAML 文件部署服务。** 就这些。

### 不仅仅是 microVM:五层防御

我想强调一点:**这个示例不是简单加个 microVM 就完事**,它叠加了五层防护:

| 你担心的问题 | 这一层怎么应对 |
| --- | --- |
| 恶意代码逃出容器 | `kata-vm-isolation` → 专属 microVM,带独立内核 |
| 容器内提权 | `runAsNonRoot` + drop ALL caps + 只读文件系统 + seccomp |
| Agent 偷偷往外发请求 | NetworkPolicy 白名单 —— 只允许 Copilot/GitHub/MCP 出站 |
| Token 泄露 | K8s Secret 注入(可升级为 CSI + Key Vault) |
| 模型让 Agent 干危险的事 | `on_permission_request` 默认拒绝;只放行白名单内的操作 |

**microVM 是最外层的墙——硬件级隔离。但墙里面,还有保安、门禁、监控。一个都不能少。**

### 六步部署

```bash
# ① 创建支持 Kata 的 AKS 集群
bash infra/01-create-aks.sh

# ② 验证 RuntimeClass 就绪
kubectl get runtimeclass kata-vm-isolation

# ③ 构建镜像并推送到 ACR(脚本会自动发现你的 ACR)
bash infra/02-build-push.sh

# ④ 填入 GitHub Copilot token
#    编辑 k8s/secret.example.yaml → 改名为 secret.yaml(别提交!)

# ⑤ 部署
bash infra/03-deploy.sh

# ⑥ 通过 API server proxy 访问
kubectl proxy --port=8001 &
```

然后跟 Agent 聊天:

```bash
curl -s -X POST \
  http://localhost:8001/api/v1/namespaces/copilot-agent/services/copilot-agent:80/proxy/chat \
  -H 'content-type: application/json' \
  -d '{"message":"Briefly introduce Kata Containers."}'
```

要流式输出?用 stream 接口:

```bash
curl -N -X POST \
  http://localhost:8001/api/v1/namespaces/copilot-agent/services/copilot-agent:80/proxy/chat/stream \
  -H 'content-type: application/json' \
  -d '{"message":"List 3 Linux kernel hardening tips","stream":true}'
```

### 怎么验证它真的跑在 microVM 里?

一条命令:

```bash
kubectl -n copilot-agent exec deploy/copilot-agent -- uname -r
```

**如果内核版本和节点的内核不一样——你的 Pod 就在自己的 guest kernel 里跑,而不是和宿主机共用。** 验证完毕。

### 我踩过的坑,你不用再踩

**`kubectl port-forward` 对 Kata Pod 不好使。** 这是最容易掉的坑。应用监听在 microVM 里,但 `port-forward` 连的是宿主机上空的 sandbox netns —— 你只会得到 `connection refused`。改用 `kubectl proxy`。

**Token 环境变量名要对。** Copilot CLI 认的是 `GH_TOKEN` 或 `GITHUB_TOKEN`,不是自定义名字。Deployment 已经从同一个 Secret 注入了这两个变量。

**只读文件系统需要 emptyDir 挂载。** 容器开了 `readOnlyRootFilesystem: true`,但 Copilot CLI 启动时要往 `/home/agent/.cache` 写东西。Deployment 在 `.cache`、`.copilot`、`/tmp` 都挂了 `emptyDir` —— 漏一个 CLI 就起不来。

**`on_permission_request` 务必保持默认拒绝。** Agent 的工具调用都要过这道权限门,默认拒绝、白名单放行。生产环境**千万别**改成全部放行。

## 收个尾:把整条逻辑串起来

把整条思路再走一遍:

> **① 场景**:AI Agent 天然要在容器里运行模型生成的、不可信的代码
> **② 问题**:传统容器共享宿主机内核 —— 一次逃逸就能拿下整个节点
> **③ 洞察**:仅靠 namespace 不够,需要硬件级别的隔离
> **④ 方案**:microVM —— 给每个 Pod 一个独立 guest kernel
> **⑤ 整合**:Kata Containers 把 microVM 原生带进 Kubernetes;AKS Pod Sandboxing 让它开箱即用
> **⑥ 实践**:`AKS_MicroVM` 示例 —— 六步部署,五层防御

在 AI Agent 的时代,**容器不只是装应用的盒子,更是装"不确定性"的盒子。** 它需要更结实的外壳。microVM 就是那层外壳。

> 完整源码:<https://github.com/kinfey/Multi-AI-Agents-Cloud-Native/tree/main/code/AKS_MicroVM>
>
> 延伸阅读:
> - [What is a microVM? — Koyeb](https://dev.to/koyeb/what-is-a-microvm-1a2b)
> - [Comparing Sandboxing Approaches for AI Agents — Docker](https://www.docker.com/blog/comparing-sandboxing-approaches-ai-agents/)
> - [Kata Containers](https://katacontainers.io/)
