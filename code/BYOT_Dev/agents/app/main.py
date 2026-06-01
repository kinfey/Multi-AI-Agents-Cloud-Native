"""ASGI entry point — exposes /healthz, /readyz, and the FastMCP server at /mcp.

The role is picked at process start from the AGENT_ROLE env var. The same image
is used for all four agents; only the env var (and consequently the registered
MCP tools + system prompt) differs.
"""

from __future__ import annotations

import logging
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from app.roles import build_role

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("byot-agent")

ROLE_NAME = os.environ.get("AGENT_ROLE", "requirements")
role = build_role(ROLE_NAME)

logger.info("starting role=%s server=%s", role.key, role.server_name)

# Stateless HTTP lets a single replica field many concurrent Copilot calls
# without keeping per-session state. Streamable HTTP is mounted at /mcp.
# DNS rebinding protection is disabled because clients reach the agent via
# `kubectl proxy`, which rewrites the Host header to the AKS API server FQDN.
mcp = FastMCP(
    name=role.server_name,
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
role.register(mcp)

mcp_app = mcp.streamable_http_app()


async def healthz(_request):
    return JSONResponse({"status": "ok", "role": role.key})


async def readyz(_request):
    return JSONResponse({"status": "ready", "role": role.key, "server": role.server_name})


# Mount("/") catches /mcp (and any future MCP routes). Concrete /healthz and
# /readyz are defined first so they match before the catch-all.
app = Starlette(
    debug=False,
    routes=[
        Route("/healthz", healthz),
        Route("/readyz", readyz),
        Mount("/", app=mcp_app),
    ],
    lifespan=mcp_app.router.lifespan_context,
)
