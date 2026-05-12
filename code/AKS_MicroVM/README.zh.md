# Kata microVM 隔離的 GitHub Copilot SDK Agent on AKS

> 在 AKS 上以 **Kata Containers microVM 隔離(`kata-vm-isolation`)** 執行
> 基於 **Microsoft Agent Framework + GitHub Copilot SDK** 的 Agent 服務,
> 緩解容器逃逸 (container escape) 對共用節點與其他工作負載的衝擊。

## 為什麼要用 Kata microVM 跑 Copilot SDK Agent?

GitHub Copilot SDK Agent 在執行時會:

- 啟動 `copilot` CLI 子行程(Node.js)
- 經過 `on_permission_request` 後可執行 **shell 命令、檔案讀寫、URL 抓取、MCP server**
- 載入第三方 MCP server(`npx @modelcontextprotocol/server-*`),可能拉取任意 npm 套件

也就是說 Agent 容器內 **天生會跑不可信、模型生成的程式碼**,典型的高風險工作負載 ——
這正是 [Hardening AI/agent CLI on AKS with Kata microVM Isolation] 場景的標準目標。

在傳統 `runc` 容器中,共用宿主 Linux kernel,kernel 0-day 或錯誤的 capability/掛載
可導致容器逃逸並影響整個節點及同節點其他 Pod。

**Kata microVM 隔離**將每個 Pod 放進獨立的 Microsoft Hyper-V (mshv) 輕量虛擬機,
擁有獨立的 guest kernel,容器逃逸只到一次性的 microVM,不會逃到節點 host kernel。
參考 Kata Containers 專案:<https://github.com/kata-containers>。

## 解決方案架構

```
                ┌──────────────────────────────────────────────────────────┐
                │                AKS Cluster (Azure Linux)                  │
                │                                                            │
                │  ┌────────────────────────────────────────────────────┐  │
   HTTPS        │  │ Node Pool: workload-runtime = KataVmIsolation       │  │
  ───────────►  │  │                                                      │  │
                │  │  ┌─────────────────────────────────────────────┐    │  │
                │  │  │ Pod (runtimeClassName:                       │    │  │
                │  │  │      kata-vm-isolation)                      │    │  │
                │  │  │ ┌──────────────────────────────────────────┐ │    │  │
                │  │  │ │  Microsoft Hyper-V microVM (Cloud HV)     │ │    │  │
                │  │  │ │  ┌────────────────────────────────────┐  │ │    │  │
                │  │  │ │  │ FastAPI (uvicorn)                  │  │ │    │  │
                │  │  │ │  │  └─ GitHubCopilotAgent              │  │ │    │  │
                │  │  │ │  │      └─ Copilot CLI (Node.js)        │  │ │    │  │
                │  │  │ │  │         └─ MCP servers / tools      │  │ │    │  │
                │  │  │ │  └────────────────────────────────────┘  │ │    │  │
                │  │  │ │  独立 guest kernel + seccomp + cgroup    │ │    │  │
                │  │  │ └──────────────────────────────────────────┘ │    │  │
                │  │  │  Egress 受 NetworkPolicy 限制                │    │  │
                │  │  └─────────────────────────────────────────────┘    │  │
                │  └────────────────────────────────────────────────────┘  │
                └──────────────────────────────────────────────────────────┘
```

關鍵防護層:

| 層級 | 防護 |
| --- | --- |
| Pod sandbox | `runtimeClassName: kata-vm-isolation` → microVM + 獨立 guest kernel |
| Container | `runAsNonRoot`, `readOnlyRootFilesystem`, drop ALL caps, `seccompProfile: RuntimeDefault` |
| 網路 | `NetworkPolicy`: 只放行 Copilot/GitHub/MCP 必要 egress |
| Secrets | `GH_COPILOT_TOKEN` 透過 K8s Secret(可改用 CSI + Key Vault) |
| Agent 工具 | `on_permission_request` 預設拒絕,白名單才放行 |

## 檔案結構

```
.
├── app/                       # GitHub Copilot SDK Agent 服務 (Python)
│   ├── main.py                # FastAPI HTTP API
│   ├── agent.py               # GitHubCopilotAgent 封裝
│   ├── tools.py               # 範例 function tools
│   └── requirements.txt
├── Dockerfile                 # Python 3.12 + Node 20 + Copilot CLI
├── k8s/
│   ├── namespace.yaml
│   ├── runtimeclass.yaml      # RuntimeClass 參考檔(AKS 會自動建立 kata-vm-isolation)
│   ├── secret.example.yaml    # 範例 secret (請改用真正 token)
│   ├── deployment.yaml        # 強制 kata-vm-isolation
│   ├── service.yaml
│   └── networkpolicy.yaml     # 限縮 egress;ingress 允許 copilot-agent / kube-system
└── infra/
    ├── 01-create-aks.sh       # 建立支援 Kata 的 AKS
    ├── 02-build-push.sh       # 建置並推送鏡像到 ACR(會自動從 RG 偵測 ACR)
    └── 03-deploy.sh           # 部署 manifests
```

## 部署步驟(概要)

1. `bash infra/01-create-aks.sh` — 建立啟用 `KataVmIsolation` 的 AKS。
2. `kubectl get runtimeclass kata-vm-isolation` — 確認 RuntimeClass 已由 AKS 自動建立。
3. `bash infra/02-build-push.sh` — 建置 image 並推到 ACR;若未設定 `ACR` 環境變數,腳本會自動從 resource group 偵測。
4. 把 GitHub Copilot token 放進 `k8s/secret.example.yaml`,改名為 `secret.yaml`(請勿提交)。
5. `bash infra/03-deploy.sh` — apply 全部 manifests。
6. 透過 API server proxy 呼叫 agent(詳見下方 [呼叫 Agent](#呼叫-agent))。

## 呼叫 Agent

> **重要**：`kubectl port-forward` **無法** 用於 Kata pod。應用程式 listener 在
> microVM 內部,`port-forward` 進到 host 上空的 sandbox netns 會得到 `connection refused`。
> 請使用以下任一方式。

### 方案 A：API server proxy(本機開發推薦)

```bash
kubectl proxy --port=8001 &

# 健康檢查
curl -s http://localhost:8001/api/v1/namespaces/copilot-agent/services/copilot-agent:80/proxy/healthz
curl -s http://localhost:8001/api/v1/namespaces/copilot-agent/services/copilot-agent:80/proxy/readyz

# Chat(同步 JSON)
curl -s -X POST \
  http://localhost:8001/api/v1/namespaces/copilot-agent/services/copilot-agent:80/proxy/chat \
  -H 'content-type: application/json' \
  -d '{"message":"用一句話介紹 Kata Containers"}'
# => {"reply":"Kata Containers 是一種結合虛擬機隔離與容器輕量化體驗的開源 runtime。"}

# Chat(串流純文字)
curl -N -X POST \
  http://localhost:8001/api/v1/namespaces/copilot-agent/services/copilot-agent:80/proxy/chat/stream \
  -H 'content-type: application/json' \
  -d '{"message":"列出 3 個 Linux kernel hardening 技巧","stream":true}'
```

### 方案 B：叢集內 client(適合 CI / smoke test)

```bash
kubectl -n copilot-agent run curl --rm -it --restart=Never \
  --image=curlimages/curl -- \
  -s -X POST http://copilot-agent/chat \
  -H 'content-type: application/json' \
  -d '{"message":"ping"}'
```

### 方案 C：透過 Ingress / LoadBalancer 對外曝露

生產環境請將 `k8s/service.yaml` 改為 `type: LoadBalancer`(或搭配 Application Gateway /
NGINX Ingress + TLS),直接呼叫公開 endpoint。

## 驗證 microVM 隔離

```bash
kubectl -n copilot-agent exec deploy/copilot-agent -- uname -r
# 預期顯示的 kernel 版本 與節點 (kubectl debug node) 的 kernel 不同 — 證明在 guest kernel 中。
```

## 注意事項

- **AKS 的 Kata 支援** 名稱為 *Pod Sandboxing*,RuntimeClass 為 `kata-vm-isolation`
  (底層 containerd handler 為 `kata`),hypervisor 為 Microsoft Hyper-V (mshv) 而非 QEMU。
  需要 `--os-sku AzureLinux` + 支援巢狀虛擬化的 VM 系列
  (例如 `Standard_D4s_v3`;若訂閱配額允許,`Standard_D4s_v5` 亦可)。
- 上游 Kata Containers 也提供 `kata-qemu` / `kata-clh` 等其他 RuntimeClass(本專案另附
  通用 `kata-qemu` 版本範例,適用於非 AKS 的自管 Kubernetes)。
- `copilot` CLI 需要可用的 GitHub Copilot 訂閱及 token,部署前請確認。
- Copilot CLI / SDK 讀取 token 的環境變數是 `GH_TOKEN`(或 `GITHUB_TOKEN`),**不是**
  `GH_COPILOT_TOKEN` 這類自訂名稱。Deployment 已將 `GH_TOKEN`、`GITHUB_TOKEN` 都從同一個
  Secret 值注入,SDK 才能順利認證。
- `GITHUB_COPILOT_MODEL` 必須是你的 Copilot 方案實際可用的 model(例如
  `gpt-4.1`、`claude-3.5-sonnet`、`o4-mini`)。設為空字串 `""` 則使用 SDK 預設 model。
  不可用的 model 名稱會在 session 建立時回 `Model "..." is not available`。
- `copilot` CLI 啟動時會將 bundled package 解壓到 `/home/agent/.cache`。由於容器
  使用 `readOnlyRootFilesystem: true`,deployment 已在 `/home/agent/.cache`
  掛載 `emptyDir`(與 `/home/agent/.copilot`、`/tmp` 並列),否則 CLI 將無法啟動。
- `NetworkPolicy` 的 ingress 同時允許 `copilot-agent` 與 `kube-system` namespace
  (讓 `kubectl proxy` / API server proxy 與叢集內 Ingress controller 能到達 Service)。
  生產環境若以專用 Ingress namespace 為前端,請進一步收緊。
- **Kata 限制**：`kubectl port-forward` 對 Kata pod 無效(應用 listener 在 microVM 內,
  不在 sandbox netns)。請改用 [呼叫 Agent](#呼叫-agent) 章節提供的方式。
- 預設 `on_permission_request` 採白名單拒絕策略,請勿在生產環境改成全部 approve。
