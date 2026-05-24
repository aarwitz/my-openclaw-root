# Google Workspace MCP active cutover

Active MCP config entry:
- /home/aaron/.openclaw/.vscode/mcp.json -> google-workspace-readonly

Runtime command:
- /home/aaron/.openclaw/scripts/start-google-workspace-mcp-readonly.sh

Current mode:
- Read-only (`serve --read-only`)

## One-time setup required

The server expects:
- credentials file at `~/.google-mcp/credentials.json`
- at least one configured account

Credential source of truth:
- Keep credentials in `/home/aaron/.openclaw/credentials/google_client_secret.json`
- Bridge script `/home/aaron/.openclaw/scripts/ensure-google-workspace-mcp-credentials.sh` auto-links to `~/.google-mcp/credentials.json`

Status check:

```bash
npx -y google-workspace-mcp status
```

If status reports missing credentials:

1. Create Google OAuth desktop app credentials in Google Cloud.
2. Save the JSON to:

```bash
/home/aaron/.openclaw/credentials/google_client_secret.json
```

3. Run the bridge once (or let setup/start scripts run it automatically):

```bash
/home/aaron/.openclaw/scripts/ensure-google-workspace-mcp-credentials.sh
```

4. Add accounts (opens browser OAuth flow):

```bash
/home/aaron/.openclaw/scripts/setup-google-workspace-mcp.sh --account main
/home/aaron/.openclaw/scripts/setup-google-workspace-mcp.sh --account resi
/home/aaron/.openclaw/scripts/setup-google-workspace-mcp.sh --account dwight
/home/aaron/.openclaw/scripts/setup-google-workspace-mcp.sh --account druck
```

5. Re-run status:

```bash
npx -y google-workspace-mcp status
```

## Safety policy

- Keep `google-workspace-readonly` until account auth is stable for at least 7 days.
- Keep gog scripts available as fallback during this stabilization window.
- Do not enable write-mode MCP until read-only checks are consistently green.
