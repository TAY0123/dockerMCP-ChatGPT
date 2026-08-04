#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-.env}"
TIMEOUT_SECONDS="${QUICK_TUNNEL_TIMEOUT_SECONDS:-90}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required" >&2
  exit 1
fi

ENV_FILE="$ENV_FILE" bash "$SCRIPT_DIR/init-env.sh"
compose=(docker compose --env-file "$ENV_FILE")

get_env_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE"
}

show_startup_diagnostics() {
  echo >&2
  echo "Service startup failed. Current status:" >&2
  "${compose[@]}" ps >&2 || true
  echo >&2
  echo "Recent service logs:" >&2
  "${compose[@]}" logs --no-color --tail=200 runner keycloak mcp gateway >&2 || true
}

# Start the application locally before requesting a temporary hostname.
if ! "${compose[@]}" up -d --build postgres keycloak runner mcp gateway; then
  show_startup_diagnostics
  exit 1
fi

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

# Recreate URL-sensitive services so MCP metadata, OAuth issuer URLs,
# hostname validation, and Keycloak use the temporary hostname.
if ! "${compose[@]}" up -d --force-recreate keycloak mcp gateway; then
  show_startup_diagnostics
  exit 1
fi

client_id="$(get_env_value OAUTH_CLIENT_ID)"
client_secret="$(get_env_value OAUTH_CLIENT_SECRET)"
scopes="openid profile email offline_access $(get_env_value OAUTH_REQUIRED_SCOPES)"
mcp_user="$(get_env_value MCP_USER)"
mcp_password="$(get_env_value MCP_USER_PASSWORD)"

cat <<EOF

Quick Tunnel is running.

MCP endpoint:    $url/mcp
OAuth issuer:   $url/realms/mcp
Client ID:      $client_id
Client secret:  $client_secret
Scopes:         $scopes
Login user:     $mcp_user
Login password: $mcp_password

The generated settings are stored in $ENV_FILE with mode 600.
Enter the MCP endpoint and OAuth values above in ChatGPT.
The callback wildcard in the default template is for testing only; replace it
with ChatGPT's exact callback URL for a stable deployment.

Quick Tunnel URLs change when cloudflared is recreated. Use the named tunnel
profile when the ChatGPT MCP endpoint must remain stable.
EOF
