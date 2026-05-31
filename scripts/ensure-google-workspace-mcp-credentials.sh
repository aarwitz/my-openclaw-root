#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

SRC="/home/aaron/.openclaw/credentials/google_client_secret.json"
DST_DIR="${HOME}/.google-mcp"
DST="${DST_DIR}/credentials.json"

if [[ ! -f "$SRC" ]]; then
  echo "Missing source credentials file: $SRC" >&2
  exit 1
fi

if ! jq -e '.installed.client_id and .installed.client_secret and .installed.redirect_uris' "$SRC" >/dev/null 2>&1; then
  echo "Source file does not look like Google OAuth desktop credentials: $SRC" >&2
  exit 1
fi

mkdir -p "$DST_DIR"
chmod 700 "$DST_DIR" || true
chmod 600 "$SRC" || true

if [[ -L "$DST" ]]; then
  target="$(readlink "$DST")"
  if [[ "$target" == "$SRC" ]]; then
    exit 0
  fi
fi

if [[ -f "$DST" && ! -L "$DST" ]]; then
  if cmp -s "$SRC" "$DST"; then
    rm -f "$DST"
  else
    ts="$(date +%Y%m%d-%H%M%S)"
    mv "$DST" "${DST}.bak.${ts}"
  fi
fi

ln -s "$SRC" "$DST"
