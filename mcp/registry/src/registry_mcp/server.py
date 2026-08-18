"""registry-mcp: trivial streamable-HTTP MCP server with a live registry endpoint.

Proof of principle for the uv-installed / systemd-run HTTP MCP pattern:
  - installed with `uv` from a pyproject.toml (no top-level project needed)
  - runs as a persistent process (unlike stdio MCPs, which the client spawns)
  - all runtime config (host/port/registry file) comes in as CLI args, so
    the systemd unit is a single self-contained ExecStart line
  - also serves a plain GET /registry endpoint returning ready-to-use
    mcpServers JSON (url + description per server), built from a ports.json
    registry file, so a client-side sync step can bootstrap its static MCP
    config from one known URL instead of hand-editing ports as new HTTP
    MCPs come online -- and so an agent calling the list_mcp_servers tool
    gets enough context to judge which server to use, not just where it is.
  - also serves a GET /list HTML page -- the same data, for humans.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import socket
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

_DEFAULT_REGISTRY_FILE = "config/ports.json"

# mcp>=2.0 renamed FastMCP -> MCPServer and dropped host/port from the
# constructor / .settings -- they're passed to run() instead, see main().
mcp = MCPServer("registry")

# Set once by main() from CLI args before mcp.run(); read per-request by the
# tools/routes below.
_state: dict = {
    "port": 8000,
    "public_host": None,
    "registry_file": Path(_DEFAULT_REGISTRY_FILE),
}


def _load_entries() -> dict[str, dict]:
    """name -> {"port": int, "description": str}, straight from ports.json."""
    try:
        with _state["registry_file"].open() as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "registry": {
                "port": _state["port"],
                "description": "MCP server registry (this service).",
            }
        }


def _build_registry() -> dict:
    host = _state["public_host"] or socket.getfqdn()
    entries = _load_entries()
    return {
        "mcpServers": {
            name: {
                "url": f"http://{host}:{info['port']}/mcp",
                "description": info.get("description", ""),
            }
            for name, info in entries.items()
        }
    }


@mcp.tool()
def say_hello(name: str = "world") -> str:
    """Return a greeting. Trivial tool proving the install/config/start pattern works end to end."""
    host = _state["public_host"] or socket.getfqdn()
    return f"Hello, {name}! registry-mcp is alive on {host}:{_state['port']}."


@mcp.tool()
def list_mcp_servers() -> str:
    """Return the known MCP server registry (name -> url, description) for this deployment, as JSON text."""
    return json.dumps(_build_registry(), indent=2)


@mcp.custom_route("/registry", methods=["GET"])
async def registry_endpoint(request: Request) -> JSONResponse:
    """Plain HTTP GET, outside the MCP protocol -- for client-side config sync scripts."""
    return JSONResponse(_build_registry())


@mcp.custom_route("/list", methods=["GET"])
async def list_page(request: Request) -> HTMLResponse:
    """Human-readable HTML listing of the registry -- same data as /registry, for people."""
    host = _state["public_host"] or socket.getfqdn()
    entries = _load_entries()
    rows = []
    for name, info in sorted(entries.items()):
        port = info.get("port", "")
        description = info.get("description", "")
        url = f"http://{host}:{port}/mcp"
        rows.append(
            "<tr>"
            f"<td>{html.escape(name)}</td>"
            f"<td>{html.escape(str(port))}</td>"
            f"<td><code>{html.escape(url)}</code></td>"
            f"<td>{html.escape(description)}</td>"
            "</tr>"
        )
    body = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>MCP Registry</title>"
        "<style>"
        "body{font-family:sans-serif;margin:2rem;}"
        "table{border-collapse:collapse;}"
        "th,td{border:1px solid #ccc;padding:0.4rem 0.8rem;text-align:left;}"
        "th{background:#f0f0f0;}"
        "</style></head><body>"
        f"<h1>MCP Registry &mdash; {html.escape(host)}</h1>"
        "<table><thead><tr><th>Name</th><th>Port</th><th>URL</th><th>Description</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        "<p>Machine-readable: <a href='/registry'>/registry</a></p>"
        "</body></html>"
    )
    return HTMLResponse(body)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="registry-mcp", description=__doc__)
    parser.add_argument(
        "--host",
        default=os.environ.get("REGISTRY_MCP_HOST", "0.0.0.0"),
        help="bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("REGISTRY_MCP_PORT", "8000")),
        help="bind port (default: 8000)",
    )
    parser.add_argument(
        "--registry",
        default=os.environ.get("REGISTRY_MCP_REGISTRY_FILE", _DEFAULT_REGISTRY_FILE),
        help=(
            "path to the mcp-servers ports.json registry file "
            "(default: %(default)s, resolved relative to CWD -- pass an "
            "absolute path for a real deployment)"
        ),
    )
    parser.add_argument(
        "--public-host",
        default=os.environ.get("REGISTRY_MCP_PUBLIC_HOST"),
        help="hostname advertised in registry URLs (default: this host's FQDN)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="import/construct only, then exit -- does not bind a socket",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    _state["port"] = args.port
    _state["public_host"] = args.public_host
    _state["registry_file"] = Path(args.registry)

    if args.check:
        print(
            f"OK: registry_mcp constructed "
            f"(host={args.host} port={args.port} registry={args.registry})"
        )
        return

    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
