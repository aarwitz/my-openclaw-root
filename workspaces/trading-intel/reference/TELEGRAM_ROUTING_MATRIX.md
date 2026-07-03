# Telegram Routing Matrix (Canonical)

Status: authoritative
Machine source: reference/telegram_routing_matrix.json

## Surface definitions

- DM: direct 1:1 chat with a bot account; no group chat id and no topic id.
- Group: a Telegram chat room (supergroup/forum) identified by chat id.
- Topic: a thread within a forum-enabled group; topic ids are only valid inside that group.

## DM routes

| Surface | Account ID | Bot handle | Agent ID | Persona | Primary use |
|---|---|---|---|---|---|
| DM | jerry | @jerry_rsl_bot | main | Jerry | General OpenClaw operations and health checks |
| DM | dwight | @dwight_rsl_bot | dwight | Dwight | Oversight and task execution status |
| DM | druck | @druck_rsl_bot | trader | Druck | Chat front door and orchestration |

## Group and topic routes

| Surface | Group name | Chat ID | Topic name | Topic ID | Bot handle | Agent ID | Mention required |
|---|---|---|---|---|---|---|---|
| Group topic | Trading Desk | -1003846579956 | Infrastructure | 1 | @dwight_rsl_bot | dwight | yes |
| Group topic | Trading Desk | -1003846579956 | Ask Druck | 641 | @druck_rsl_bot | trader | yes |

## Rules

- Topic ids are scoped to their group chat id; never treat a topic id as globally unique.
- DM routing uses account bindings, not group/topic routing.
- In group topics, include explicit @mention to trigger mention-gated agents.
- Routing changes must update reference/telegram_routing_matrix.json and pass scripts/audit_telegram_routing.py.
