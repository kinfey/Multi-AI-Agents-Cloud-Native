# syntax=docker/dockerfile:1.7
#
# Image bundling:
#  - Python 3.12 (FastAPI + agent-framework-github-copilot)
#  - Node.js 20  (required by GitHub Copilot CLI / MCP servers)
#  - GitHub Copilot CLI (`copilot`)
#
# The container is designed to run inside a Kata Containers microVM
# (RuntimeClass: kata-mshv-vm-isolation on AKS). Even with that, we still
# run as a non-root user with a read-only root filesystem.

FROM python:3.12-slim-bookworm AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NODE_MAJOR=20

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git tini; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends nodejs; \
    rm -rf /var/lib/apt/lists/*; \
    npm config set update-notifier false; \
    npm install -g @github/copilot

# Non-root user
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin agent

WORKDIR /app
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

ENV PATH="/usr/local/bin:/usr/bin:/bin" \
    GITHUB_COPILOT_CLI_PATH=/usr/bin/copilot \
    GITHUB_COPILOT_COPILOT_HOME=/home/agent/.copilot \
    HOME=/home/agent \
    PYTHONPATH=/app

RUN mkdir -p /home/agent/.copilot && chown -R agent:agent /home/agent /app

USER agent
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
