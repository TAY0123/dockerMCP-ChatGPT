#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-.env}"
TIMEOUT_SECONDS="${QUICK_TUNNEL_TIMEOUT_SECONDS:-90}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "$ENV_FILE does not exist. Copy .env.example to .env and set the required secrets first." >&2
  exit 1
fi

compose=(docker compose --env-file "$ENV_FILE")

# Start the application with the currently configured URL so the local gateway
# is healthy before cloudflared requests a temporary public hostname.
"${compose[@]}" up -d --build postgres keycloak runner mcp gateway

# A fresh cloudflared process creates a fresh trycloudflare.com hostname.
"${compose[@]}" --profile quick-tunnel rm -sf cloudflared-quick >/dev/null 2>&1 || true
"${compose[@]}" --profile quick-tunnel up -d cloudflared-quick

url=""
deadline=$((SECONDS + TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
  logs="$("${compose[@]}" --profile quick-tunnel logs --no-color cloudflared-quick 2>/dev/null || true)"
  url="$(printf '%s\n' "$logs" | grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n 1 || true)"
  if [[ -n "$url" ]]; then
    break
  fi
  sleep 2
done

if [[ -z "$url" ]]; then
  echo "Quick Tunnel did not publish a URL within ${TIMEOUT_SECONDS} seconds." >&2
  "${compose[@]}" --profile quick-tunnel logs --no-color cloudflared-quick >&2 || true
  exit 1
fi

tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT

awk -v value="$url" '
  BEGIN { replaced = 0 }
  /^PUBLIC_BASE_URL=/ {
    print "PUBLIC_BASE_URL=" value
    replaced = 1
    next
  }
  { print }
  END {
    if (!replaced) {
      print "PUBLIC_BASE_URL=" value
    }
  }
' "$ENV_FILE" > "$tmp_file"

chmod --reference="$ENV_FILE" "$tmp_file" 2>/dev/null || true
mv "$tmp_file" "$ENV_FILE"
trap - EXIT

# Recreate URL-sensitive services so MCP resource metadata, OAuth issuer URLs,
# allowed hosts, and Keycloak hostname all use the new temporary hostname.
"${compose[@]}" up -d --force-recreate keycloak mcp gateway

cat <<EOF

Quick Tunnel is running.

Public origin:  $url
MCP endpoint:   $url/mcp
OAuth issuer:  $url/realms/mcp

PUBLIC_BASE_URL in $ENV_FILE was updated automatically.
Update the MCP endpoint in ChatGPT whenever this script generates a new URL.
Quick Tunnels are temporary development endpoints; use the named tunnel profile for a stable deployment.
EOF
