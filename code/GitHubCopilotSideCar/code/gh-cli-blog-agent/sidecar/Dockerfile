# Dockerfile for Copilot CLI Agent (sidecar container)

FROM python:3.12.10-slim

# Build arguments
ARG COPILOT_VERSION=latest

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    xz-utils \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20+ (official binary, no NodeSource APT repo needed)
ARG NODE_VERSION=20.20.0
RUN DPKG_ARCH=$(dpkg --print-architecture) \
    && case "${DPKG_ARCH}" in amd64) ARCH=x64;; arm64) ARCH=arm64;; armhf) ARCH=armv7l;; *) ARCH=${DPKG_ARCH};; esac \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${ARCH}.tar.xz" -o node.tar.xz \
    && tar -xJf node.tar.xz -C /usr/local --strip-components=1 --no-same-owner \
    && rm -f node.tar.xz \
    && ln -sf /usr/local/bin/node /usr/local/bin/nodejs

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
      description="GitHub Copilot CLI Blog Agent - Sidecar Container"

# Expose agent port
EXPOSE 8001

# Default command
CMD ["python", "main.py"]
