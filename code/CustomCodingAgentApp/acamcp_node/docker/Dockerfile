FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# MCP streamable HTTP endpoint is served at /mcp on this port.
ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    OPENCLAW_GATEWAY_URL=https://azure-openclaw-aca-app.bluedune-876fc257.swedencentral.azurecontainerapps.io

EXPOSE 8000

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

ENTRYPOINT ["python", "-m", "app"]
