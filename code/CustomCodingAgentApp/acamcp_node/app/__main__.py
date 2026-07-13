"""Entrypoint: run the MCP server over the streamable HTTP transport."""

from .server import create_default_server


def main() -> int:
    server = create_default_server()
    # Streamable HTTP transport serves the MCP endpoint at /mcp.
    server.run(transport="streamable-http")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
