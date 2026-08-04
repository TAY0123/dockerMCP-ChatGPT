# Docker MCP ChatGPT Coding Runner

A self-hosted remote MCP server for ChatGPT that can run shell commands, edit code, install Ubuntu packages, use Git, and authenticate with GitHub CLI. OAuth is bundled with Keycloak, and Cloudflare Tunnel can be enabled with a Docker Compose profile.

## What this reuses

This project avoids rebuilding mature infrastructure:

- **Model Context Protocol Python SDK** provides FastMCP, Streamable HTTP, OAuth protected-resource metadata, bearer-token middleware, and tool annotations.
- **Keycloak** provides OAuth/OIDC, login UI, authorization-code flow, PKCE, refresh tokens, and client management.
- **Caddy** provides reverse proxying so MCP and OAuth use one public hostname.
- **Cloudflare Tunnel** provides the optional outbound-only public route.
- **Ubuntu 24.04** supplies Bash, Python, Node.js, Git, GitHub CLI (`gh`), compilers, ripgrep, fd, jq, and curl.

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
   |-- /mcp and RFC 9728 metadata --> non-root FastMCP service
   |                                      |
   |                                      | Unix domain socket only
   |                                      v
   |                                Ubuntu command runner
   |                                - root user
   |                                - writable container filesystem
   |                                - outbound internet access
   |                                - Git and GitHub CLI
   |                                - persistent /workspace and /root
   |
   `-- /realms/mcp/* --> Keycloak --> PostgreSQL
```

The public MCP service and privileged command runner are separate containers. The MCP service remains non-root, read-only, capability-dropped, and connected to the runner only through a Unix socket. Commands requested through MCP execute as root in the runner container.

## Exposed MCP tools

- `sandbox_info`: report root, network, filesystem, GitHub CLI, and execution-limit state.
- `run_command`: execute a root Bash command starting under `/workspace`.
- `read_file`: read a bounded text file from `/workspace`.
- `write_file`: create or explicitly overwrite a text file under `/workspace`.
- `list_files`: list a bounded directory tree without following symlinks.

The dedicated file tools remain confined to `/workspace`. The `run_command` tool is intentionally not confined to workspace file access because root package installation requires access to the container filesystem.

## Included development tools

The image includes:

```text
bash, build-essential, curl, fd, git, gh, jq,
nodejs, npm, python3, pip, venv, ripgrep
```

Check them from the runner:

```bash
docker compose exec runner git --version
docker compose exec runner gh --version
```

## Requirements

- Ubuntu host with Docker Engine and Docker Compose v2.
- A ChatGPT plan or workspace that supports custom remote MCP apps.
- A public HTTPS hostname for ChatGPT. Cloudflare Tunnel is included, but another HTTPS reverse proxy can be used.

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

Set `PUBLIC_BASE_URL` to the final HTTPS origin:

```dotenv
PUBLIC_BASE_URL=https://mcp.example.com
```

Do not include `/mcp` in `PUBLIC_BASE_URL`.

### Obtain the ChatGPT callback URL

While creating the custom app in ChatGPT, select OAuth and copy the exact callback URL shown by ChatGPT. It looks similar to:

```text
https://chatgpt.com/connector/oauth/<callback_id>
```

Set the full value as `CHATGPT_CALLBACK_URL`. Do not replace the callback ID, add a trailing slash, or use a wildcard.

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

```bash
docker compose up -d --build
```

Endpoints:

```text
MCP:             http://localhost:8080/mcp
Health:          http://localhost:8080/health
Keycloak admin:  http://localhost:8081/admin/
```

OAuth issuer URLs must match the token issuer exactly. For a ChatGPT connection, use the final HTTPS `PUBLIC_BASE_URL`, not the local URL.

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

The tunnel does not replace OAuth. It only publishes the gateway through an outbound connection. Do not put another interactive login in front of this hostname unless ChatGPT can satisfy it; Keycloak already protects MCP.

## Install software at runtime

The runner executes as root, has outbound network access, and has a writable root filesystem. Install packages from the host:

```bash
docker compose exec runner bash -lc \
  'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y shellcheck'
```

The same command can be invoked through the MCP `run_command` tool:

```bash
apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y shellcheck
```

Runtime-installed packages remain while the current runner container exists, but disappear when Docker recreates that container. For permanent dependencies, add them to the `Dockerfile` and rebuild:

```bash
docker compose up -d --build --force-recreate runner mcp
```

## GitHub login

The official GitHub CLI is installed as `gh`. Authenticate interactively from the host terminal rather than sending credentials through ChatGPT:

```bash
docker compose exec runner gh auth login \
  --hostname github.com \
  --git-protocol https \
  --web
```

Then configure Git to use GitHub CLI as its credential helper:

```bash
docker compose exec runner gh auth setup-git
docker compose exec runner gh auth status
```

For a headless token login, keep the token out of shell history:

```bash
printf '%s' "$GH_TOKEN" | docker compose exec -T runner gh auth login --with-token
docker compose exec runner gh auth setup-git
```

GitHub CLI configuration, Git configuration, and SSH material under `/root` persist in the `runner_home` Docker volume. The workspace persists separately in the `workspace` volume.

After authentication, commands can use both tools:

```bash
docker compose exec runner git clone https://github.com/OWNER/REPOSITORY.git /workspace/REPOSITORY
docker compose exec runner gh repo view OWNER/REPOSITORY
```

## Workspace management

Import a project:

```bash
docker compose cp ./my-project/. runner:/workspace/
```

Export it:

```bash
docker compose cp runner:/workspace/. ./workspace-export
```

Open a root shell:

```bash
docker compose exec runner bash
```

## OAuth and realm changes

Keycloak imports the realm only when the `mcp` realm does not already exist. Changing `CHATGPT_CALLBACK_URL`, the OAuth client secret, or the bootstrap user in `.env` does not rewrite an existing realm.

For an existing deployment, update the client in the local Keycloak admin console. During disposable development, stop the stack and remove only the PostgreSQL data volume to re-import the realm. Do not remove `workspace` or `runner_home` unless you intend to delete source files or GitHub credentials.

## Remaining isolation

- The public MCP service is non-root and read-only.
- MCP and runner are separate processes in separate containers.
- The runner has no Docker socket.
- The runner is not attached to the internal Keycloak/PostgreSQL network.
- The runner reaches MCP only through a shared Unix socket.
- CPU, memory, PID, file-descriptor, output-size, file-size, and timeout limits remain enabled.
- Workspace path traversal and symlink escape checks remain enabled for dedicated file tools.
- OAuth access-token signature, issuer, audience, expiry, and scope validation remain enabled.
- Keycloak admin endpoints are blocked from the public gateway and bound separately to loopback.
- Cloudflare Tunnel remains disabled unless its Compose profile is selected.

## Critical security warning

This configuration gives an OAuth-authenticated LLM a remote root shell with outbound internet access. That is not a strong sandbox. A malicious instruction, prompt injection, compromised dependency, or stolen OAuth session can:

- modify the runner operating system;
- read persisted GitHub credentials;
- push code or change repositories using your GitHub identity;
- download and execute arbitrary software;
- exfiltrate workspace data;
- attack services reachable through the host or internet.

Use a dedicated low-privilege GitHub account or narrowly scoped token. Do not mount the Docker socket, host filesystem, personal SSH agent, cloud credentials, or production secrets. Keep ChatGPT action confirmations enabled.

Docker containers share the host kernel. For hostile or multi-user workloads, use gVisor, Kata Containers, or a Firecracker-style microVM and isolate the runner on a separate machine or disposable VM.

## Useful commands

```bash
# Show status
docker compose ps

# Follow logs
docker compose logs -f mcp runner keycloak gateway

# Check privilege and tools
docker compose exec runner bash -lc 'id && git --version && gh --version'

# Stop services without deleting data
docker compose down

# Rebuild after source or Dockerfile changes
docker compose up -d --build
```
