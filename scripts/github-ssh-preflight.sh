#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

KEY="/home/aaron/.ssh/id_rsa"
PUB="/home/aaron/.ssh/id_rsa.pub"
CFG="/home/aaron/.ssh/config"

fail() {
  echo "[github-ssh-preflight] ERROR: $*" >&2
  exit 1
}

[[ -f "$KEY" ]] || fail "Missing private key: $KEY"
[[ -f "$PUB" ]] || fail "Missing public key: $PUB"
[[ -f "$CFG" ]] || fail "Missing SSH config: $CFG"

perm_key="$(stat -c '%a' "$KEY")"
perm_cfg="$(stat -c '%a' "$CFG")"
[[ "$perm_key" == "600" ]] || fail "Private key permissions must be 600 (got $perm_key)"
[[ "$perm_cfg" == "600" || "$perm_cfg" == "644" ]] || fail "SSH config permissions should be 600/644 (got $perm_cfg)"

if ! rg -q '^Host github\.com$' "$CFG"; then
  fail "SSH config missing 'Host github.com' block"
fi

if ! ssh -T git@github.com -o BatchMode=yes -o ConnectTimeout=10 >/tmp/github_ssh_preflight.out 2>&1; then
  # ssh -T exits 1 on successful auth for GitHub because shell access is denied; treat auth messages as success.
  if ! rg -q "successfully authenticated|Hi .* You've successfully authenticated" /tmp/github_ssh_preflight.out; then
    cat /tmp/github_ssh_preflight.out >&2
    fail "GitHub SSH auth failed"
  fi
fi

# Verify git transport rewrite is active and reachable with HTTPS-form URL.
if ! git ls-remote https://github.com/aarwitz/lidi-solutions.git HEAD >/tmp/github_ssh_lsremote.out 2>&1; then
  cat /tmp/github_ssh_lsremote.out >&2
  fail "git ls-remote via HTTPS-form URL failed (rewrite to SSH may be missing)"
fi

echo "[github-ssh-preflight] OK: GitHub SSH auth and URL rewrite are healthy"
