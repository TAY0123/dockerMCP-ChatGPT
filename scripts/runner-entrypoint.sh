#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  password="${ROOT_PASSWORD:-root}"

  if [[ -z "$password" ]]; then
    echo "ROOT_PASSWORD must not be empty" >&2
    exit 1
  fi

  if [[ "$password" == *$'\n'* || "$password" == *:* ]]; then
    echo "ROOT_PASSWORD must not contain a newline or colon" >&2
    exit 1
  fi

  printf 'root:%s\n' "$password" | chpasswd
fi

exec /usr/bin/tini -- "$@"
