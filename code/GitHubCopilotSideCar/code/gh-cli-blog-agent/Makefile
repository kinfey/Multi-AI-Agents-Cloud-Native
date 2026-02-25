CLUSTER_NAME ?= blog-agent-demo
ARCH := $(shell uname -m)

ifeq ($(ARCH),x86_64)
DOCKER_PLATFORM ?= linux/amd64
else ifeq ($(ARCH),aarch64)
DOCKER_PLATFORM ?= linux/arm64
else ifeq ($(ARCH),arm64)
DOCKER_PLATFORM ?= linux/arm64
else
DOCKER_PLATFORM ?= linux/amd64
endif

.PHONY: check-tools install-tools-macos install-tools-linux cluster-up cluster-down build load deploy up down port-forward status logs clean doctor wait-ready smoke

check-tools:
	@command -v docker >/dev/null 2>&1 || (echo "docker is not installed" && exit 1)
	@command -v kubectl >/dev/null 2>&1 || (echo "kubectl is not installed" && exit 1)
	@command -v kind >/dev/null 2>&1 || (echo "kind is not installed" && exit 1)
	@echo "Dependency check passed: docker / kubectl / kind (arch=$(ARCH), platform=$(DOCKER_PLATFORM))"

install-tools-macos:
	@command -v brew >/dev/null 2>&1 || (echo "Homebrew not found, please install brew first: https://brew.sh" && exit 1)
	brew install kubectl kind

install-tools-linux:
	@command -v curl >/dev/null 2>&1 || (echo "curl not found, please install curl first" && exit 1)
	@command -v sudo >/dev/null 2>&1 || (echo "sudo not found, please run as root or install sudo" && exit 1)
	@if command -v kubectl >/dev/null 2>&1; then \
		echo "kubectl already installed, skipping"; \
	else \
		echo "Installing kubectl..."; \
		curl -fsSL "https://dl.k8s.io/release/$$(curl -fsSL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" -o kubectl; \
		sudo install -m 0755 kubectl /usr/local/bin/kubectl; \
		rm -f kubectl; \
	fi
	@if command -v kind >/dev/null 2>&1; then \
		echo "kind already installed, skipping"; \
	else \
		echo "Installing kind..."; \
		ARCH_KIND=amd64; \
		if [ "$(ARCH)" = "aarch64" ] || [ "$(ARCH)" = "arm64" ]; then ARCH_KIND=arm64; fi; \
		curl -fsSL "https://kind.sigs.k8s.io/dl/v0.24.0/kind-linux-$${ARCH_KIND}" -o kind; \
		sudo install -m 0755 kind /usr/local/bin/kind; \
		rm -f kind; \
	fi
	@kubectl version --client
	@kind version

cluster-up:
	kind get clusters | grep -qx "$(CLUSTER_NAME)" || kind create cluster --name $(CLUSTER_NAME)

cluster-down:
	kind delete cluster --name $(CLUSTER_NAME)

build:
	docker build --platform $(DOCKER_PLATFORM) -t blog-agent-main:latest ./app
	docker build --platform $(DOCKER_PLATFORM) -t blog-agent-copilot:latest ./sidecar
	docker build --platform $(DOCKER_PLATFORM) -t blog-agent-skill:latest ./skill-sidecar

load:
	kind load docker-image blog-agent-main:latest --name $(CLUSTER_NAME)
	kind load docker-image blog-agent-copilot:latest --name $(CLUSTER_NAME)
	kind load docker-image blog-agent-skill:latest --name $(CLUSTER_NAME)

deploy:
	kubectl apply -f k8s/configmap.yaml
	kubectl apply -f k8s/secret.yaml
	kubectl apply -f k8s/deployment.yaml
	kubectl apply -f k8s/service.yaml

up: check-tools cluster-up build load deploy
	@echo ""
	@echo "======================================================"
	@echo "Blog App deployed! (Copilot Agent + Skill as sidecars)"
	@echo "======================================================"
	kubectl get pods -l app=blog-agent

down:
	kubectl delete -f k8s/service.yaml --ignore-not-found=true
	kubectl delete -f k8s/deployment.yaml --ignore-not-found=true
	kubectl delete -f k8s/secret.yaml --ignore-not-found=true
	kubectl delete -f k8s/configmap.yaml --ignore-not-found=true

port-forward:
	@echo "Blog App (Main):            http://localhost:8080/"
	@echo "Blog Files:                 http://localhost:8080/blog/"
	@echo "Copilot Agent (via proxy):  http://localhost:8080/agent/"
	@echo "Skill Server (via proxy):   http://localhost:8080/skill/"
	@echo "Copilot Agent (direct):     http://localhost:8001/"
	@echo "Skill Server (direct):      http://localhost:8002/"
	@echo ""
	kubectl port-forward --address 0.0.0.0  svc/blog-agent-svc 8080:80 8001:8001 8002:8002

status:
	kubectl get deploy,pod,svc -l app=blog-agent

logs:
	kubectl logs deploy/blog-agent -c blog-app --tail=50

logs-copilot:
	kubectl logs deploy/blog-agent -c copilot-agent --tail=50

logs-skill:
	kubectl logs deploy/blog-agent -c skill-server --tail=50

wait-ready:
	kubectl rollout status deployment/blog-agent --timeout=180s

smoke: wait-ready
	@echo "Checking monitor status file..."
	kubectl exec deployment/blog-agent -c blog-app -- sh -c 'test -f /usr/share/nginx/html/blog/sidecar-status.txt'
	kubectl exec deployment/blog-agent -c blog-app -- sh -c 'grep -q "sidecar" /usr/share/nginx/html/blog/sidecar-status.txt'
	@echo "Checking Skill Server sidecar health..."
	kubectl exec deployment/blog-agent -c blog-app -- sh -c 'curl -sf http://127.0.0.1:8002/health || echo "Skill server not ready yet"'
	@echo "Checking Copilot Agent sidecar health..."
	kubectl exec deployment/blog-agent -c blog-app -- sh -c 'curl -sf http://127.0.0.1:8001/health || echo "Copilot agent not ready yet (needs COPILOT_GITHUB_TOKEN)"'
	@echo ""
	@echo "✅ Smoke test passed!"

set-token:
	@read -p "Enter your GitHub Copilot Token: " token; \
	kubectl create secret generic blog-agent-secret \
		--from-literal=copilot-github-token="$$token" \
		--dry-run=client -o yaml | kubectl apply -f -
	kubectl rollout restart deployment/blog-agent

clean: down

doctor:
	@echo "== Host =="
	@echo "arch: $(ARCH)"
	@echo "docker platform(default): $(DOCKER_PLATFORM)"
	@echo
	@echo "== CLI Version =="
	@docker --version || true
	@kubectl version --client || true
	@kind version || true
	@echo
	@echo "== Kind Cluster =="
	@kind get clusters || true
	@echo
	@echo "== Kubectl Context =="
	@kubectl config current-context || true
	@echo
	@echo "== Nodes =="
	@kubectl get nodes -o wide || true
