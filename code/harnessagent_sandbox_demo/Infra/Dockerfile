# syntax=docker/dockerfile:1.7
#
# Single-stage build for the FIFA 2026 podcast pipeline.
# Hyperlight SDK + Python guest .aot are pulled from PyPI (hyperlight-sandbox
# 0.4.0 + hyperlight-sandbox-python-guest 0.4.0). No source compile needed.
#
# Runs on a Kubernetes node that exposes hyperlight.dev/hypervisor: kvm via
# https://github.com/hyperlight-dev/hyperlight-on-kubernetes
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HYPERLIGHT_PYTHON_MODULE_PATH=/opt/hyperlight/python-sandbox.aot \
    PODCAST_OUTPUT_DIR=/var/podcast/outputs \
    AZURE_CREDENTIAL_KIND=default

# git is required by the agent-framework-* git+https requirement entries.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 65532 podcast \
 && useradd  --system --uid 65532 --gid 65532 --home-dir /home/podcast \
             --shell /usr/sbin/nologin --create-home podcast \
 && mkdir -p /opt/hyperlight /var/podcast/outputs \
 && chown -R 65532:65532 /var/podcast

WORKDIR /app
COPY requirements.txt ./

# Install app deps + Hyperlight SDK with wasm backend and bundled Python guest.
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir "hyperlight-sandbox[wasm,python-guest]==0.4.0"

# Extract the bundled .aot from the python_guest package to a stable path.
RUN python -c "import importlib.resources as r, shutil; src = r.files('python_guest').joinpath('resources/python-sandbox.aot'); shutil.copy(str(src), '/opt/hyperlight/python-sandbox.aot')" \
 && chmod 0444 /opt/hyperlight/python-sandbox.aot

COPY agents/   ./agents/
COPY sandbox/  ./sandbox/
COPY skills/   ./skills/
COPY workflow_pipeline.py main.py ./

USER 65532:65532
VOLUME ["/var/podcast/outputs"]
ENTRYPOINT ["python", "main.py"]
