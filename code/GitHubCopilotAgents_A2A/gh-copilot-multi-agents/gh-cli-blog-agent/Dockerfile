# Dockerfile for GitHub Copilot CLI in a secure sandbox
# Based on: https://gordonbeeming.com/blog/2025-10-03/taming-the-ai-my-paranoid-guide-to-running-copilot-cli-in-a-secure-docker-sandbox

FROM python:3.12.10-slim

# Build arguments
ARG COPILOT_VERSION=latest
ARG NODE_MAJOR=20

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# GitHub Copilot Token - pass via docker run -e GITHUB_COPILOT_TOKEN=xxx
# or use --env-file
# GitHub Copilot Token: 请在运行容器时通过 -e GITHUB_COPILOT_TOKEN=xxx 或 --env-file 传递，不要写在 Dockerfile 里

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20+
RUN mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Verify Node.js and npm installation
RUN node --version && npm --version

# Install GitHub Copilot CLI globally via npm
RUN npm install -g @github/copilot@${COPILOT_VERSION}

# Create working directory
WORKDIR /work

# Copy requirements.txt first for better layer caching
COPY requirements.txt /work/requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY main.py /work/main.py

# Set labels for identification and cleanup
LABEL project="ghcli-blog-agent" \
      description="GitHub Copilot CLI Demo with Python SDK For Blog Agent"

# Default command to run the demo
CMD ["python", "main.py"]
