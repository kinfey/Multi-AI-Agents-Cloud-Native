# Single image used by all four roles. Selected at runtime via AGENT_ROLE.
#
# Layout inside container:
#   /app
#     app/
#       main.py              - Starlette: /healthz, /readyz, MCP at /mcp
#       airunway_client.py   - Builds OpenAIChatCompletionClient -> AI Runway
#       roles/
#         requirements_agent.py
#         code_agent.py
#         test_agent.py
#         deploy_agent.py
#
# The container is designed to run inside a Kata MicroVM
# (RuntimeClass: kata-vm-isolation on AKS). It also runs comfortably under runc.

FROM python:3.12-slim-bookworm AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates tini; \
    rm -rf /var/lib/apt/lists/*; \
    useradd --create-home --uid 10001 --shell /usr/sbin/nologin agent

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

RUN chown -R agent:agent /app
USER agent

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
