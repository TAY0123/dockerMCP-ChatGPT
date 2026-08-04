# Docker MCP ChatGPT Sandbox

A self-hosted remote MCP server for ChatGPT that can run shell commands and edit code inside a hardened Ubuntu container. OAuth is bundled with Keycloak, and an optional Cloudflare Tunnel is enabled with a Docker Compose profile.

## What this reuses

This project deliberately avoids implementing mature infrastructure from scratch:

- **Model Context Protocol Python SDK** provides FastMCP, Streamable HTTP, OAuth protected-resource metadata, bearer-token middleware, and tool annotations.
- **Keycloak** provides the OAuth/OIDC authorization server, login UI, authorization-code flow, PKCE, refresh tokens, and client management.
- **Caddy** provides a small reverse-proxy layer so the MCP endpoint and OAuth issuer use one public hostname.
- **Cloudflare Tunnel** provides the optional outbound-only public route.
- **Ubuntu 24.04** supplies the coding toolchain: Bash, Python, Node.js, Git, compilers, ripgrep, fd, jq, and curl.

There was no existing application code in this repository to preserve; the original repository contained only an empty `README` file.

## Architecture

```text
ChatGPT
   |
   | HTTPS / OAuth
   v
Cloudflare Tunnel (optional Compose profile)
   |
   v
Caddy gateway :8080
   |-- /mcp and RFC 9728 metadata --> FastMCP service
   |                                      |
   |                                      | Unix domain socket only
   |                                      v
   |                                Ubuntu runner container
   |                                - no network interface
   |                                - non-root user
   |                                - read-only root filesystem
   |                                - writable /workspace volume
   |
   `-- /realms/mcp/* --> Keycloak --> PostgreSQL
```

The MCP service and command runner are separate containers. The runner has `network_mode: none`; it cannot reach Keycloak, the internet, or other containers. It accepts requests only through a shared Unix socket mounted into the MCP service.

## Exposed MCP tools

- `sandbox_info`: report sandbox isolation and limits.
- `run_command`: execute a Bash command under `/workspace`.
- `read_file`: read a bounded text file from `/workspace`.
- `write_file`: create or explicitly overwrite a text file.
- `list_files`: list a bounded directory tree without following symlinks.

All paths are resolved and checked against `/workspace`. Output, file sizes, process count, memory, CPU, open files, and execution time are bounded.

## Requirements

- Ubuntu host with Docker Engine and Docker Compose v2.
- A ChatGPT plan/workspace that supports custom remote MCP apps.
- A public HTTPS hostname for ChatGPT. Cloudflare Tunnel is included, but any HTTPS reverse proxy can be used.

## Initial setup

Copy the environment template:

```bash
cp .env.example .env
```

Generate independent secrets and put them in `.env`:

```bash
openssl rand -hex 32
```

At minimum, replace `POSTGRES_PASSWORD`, `KEYCLOAK_ADMIN_PASSWORD`, `OAUTH_CLIENT_SECRET`, and `MCP_USER_PASSWORD`.

Set `PUBLIC_BASE_URL` to the final HTTPS origin, for example:

```dotenv
PUBLIC_BASE_URL=https://mcp.example.com
```

Do not include `/mcp` in `PUBLIC_BASE_URL`.

### Obtain the ChatGPT callback URL

While creating the custom app in ChatGPT, select OAuth and copy the exact callback URL shown by ChatGPT. It looks similar to:

```text
https://chatgpt.com/connector/oauth/<callback_id>
```

Set that full value as `CHATGPT_CALLBACK_URL`. Do not replace the callback ID, add a trailing slash, or use a wildcard.

The OAuth values entered in ChatGPT must match `.env`:

```text
MCP endpoint:  https://mcp.example.com/mcp
Client ID:     value of OAUTH_CLIENT_ID
Client secret: value of OAUTH_CLIENT_SECRET
Scopes:        openid profile email offline_access mcp:tools
```

The authorization-server issuer is:

```text
https://mcp.example.com/realms/mcp
```

The initial realm user is configured by `MCP_USER` and `MCP_USER_PASSWORD`. Keycloak forces a password change on first login.

## Run locally without Cloudflare Tunnel

The default Compose stack binds only to loopback:

```bash
docker compose up -d --build
```

Endpoints:

```text
MCP:             http://localhost:8080/mcp
Health:          http://localhost:8080/health
Keycloak admin:  http://localhost:8081/admin/
```

OAuth issuer URLs must match the token issuer exactly. For a real ChatGPT connection, use the final HTTPS `PUBLIC_BASE_URL`, not the local URL.

## Enable Cloudflare Tunnel

Create a named tunnel in Cloudflare Zero Trust and configure one public hostname:

```text
Hostname: mcp.example.com
Service:  http://gateway:8080
```

Copy the tunnel token into `CLOUDFLARE_TUNNEL_TOKEN`, then start the profile:

```bash
docker compose --profile tunnel up -d --build
```

Without `--profile tunnel`, the `cloudflared` container does not start.

The tunnel does not replace OAuth. It only publishes the gateway through an outbound-only connection. Do not put an interactive Cloudflare Access login in front of the MCP hostname unless ChatGPT is explicitly configured to satisfy it; Keycloak already protects the MCP endpoint.

## Workspace management

The writable workspace is a named Docker volume. Import a project:

```bash
docker compose cp ./my-project/. runner:/workspace/
```

Export it:

```bash
docker compose cp runner:/workspace/. ./workspace-export
```

Inspect it directly:

```bash
docker compose exec runner bash
```

The runner has no network interface, so package downloads, `git clone`, and remote API calls fail by design. Import dependencies or source code from the host instead. This default is intentional for a command-execution service reachable by an LLM.

## OAuth and realm changes

Keycloak imports the realm only when the `mcp` realm does not already exist. Changing `CHATGPT_CALLBACK_URL`, the OAuth client secret, or the bootstrap user in `.env` does not rewrite an existing realm.

For an existing deployment, update the client in the local Keycloak admin console. During disposable development, stop the stack and remove only the PostgreSQL data volume to re-import the realm. Do not remove the workspace volume unless you intend to delete workspace files.

## Hardening included

- Dedicated non-root user.
- Separate MCP and runner processes in separate containers.
- Runner has no Docker socket and no network namespace.
- Read-only root filesystems.
- Writable paths limited to tmpfs, the Unix-socket volume, and `/workspace`.
- All Linux capabilities dropped.
- `no-new-privileges` enabled.
- CPU, memory, PID, file-descriptor, output-size, file-size, and timeout limits.
- Workspace path traversal and symlink escape checks.
- OAuth access-token signature, issuer, audience, expiry, and scope validation.
- Keycloak admin endpoints blocked from the public gateway and bound separately to loopback.
- Cloudflare Tunnel disabled unless its Compose profile is selected.

## Important security limit

This is a hardened Docker sandbox, not a virtual machine boundary. Containers share the Ubuntu host kernel. A kernel or container-runtime vulnerability can break isolation. For hostile multi-tenant workloads, place the runner behind gVisor, Kata Containers, or a Firecracker-style microVM runtime, and keep the host patched.

Also treat every MCP tool as high impact. Restrict who can authenticate, keep ChatGPT action confirmations enabled, review commands, rotate secrets, and back up only the workspace data you actually need.

## Useful commands

```bash
# Show status
docker compose ps

# Follow logs
docker compose logs -f mcp runner keycloak gateway

# Stop services without deleting data
docker compose down

# Rebuild after code changes
docker compose up -d --build
```
