---
name: gog
description: Google Workspace CLI for Gmail, Calendar, Drive, Contacts, Sheets, and Docs.
homepage: https://gogcli.sh
metadata: {"clawdbot":{"emoji":"🎮","requires":{"bins":["gog"]},"install":[{"id":"brew","kind":"brew","formula":"steipete/tap/gogcli","bins":["gog"],"label":"Install gog (brew)"}]}}
---

# gog

Use `gog` for Gmail/Calendar/Drive/Contacts/Sheets/Docs. Requires OAuth setup.

## Mandatory wrapper (deterministic)

All bot workflows must call gog through:

```bash
/home/aaron/.openclaw/scripts/gog-account-router.sh --agent <main|resi|dwight|druck> <gog args...>
```

This enforces:
- deterministic account routing by agent
- `--no-input` for non-interactive execution
- fast-fail auth/service probes before risky operations

Examples:

```bash
/home/aaron/.openclaw/scripts/gog-account-router.sh --agent main drive search "owner:me" --max 5
/home/aaron/.openclaw/scripts/gog-account-router.sh --agent dwight gmail search "newer_than:7d" --max 10
/home/aaron/.openclaw/scripts/gog-account-router.sh --agent main --print-account
```

Current default account mapping:
- `main` -> `aaronclawrsl@gmail.com`
- `resi` -> `aaronclawrsl@gmail.com`
- `dwight` -> `aaronclawrsl@gmail.com`
- `druck` -> `aaronclawrsl@gmail.com`

Setup (once)
- `gog auth credentials /path/to/client_secret.json`
- `gog auth add you@gmail.com --services gmail,calendar,drive,contacts,sheets,docs`
- `gog auth list`

Session reality
- Re-auth does not rewrite the OAuth client JSON file.
- The client secret JSON is only app configuration; gog stores renewable account sessions separately.
- Use `gog auth list --check --no-input` to validate the live session before relying on Google services in an automation chain.
- If Google returns `invalid_grant`, re-run `gog auth manage --services <service>` and retry the blocked command.

Common commands
- Gmail search: `gog gmail search 'newer_than:7d' --max 10`
- Gmail send: `gog gmail send --to a@b.com --subject "Hi" --body "Hello"`
- Calendar: `gog calendar events <calendarId> --from <iso> --to <iso>`
- Drive search: `gog drive search "query" --max 10`
- Contacts: `gog contacts list --max 20`
- Sheets get: `gog sheets get <sheetId> "Tab!A1:D10" --json`
- Sheets update: `gog sheets update <sheetId> "Tab!A1:B2" --values-json '[["A","B"],["1","2"]]' --input USER_ENTERED`
- Sheets append: `gog sheets append <sheetId> "Tab!A:C" --values-json '[["x","y","z"]]' --insert INSERT_ROWS`
- Sheets clear: `gog sheets clear <sheetId> "Tab!A2:Z"`
- Sheets metadata: `gog sheets metadata <sheetId> --json`
- Docs export: `gog docs export <docId> --format txt --out /tmp/doc.txt`
- Docs cat: `gog docs cat <docId>`

Notes
- Set `GOG_ACCOUNT=you@gmail.com` to avoid repeating `--account`.
- For scripting, prefer `--json` plus `--no-input`.
- Before a Drive upload, run a cheap probe such as `gog drive search 'owner:me' --max 1 --no-input`.
- Before Gmail/calendar actions, run a matching cheap probe instead of discovering auth drift after composing work.
- Sheets values can be passed via `--values-json` (recommended) or as inline rows.
- Docs supports export/cat/copy. In-place edits require a Docs API client (not in gog).
- Confirm before sending mail or creating events.
