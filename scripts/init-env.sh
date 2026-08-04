#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-.env}"
ENV_TEMPLATE="${ENV_TEMPLATE:-.env.example}"

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ ! -f "$ENV_TEMPLATE" ]]; then
    echo "$ENV_TEMPLATE does not exist" >&2
    exit 1
  fi
  cp "$ENV_TEMPLATE" "$ENV_FILE"
  echo "Created $ENV_FILE from $ENV_TEMPLATE"
fi

get_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE"
}

set_value() {
  local key="$1"
  local value="$2"
  local tmp
  tmp="$(mktemp)"

  awk -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    index($0, key "=") == 1 {
      print key "=" value
      replaced = 1
      next
    }
    { print }
    END {
      if (!replaced) {
        print key "=" value
      }
    }
  ' "$ENV_FILE" > "$tmp"

  chmod --reference="$ENV_FILE" "$tmp" 2>/dev/null || true
  mv "$tmp" "$ENV_FILE"
}

generate_secret() {
  od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
}

for key in POSTGRES_PASSWORD KEYCLOAK_ADMIN_PASSWORD OAUTH_CLIENT_SECRET MCP_USER_PASSWORD; do
  value="$(get_value "$key")"
  case "$value" in
    ""|auto|AUTO|generate|GENERATE|replace-*)
      set_value "$key" "$(generate_secret)"
      echo "Generated $key"
      ;;
  esac
done

callback_url="$(get_value CHATGPT_CALLBACK_URL)"
if [[ -z "$callback_url" ]]; then
  set_value CHATGPT_CALLBACK_URL 'https://chatgpt.com/connector/oauth/*'
  callback_url='https://chatgpt.com/connector/oauth/*'
fi

if [[ "$callback_url" != https://chatgpt.com/connector/oauth/* ]]; then
  echo "CHATGPT_CALLBACK_URL must begin with https://chatgpt.com/connector/oauth/" >&2
  exit 1
fi

chmod 600 "$ENV_FILE" 2>/dev/null || true

echo "Environment ready: $ENV_FILE"
