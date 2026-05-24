# Jerry on Telegram — User Guide

Your personal AI assistant **Jerry** (@a_rslbot) runs on OpenClaw, reachable via Telegram and the web dashboard.

Need a clear answer to "do I run scripts myself from Telegram?" See [TELEGRAM_EXECUTION_GUIDE.md](TELEGRAM_EXECUTION_GUIDE.md).

---

## Quick Reference

| What | How |
|---|---|
| Talk to Jerry privately | DM @a_rslbot on Telegram |
| Talk in a group | @a_rslbot followed by your message |
| Web dashboard | `http://rsl/#token=fe823efd7110337d2c5ba4eec8cad41469a9c909ed643757` (Tailscale only) |
| Health check | `openclaw health` on the server |
| Restart | `~/.openclaw/scripts/safe-restart.sh` on the server |

---

## Groups

Each group has a focused persona. **@mention Jerry** in every group message — it won't respond without it.

| Group | Purpose | Example Uses |
|---|---|---|
| **EWAG** | Elite Wellness product work | Sprint planning, feature questions, delivery status |
| **GitHub Admin** | Repo ops and code review | PR reviews, issue triage, release management |
| **OpenClaw Admin** | Jerry's own config and health | Debug Jerry, change settings, check logs |

### DMs vs Groups

- **DMs** are private, persistent sessions scoped per-user. Good for personal tasks, email drafts, research.
- **Groups** keep context within that group's topic. EWAG context stays in EWAG; GitHub context stays in GitHub Admin.
- Never share private info (emails, tokens) in groups — Jerry is instructed to keep private data redacted in shared chats, but use DMs for anything sensitive.

---

## What Jerry Can Do

### Gmail
- Search your inbox: *"check my Gmail for anything from Taylor in the last 2 days"*
- Draft emails: *"draft an email to Taylor about the sprint review"*
- Send emails: *"send it"* (Jerry will preview and ask for confirmation first)
- The daily EWAG email at 8am ET is automatic — no action needed.

### Web Search (Brave)
- Just ask: *"search for React Native navigation best practices"*
- Jerry uses Brave Search automatically when a web lookup is needed.

### GitHub
- *"list open PRs on aarwitz/workspace"*
- *"create an issue for the login bug fix"*
- *"what's the status of the EWAG repo?"*

### Google Calendar
- *"what's on my calendar today?"*
- *"any meetings tomorrow?"*
- (Calendar API must be enabled in Google Cloud Console for this to work)

### File & Code Work
- Jerry can read and edit files in its workspace (`~/.openclaw/workspace/`)
- *"update the ELITE_KANBAN board with the new tasks"*
- *"add Taylor's notes to the meeting summary"*

### General Research
- *"explain the difference between REST and GraphQL"*
- *"summarize this error log: [paste]"*
- *"write a Python script to parse CSV files"*

---

## Tips for Best Results

1. **Be specific.** "Check Gmail for unread messages from Taylor about the invoice" works better than "check email."
2. **One task at a time** in groups. Chain requests in DMs.
3. **Use groups for their purpose.** EWAG for product, GitHub Admin for repos, OpenClaw Admin for Jerry config.
4. **For long content** (error logs, code blocks, docs), paste directly in the message — Jerry handles large inputs.
5. **Confirm before sends.** Jerry always previews outbound emails and asks before sending. If you say "send the daily email now," it will draft → show you → wait for your OK.
6. **Memory persists.** Jerry remembers context within a session. You can refer back to earlier messages: *"update that email draft to be shorter."*

---

## Daily Automation

**EWAG Daily Morning Brief** runs at **8:00 AM ET** every day:
- Checks your calendar for today and tomorrow
- Scans Gmail for urgent EWAG items from the last 24h
- Sends a summary email to Aaron + Taylor
- Posts a notification in the EWAG Telegram group

If nothing changed, it sends a short "no action items" email.

---

## Heartbeat

Jerry checks in every 30 minutes between **7:30 AM – 11:00 PM ET**. During heartbeats it:
- Updates the daily memory log
- Sends a Telegram alert only if something urgent changed
- Replies `HEARTBEAT_OK` if nothing notable happened

No late-night pings unless something is genuinely urgent.

---

## Common Commands (Server CLI)

Run these from the Ubuntu server over SSH or VS Code terminal:

```bash
# Status
openclaw health
openclaw gateway status
tailscale serve status

# Restart
~/.openclaw/scripts/safe-restart.sh

# Logs
openclaw logs

# Dashboard
openclaw dashboard --no-open

# Cron jobs
cat ~/.openclaw/cron/jobs.json | python3 -m json.tool | head -40
```

See [cli_guide.md](cli_guide.md) for full start/stop procedures.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Jerry doesn't respond in group | Make sure you @mentioned @a_rslbot |
| Jerry doesn't respond at all | SSH in, run `openclaw health` — if unhealthy, `~/.openclaw/scripts/safe-restart.sh` |
| Daily email didn't arrive | Check `openclaw health` and gateway logs (`openclaw logs`) — most likely the gateway was down at 8am |
| Dashboard won't load | Verify Tailscale is up (`tailscale status`) and serve is active (`tailscale serve status`) |
| "No TTY" or keyring errors | Gateway env vars issue — check `systemctl --user show openclaw-gateway -p Environment` |

---

## Security Notes

- The dashboard is **Tailscale-only** — no public internet exposure.
- Secrets (bot token, API keys) are in systemd env vars, not in Telegram messages.
- Jerry will never echo passwords or tokens in chat.
- Jerry asks for confirmation before sending emails, posting publicly, or deleting anything.
