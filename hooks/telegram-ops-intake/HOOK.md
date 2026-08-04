---
name: telegram-ops-intake
description: "Persist every successful outbound Telegram message and route explicit warnings/failures to AutoTrade's durable work rail"
metadata: { "openclaw": { "emoji": "📥", "events": ["message:sent"], "requires": { "bins": ["python3"] } } }
---

# Telegram Operations Intake

Fires after successful Telegram delivery. Every message is written to the
append-only operator-event ledger. Explicit WARN/CRIT/FAILED messages are also
deduplicated into the AutoTrade priority queue; Dwight owns authenticated
promotion to Task Manager sprint 5. The hook never sends a Telegram message and
therefore cannot recurse.
