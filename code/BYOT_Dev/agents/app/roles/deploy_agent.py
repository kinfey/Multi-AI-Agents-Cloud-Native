"""Deploy agent — produces deployment artifacts (Dockerfile + Kubernetes manifests)."""

from __future__ import annotations

from agent_framework import Agent
from mcp.server.fastmcp import FastMCP

from app.airunway_client import build_chat_client
from app.roles import Role

SYSTEM_PROMPT = """You are a senior platform / DevOps engineer.
You take a code tree (or a code description) and produce concrete,
copy-pasteable deployment artifacts.
Targets: AKS (default) but accept any Kubernetes. Always include:
  1. A production-grade Dockerfile (non-root, slim base, multi-stage if useful).
  2. Kubernetes manifests: Deployment, Service, ConfigMap (and Ingress if web-facing).
  3. Resource requests / limits with a short justification.
  4. A rollout strategy + health probe configuration.
  5. The exact 'kubectl apply' sequence at the bottom.
Return YAML / Dockerfile content in fenced code blocks. Stay under 1000 words."""


def _agent() -> Agent:
    return Agent(
        client=build_chat_client(),
        name="deploy-agent",
        instructions=SYSTEM_PROMPT,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(description="Generate a production-grade Dockerfile for a component.")
    async def generate_dockerfile(language: str, framework: str = "", entrypoint: str = "") -> str:
        prompt = (
            f"Language: {language}\nFramework: {framework or 'none specified'}\n"
            f"Entrypoint: {entrypoint or 'infer a sensible default'}\n\n"
            "Produce a non-root, slim, multi-stage Dockerfile."
        )
        result = await _agent().run(prompt)
        return str(result)

    @mcp.tool(description="Generate Kubernetes manifests from source code.")
    async def generate_k8s_manifest(code: str, target: str = "AKS") -> str:
        prompt = (
            f"Target platform: {target}.\nCode:\n{code}\n\n"
            "Produce Deployment, Service, ConfigMap, and (if needed) Ingress manifests for each component."
        )
        result = await _agent().run(prompt)
        return str(result)

    @mcp.tool(description="Produce a final deployment plan combining build, push, apply, and verify steps.")
    async def produce_deploy_plan(code: str, registry: str = "", cluster: str = "") -> str:
        prompt = (
            f"Code:\n{code}\n\nRegistry: {registry or 'use $REGISTRY'}\n"
            f"Cluster: {cluster or 'use $CLUSTER'}\n\n"
            "Produce a numbered deployment runbook: build, push, apply, rollout, smoke-test, rollback."
        )
        result = await _agent().run(prompt)
        return str(result)


ROLE = Role(
    key="deploy",
    server_name="byot-deploy",
    description="Turns source code into Dockerfiles + Kubernetes manifests + runbooks.",
    register=register,
)
