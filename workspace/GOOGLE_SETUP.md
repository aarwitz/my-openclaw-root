# GOOGLE_SETUP.md

This guide enables OpenClaw to use Gmail and Google Drive via the `gog` skill.

## Important

Do not use plain account passwords for API automation. Gmail/Drive access here uses OAuth client credentials from Google Cloud.

## One-Time Google Cloud Setup

1. Open Google Cloud Console.
2. Create/select a project.
3. Enable APIs:
   - Gmail API
   - Google Drive API
   - Google Docs API (optional but recommended)
   - Google Sheets API (optional)
4. Configure OAuth consent screen.
5. Create OAuth client credentials of type Desktop app.
6. Download the JSON file, for example:
   - `/home/aaron/.openclaw/credentials/google_client_secret.json`

## One-Time CLI Auth Setup

Run:

```bash
gog auth credentials /home/aaron/.openclaw/credentials/google_client_secret.json
gog auth add aaronclawrsl@gmail.com --services gmail,drive,docs,sheets,calendar,contacts
gog auth list
```

Optional default account:

```bash
echo 'export GOG_ACCOUNT=aaronclawrsl@gmail.com' >> ~/.bashrc
source ~/.bashrc
```

## Verify Access

```bash
gog gmail search 'newer_than:2d' --max 5
gog drive search "owner:me" --max 5
```

## Safe Operations Pattern

- Draft first, send second.
- Confirm recipients before send.
- Confirm file share/delete operations before execute.

## Example Email Draft + Send

```bash
gog gmail drafts create \
  --to person@example.com \
  --subject "Subject" \
  --body-file ./message.txt

gog gmail drafts send <draftId>
```

## Example Drive Save

Use Google Docs or Drive operations through `gog` commands after auth.

Tip: Keep OAuth JSON and resulting tokens outside project repos.
