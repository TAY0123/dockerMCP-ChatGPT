# Quick start

## Cloudflare Quick Tunnel

For a disposable test, run one command from the repository root:

```bash
bash scripts/quick-tunnel.sh
```

On the first run, the script:

1. copies `.env.example` to `.env`;
2. generates random database, Keycloak, OAuth-client, and login passwords;
3. builds and starts the Docker Compose stack;
4. creates a temporary `trycloudflare.com` URL;
5. updates `PUBLIC_BASE_URL` automatically;
6. prints the MCP endpoint and all values required by ChatGPT.

The generated `.env` is changed to mode `600`. Do not commit it.

Enter the printed values in ChatGPT:

```text
MCP endpoint:   https://<random>.trycloudflare.com/mcp
Client ID:      chatgpt-mcp
Client secret:  printed by the script
Scopes:         openid profile email offline_access mcp:tools
```

When OAuth opens, sign in with the printed MCP username and password. Keycloak asks you to change the temporary password on first login.

The default callback setting is:

```dotenv
CHATGPT_CALLBACK_URL=https://chatgpt.com/connector/oauth/*
```

That path wildcard avoids a two-stage setup for temporary testing. For a stable deployment, replace it with the exact callback URL displayed by ChatGPT and recreate the Keycloak realm or update the client in Keycloak.

Quick Tunnel hostnames change whenever `cloudflared-quick` is recreated. Update the MCP endpoint in ChatGPT after the hostname changes.

## Stable named tunnel

Initialize the environment first:

```bash
bash scripts/init-env.sh
```

Then edit `.env`:

```dotenv
PUBLIC_BASE_URL=https://mcp.example.com
CHATGPT_CALLBACK_URL=https://chatgpt.com/connector/oauth/<exact_callback_id>
CLOUDFLARE_TUNNEL_TOKEN=<named_tunnel_token>
```

Start the stable tunnel profile:

```bash
docker compose --profile tunnel up -d --build
```

## Useful credentials

The generated values remain in `.env`:

```bash
grep -E '^(OAUTH_CLIENT_ID|OAUTH_CLIENT_SECRET|MCP_USER|MCP_USER_PASSWORD)=' .env
```

Git and GitHub CLI are available in the root runner:

```bash
docker compose exec runner git --version
docker compose exec runner gh auth login --web
docker compose exec runner gh auth setup-git
```
