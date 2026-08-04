"""OAuth-protected MCP facade for the privileged coding runner."""

from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import KeycloakJWTVerifier

RUNNER_SOCKET = os.getenv("RUNNER_SOCKET", "/run/runner/runner.sock")


def _env_or_default(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


PUBLIC_BASE_URL = _env_or_default("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
RESOURCE_SERVER_URL = _env_or_default("MCP_RESOURCE_URL", f"{PUBLIC_BASE_URL}/mcp").rstrip("/")
OAUTH_ISSUER_URL = _env_or_default("OAUTH_ISSUER_URL", f"{PUBLIC_BASE_URL}/realms/mcp").rstrip("/")
OAUTH_JWKS_URL = os.getenv(
    "OAUTH_JWKS_URL", "http://keycloak:8080/realms/mcp/protocol/openid-connect/certs"
)
OAUTH_AUDIENCE = os.getenv("OAUTH_AUDIENCE", "mcp-server")
OAUTH_ENABLED = os.getenv("OAUTH_ENABLED", "true").lower() in {"1", "true", "yes", "on"}


def _split_values(value: str) -> list[str]:
    return [item.strip() for item in value.replace(",", " ").split() if item.strip()]


REQUIRED_SCOPES = _split_values(os.getenv("OAUTH_REQUIRED_SCOPES", "mcp:tools"))


def _transport_security() -> TransportSecuritySettings:
    parsed = urlparse(PUBLIC_BASE_URL)
    allowed_hosts = {"127.0.0.1:*", "localhost:*", "mcp:8000"}
    allowed_origins = {"http://127.0.0.1:*", "http://localhost:*", "https://chatgpt.com"}
    if parsed.hostname:
        allowed_hosts.add(parsed.hostname)
        allowed_hosts.add(f"{parsed.hostname}:*")
    if parsed.scheme and parsed.netloc:
        allowed_origins.add(f"{parsed.scheme}://{parsed.netloc}")
    allowed_hosts.update(_split_values(os.getenv("MCP_ALLOWED_HOSTS", "")))
    allowed_origins.update(_split_values(os.getenv("MCP_ALLOWED_ORIGINS", "")))
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(allowed_hosts),
        allowed_origins=sorted(allowed_origins),
    )


def _build_server() -> FastMCP:
    common = dict(
        name="Ubuntu Coding Runner",
        instructions=(
            "Run commands and edit files in /workspace. The command runner executes as root, "
            "has outbound network access, can install operating-system packages, and can use Git and GitHub CLI. "
            "Treat every command as a privileged, potentially destructive action."
        ),
        host="0.0.0.0",
        port=8000,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=_transport_security(),
    )
    if not OAUTH_ENABLED:
        return FastMCP(**common)

    verifier = KeycloakJWTVerifier(
        issuer=OAUTH_ISSUER_URL,
        jwks_url=OAUTH_JWKS_URL,
        audience=OAUTH_AUDIENCE,
        required_scopes=REQUIRED_SCOPES,
    )
    return FastMCP(
        **common,
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=OAUTH_ISSUER_URL,
            resource_server_url=RESOURCE_SERVER_URL,
            required_scopes=REQUIRED_SCOPES,
        ),
    )


mcp = _build_server()


async def _runner_request(method: str, path: str, payload: dict | None = None) -> dict:
    transport = httpx.AsyncHTTPTransport(uds=RUNNER_SOCKET)
    async with httpx.AsyncClient(transport=transport, base_url="http://runner", timeout=330.0) as client:
        response = await client.request(method, path, json=payload)
    data = response.json()
    if response.is_error:
        raise RuntimeError(data.get("error", f"Runner returned HTTP {response.status_code}"))
    return data


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    try:
        runner = await _runner_request("GET", "/health")
        return JSONResponse({"ok": True, "runner": runner.get("ok", False), "oauth": OAUTH_ENABLED})
    except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)


@mcp.tool(
    title="Runner information",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
)
async def sandbox_info() -> dict:
    """Return privilege, network, filesystem, GitHub CLI, and execution-limit information."""
    return await _runner_request("GET", "/info")


@mcp.tool(
    title="Run privileged command",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def run_command(command: str, cwd: str = ".", timeout_seconds: int = 60) -> dict:
    """Run a root Bash command with outbound network access, starting inside /workspace.

    Commands can modify the entire runner container, install packages, access persisted GitHub
    credentials, and contact external services. The timeout is capped by the server.
    """
    return await _runner_request(
        "POST",
        "/execute",
        {"command": command, "cwd": cwd, "timeout_seconds": timeout_seconds},
    )


@mcp.tool(
    title="Read workspace file",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
)
async def read_file(path: str, max_bytes: int = 262144) -> dict:
    """Read a UTF-8/text file inside /workspace, with a bounded response size."""
    return await _runner_request("POST", "/read", {"path": path, "max_bytes": max_bytes})


@mcp.tool(
    title="Write workspace file",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    ),
)
async def write_file(path: str, content: str, overwrite: bool = False) -> dict:
    """Create a workspace file, or replace it only when overwrite is true."""
    return await _runner_request(
        "POST", 
        "/write", 
        {"path": path, "content": content, "overwrite": overwrite}
    )


@mcp.tool(
    title="List workspace files",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
)
async def list_files(path: str = ".", max_depth: int = 4, max_entries: int = 500) -> dict:
    """List files and directories under a workspace path without following symlinks."""
    return await _runner_request(
        "POST",
        "/list",
        {"path": path, "max_depth": max_depth, "max_entries": max_entries},
    )


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
