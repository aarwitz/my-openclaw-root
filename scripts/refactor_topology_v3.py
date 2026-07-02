#!/usr/bin/env python3
"""Phase E topology refactor of openclaw.json.

Changes:
  1. agents.defaults.subagents.allowAgents: drop bessent, add developer + overseer
  2. agents.list:
       - rename bessent -> developer
            workspace -> /home/aaron/.openclaw/workspaces/developer
            identity.name -> "Developer", emoji -> "wrench"
            subagents.allowAgents -> archivist, dwight (kept)
       - add overseer
            workspace -> /home/aaron/.openclaw/workspaces/overseer
            groupChat.mentionPatterns -> ["@druck_rsl_bot\\b"]
            skills: druck-research, github-ssh, task-manager
            subagents.allowAgents -> researcher, quant, critic, trader, executor, archivist, developer
            identity.name -> "AutoTrade"
       - trader:
            remove groupChat (overseer owns the chat front door)
            remove humanDelay (pipeline lane, no typing delay)
            identity.name -> "Trader"
            subagents.allowAgents -> executor, researcher, quant, critic, archivist, developer
              (no overseer to avoid a back-call loop; no bessent)
  3. bindings (top-level): druck account -> overseer (was trader)
  4. channels.telegram.groups["-1003846579956"].topics["641"]:
       agentId -> overseer
       systemPrompt -> new AutoTrade overseer prompt
  5. channels.telegram.groups["-1003846579956"] group-level systemPrompt:
       replace "Druck chat" -> "AutoTrade chat"
  6. channels.telegram.groups["-1003846579956"].topics["1"] (Dwight oversight):
       replace "@druck_rsl_bot" cross-bot references to call overseer by name
       in the directive template (text edit only).

Idempotent: rerun is a no-op aside from a fresh backup.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper

require_wrapper()

import json
import shutil
import time
from pathlib import Path

CFG = Path("/home/aaron/.openclaw/openclaw.json")
BACKUP = CFG.with_suffix(f".json.pre-phase-e.{int(time.time())}.bak")
RUN_WITH_TRACE = "~/.openclaw/scripts/run-with-trace.sh"
OVERSEER_SCRIPTS = "/home/aaron/.openclaw/workspaces/overseer/scripts"
PQ_LIST_CMD = f"{RUN_WITH_TRACE} {OVERSEER_SCRIPTS}/pq_list.py"
PQ_PROMOTE_CMD = f"{RUN_WITH_TRACE} {OVERSEER_SCRIPTS}/pq_promote.py <id>"

DEVELOPER_AGENT = {
    "id": "developer",
    "workspace": "/home/aaron/.openclaw/workspaces/developer",
    "model": "openai/gpt-5.4",
    "verboseDefault": "full",
    "toolProgressDetail": "raw",
    "skills": ["github-ssh", "druck-research"],
    "subagents": {
        "delegationMode": "prefer",
        "allowAgents": ["archivist", "dwight"],
    },
    "identity": {"name": "Developer", "emoji": "\U0001F6E0\uFE0F"},
}

OVERSEER_SYSTEM_PROMPT = (
    "You are AutoTrade, the single chat front door + cron orchestrator + queue\n"
    "manager for the Trading Intelligence stack. You speak as @druck_rsl_bot in\n"
    "Telegram group -1003846579956 (topic 641). You do not have a human name.\n"
    "\n"
    "Authority:\n"
    "- /home/aaron/.openclaw/workspaces/overseer/AGENTS.md\n"
    "- /home/aaron/.openclaw/workspaces/trading-intel/docs/01_OPERATING_AUTHORITY.md\n"
    "- /home/aaron/.openclaw/workspaces/trading-intel/docs/02_ARCHITECTURE.md\n"
    "- /home/aaron/.openclaw/workspaces/trading-intel/docs/03_EXECUTION_STATE_MACHINE.md\n"
    "- /home/aaron/.openclaw/workspaces/trading-intel/docs/04_SHARED_STATE_SCHEMA.md\n"
    "- /home/aaron/.openclaw/workspaces/trading-intel/docs/05_IMPLEMENTATION_POLICY.md\n"
    "- /home/aaron/.openclaw/workspaces/trading-intel/DECISION_LOG.md\n"
    "\n"
    "Role (Topology v3):\n"
    "- You orchestrate the deterministic pipeline R -> Q -> C -> T -> E.\n"
    "  - researcher generates hypotheses, quant scores them, critic challenges\n"
    "    them, trader turns ready hypotheses into trade_intents, executor places\n"
    "    orders via Alpaca. archivist runs an async learning pass.\n"
    "- You never write to execution-state tables directly; you only spawn the\n"
    "  agents that own those writes.\n"
    "- You never edit scripts/schema/connectors. For that you delegate to\n"
    "  developer (the autonomous engineering lane).\n"
    "- For Task Manager / RSL issues you delegate to dwight.\n"
    "\n"
    "Chat commands (natural language, no slash required):\n"
    "- queue -> read /home/aaron/.openclaw/state/priority-queue.jsonl and show\n"
    f"  open rows by priority. Use {PQ_LIST_CMD}\n"
    "- run <pass> -> trigger /home/aaron/.openclaw/scripts/run-with-trace.sh --tag chat /home/aaron/.openclaw/scripts/trader-pass-deterministic.sh\n"
    "  Append --publish only on Aaron's explicit request.\n"
    "- status -> read the latest data.json snapshot at /home/aaron/repos/lidi-solutions/public/solutions/trader_intel/app/data.json\n"
    "  and summarize regime, hypothesis counts, and any pipeline_health issues.\n"
    "- promote <id> -> move a priority-queue row to dwight (task-manager).\n"
    f"  Use {PQ_PROMOTE_CMD}\n"
    "\n"
    "Protocol:\n"
    "- @druck_rsl_bot or @autotrade -> reply expected unless prefixed FYI/cc.\n"
    "- Never use markdown tables or pipe-separated rows in Telegram replies.\n"
    "- Never use fenced code blocks unless explicitly requested.\n"
    "- Keep messages short, source-backed, action-first.\n"
)

OVERSEER_AGENT = {
    "id": "overseer",
    "workspace": "/home/aaron/.openclaw/workspaces/overseer",
    "model": "openai/gpt-5.4",
    "verboseDefault": "full",
    "toolProgressDetail": "raw",
    "humanDelay": {"mode": "custom", "minMs": 1200, "maxMs": 2400},
    "groupChat": {"mentionPatterns": ["@druck_rsl_bot\\b"]},
    "skills": [
        "druck-research",
        "github-ssh",
        "task-manager",
        "newsapi-ai",
        "finnhub",
        "massive",
        "financialmodeling-prep-api",
    ],
    "subagents": {
        "delegationMode": "prefer",
        "allowAgents": [
            "researcher",
            "quant",
            "critic",
            "trader",
            "executor",
            "archivist",
            "developer",
            "dwight",
        ],
    },
    "identity": {"name": "AutoTrade", "emoji": "\U0001F916"},
}

TOPIC_641_SYSTEM_PROMPT = (
    "You are AutoTrade (agent id overseer) in Trading Desk topic 641. You are\n"
    "the chat front door + pipeline orchestrator. You do not have a human name.\n"
    "\n"
    "Pipeline order is strict: researcher -> quant -> critic -> trader -> executor.\n"
    "archivist runs as an async learning pass. developer owns scripts/schema/\n"
    "connectors. dwight owns task-manager.\n"
    "\n"
    "Chat behavior:\n"
    "- @druck_rsl_bot -> reply expected.\n"
    "- FYI/cc -> listen-only.\n"
    "- No markdown tables, no pipe-separated rows, no fenced code blocks unless\n"
    "  explicitly requested.\n"
    "- For trade execution: spawn trader to mint intents, then spawn executor.\n"
    "- For dev/infra changes: spawn developer.\n"
    "- For task manager work: spawn dwight.\n"
    "- For status: read data.json at\n"
    "  /home/aaron/repos/lidi-solutions/public/solutions/trader_intel/app/data.json\n"
    "- For the priority queue: use scripts under\n"
    "  /home/aaron/.openclaw/workspaces/overseer/scripts/\n"
)


def main() -> int:
    cfg = json.loads(CFG.read_text())
    shutil.copy2(CFG, BACKUP)
    print(f"backup: {BACKUP}")

    # ----- 1. defaults.subagents.allowAgents -----
    defaults = cfg["agents"]["defaults"]["subagents"]
    new_allow = [
        "researcher", "quant", "critic", "archivist",
        "trader", "executor", "developer", "overseer",
    ]
    defaults["allowAgents"] = new_allow

    # ----- 2. agents.list -----
    agents = cfg["agents"]["list"]
    # 2a: rename bessent -> developer
    found_bessent = False
    for i, a in enumerate(agents):
        if a.get("id") == "bessent":
            agents[i] = DEVELOPER_AGENT
            found_bessent = True
            print("renamed bessent -> developer")
            break
    if not found_bessent and not any(a.get("id") == "developer" for a in agents):
        agents.append(DEVELOPER_AGENT)
        print("added developer (no bessent to rename)")

    # 2b: trader cleanup
    for a in agents:
        if a.get("id") == "trader":
            a.pop("groupChat", None)
            a.pop("humanDelay", None)
            a["identity"] = {"name": "Trader", "emoji": "\U0001F4B0"}
            a["subagents"] = {
                "delegationMode": "prefer",
                "allowAgents": [
                    "executor", "researcher", "quant",
                    "critic", "archivist", "developer",
                ],
            }
            print("trader: stripped groupChat/humanDelay; subagents = pipeline-only")
            break

    # 2c: add overseer if missing
    if not any(a.get("id") == "overseer" for a in agents):
        agents.append(OVERSEER_AGENT)
        print("added overseer")
    else:
        for i, a in enumerate(agents):
            if a.get("id") == "overseer":
                agents[i] = OVERSEER_AGENT
        print("overseer agent definition refreshed")

    # ----- 3. bindings (top-level) -----
    for b in cfg.get("bindings", []):
        if b.get("match", {}).get("accountId") == "druck":
            old = b.get("agentId")
            b["agentId"] = "overseer"
            print(f"binding: druck account {old} -> overseer")

    # ----- 4. trading-intel group topic 641 -----
    groups = cfg["channels"]["telegram"]["groups"]
    g = groups.get("-1003846579956")
    if g is None:
        raise SystemExit("trading-intel group -1003846579956 missing!")
    topics = g.setdefault("topics", {})
    topic_641 = topics.setdefault("641", {})
    topic_641["agentId"] = "overseer"
    topic_641["skills"] = [
        "newsapi-ai", "finnhub", "massive", "schwab", "alpaca",
        "gog", "github-ssh", "druck-research",
        "financialmodeling-prep-api", "task-manager",
    ]
    topic_641["systemPrompt"] = TOPIC_641_SYSTEM_PROMPT
    print("topic 641: routed to overseer with new system prompt")

    # ----- 5. group-level systemPrompt rewrite -----
    gp = g.get("systemPrompt", "")
    if "Druck chat" in gp:
        g["systemPrompt"] = gp.replace(
            "Druck chat and orchestration in topic 641",
            "AutoTrade chat and orchestration in topic 641 (agent id overseer)",
        )
        print("group -1003846579956: systemPrompt rewritten")

    CFG.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"wrote {CFG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
