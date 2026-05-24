---
name: schwab
description: Charles Schwab Trader API (production) for account, positions, and order-ready data after OAuth authorization code exchange.
metadata: {"openclaw":{"emoji":"🏦","os":["linux"],"requires":{"bins":["curl","jq"]}}}
---

# Schwab

Use Schwab for brokerage account-aware workflows: account balances, positions, orders, and trade-ready account context. Use only after explicit OAuth authorization is completed.

## Credentials and app details

Source of truth: /home/aaron/.openclaw/credentials/schwab-dev-api.json

Expected keys in the credential file:
- Client ID
- Client Secret

Configured callback URL:
- https://127.0.0.1:8182/callback

Environment:
- Production

## Important auth model

Schwab uses OAuth authorization code flow. Client ID/secret alone are not enough for account endpoints.

Working validation signals:
- Token endpoint returns `invalid_request` or `invalid_grant` for fake/expired code: client credentials are recognized.
- Token endpoint returns `invalid_client`: client credentials are wrong.

## Auth bootstrap (manual)

1) Build an authorization URL:

```bash
CID="$(jq -r '."Client ID"' /home/aaron/.openclaw/credentials/schwab-dev-api.json)"
REDIRECT="https://127.0.0.1:8182/callback"
STATE="openclaw-schwab"
printf "https://api.schwabapi.com/v1/oauth/authorize?response_type=code&client_id=%s&redirect_uri=%s&state=%s\n" "$CID" "$(python3 - <<'PY'
import urllib.parse
print(urllib.parse.quote('https://127.0.0.1:8182/callback', safe=''))
PY
)" "$STATE"
```

2) Open the URL, approve, and capture the returned `code` from callback URL.

3) Exchange code for tokens:

```bash
CID="$(jq -r '."Client ID"' /home/aaron/.openclaw/credentials/schwab-dev-api.json)"
CSEC="$(jq -r '."Client Secret"' /home/aaron/.openclaw/credentials/schwab-dev-api.json)"
CODE="<paste authorization code>"
curl --compressed -s -u "$CID:$CSEC" \
  -d "grant_type=authorization_code&code=$CODE&redirect_uri=https%3A%2F%2F127.0.0.1%3A8182%2Fcallback" \
  https://api.schwabapi.com/v1/oauth/token | jq '.'
```

4) Save refresh/access token securely and use bearer auth for Schwab account endpoints.

## Safety rules

- Never place client secret in chat responses.
- Never place tokens in repository files.
- Do not place live orders unless Aaron explicitly asks.
- Confirm account and symbol before any order-related action.
