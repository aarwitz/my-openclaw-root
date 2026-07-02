#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()


"""Audit OpenClaw Telegram routing invariants.

This is a guardrail script for preventing config regressions where internal
transport keys (like account ids) are mistaken for bot identities.
"""


import json
from pathlib import Path

CFG = Path.home() / ".openclaw" / "openclaw.json"
MATRIX = Path.home() / ".openclaw" / "workspaces" / "trading-intel" / "reference" / "telegram_routing_matrix.json"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def main() -> int:
    if not CFG.exists():
        fail(f"config not found: {CFG}")
    if not MATRIX.exists():
        fail(f"routing matrix not found: {MATRIX}")

    cfg = json.loads(CFG.read_text())
    matrix = json.loads(MATRIX.read_text())

    agents = {a["id"] for a in cfg["agents"]["list"]}
    accounts = cfg["channels"]["telegram"]["accounts"]
    account_ids = set(accounts.keys())
    default_account = cfg["channels"]["telegram"].get("defaultAccount")
    matrix_default_account = matrix.get("defaultAccount")

    if default_account not in account_ids:
        fail(f"defaultAccount '{default_account}' is missing from channels.telegram.accounts")
    if default_account != matrix_default_account:
        fail(
            f"defaultAccount mismatch: config='{default_account}' matrix='{matrix_default_account}'"
        )

    # Naming contract: avoid ambiguous default transport key.
    if "default" in account_ids:
        fail("channels.telegram.accounts contains ambiguous key 'default'; use 'jerry' or explicit bot account ids")

    bindings = [
        b for b in cfg.get("bindings", []) if b.get("match", {}).get("channel") == "telegram"
    ]
    if not bindings:
        fail("no telegram bindings found")

    bound_accounts = {b["match"].get("accountId") for b in bindings}
    bound_agents = {b.get("agentId") for b in bindings}

    missing_accounts = sorted(a for a in bound_accounts if a not in account_ids)
    if missing_accounts:
        fail(f"telegram binding account ids missing in channels.telegram.accounts: {missing_accounts}")

    missing_agents = sorted(a for a in bound_agents if a not in agents)
    if missing_agents:
        fail(f"telegram binding agent ids missing in agents.list: {missing_agents}")

    # Jerry contract: 'jerry' account should route to the jerry agent (the
    # default assistant, formerly agent id 'main' — renamed; updated 2026-07-02).
    jerry_route = [
        b
        for b in bindings
        if b.get("match", {}).get("accountId") == "jerry"
        and b.get("agentId") in ("jerry", "main")
    ]
    if not jerry_route:
        fail("missing explicit telegram binding: accountId 'jerry' -> agentId 'jerry'")

    # Matrix DM routes must exist in bindings and accounts.
    matrix_dm_routes = matrix.get("dmRoutes", [])
    if not matrix_dm_routes:
        fail("routing matrix dmRoutes is empty")
    for route in matrix_dm_routes:
        account_id = route.get("accountId")
        agent_id = route.get("agentId")
        if account_id not in account_ids:
            fail(f"routing matrix accountId '{account_id}' not found in channels.telegram.accounts")
        if agent_id not in agents:
            fail(f"routing matrix agentId '{agent_id}' not found in agents.list")
        match = [
            b
            for b in bindings
            if b.get("match", {}).get("accountId") == account_id and b.get("agentId") == agent_id
        ]
        if not match:
            fail(f"missing binding for matrix dmRoute: accountId '{account_id}' -> agentId '{agent_id}'")

    groups = cfg["channels"]["telegram"].get("groups", {})
    matrix_group_routes = matrix.get("groupRoutes", [])
    if not matrix_group_routes:
        fail("routing matrix groupRoutes is empty")

    required_topics = []
    for group in matrix_group_routes:
        chat_id = group.get("chatId")
        if chat_id not in groups:
            fail(f"matrix group {chat_id} missing from channels.telegram.groups")
        cfg_topics = groups[chat_id].get("topics", {})
        for topic in group.get("topics", []):
            topic_id = topic.get("topicId")
            expected_agent = topic.get("agentId")
            cfg_topic = cfg_topics.get(topic_id)
            required_topics.append((chat_id, topic_id, expected_agent))
            if not cfg_topic:
                fail(f"group {chat_id} missing topic {topic_id}")
            if cfg_topic.get("agentId") != expected_agent:
                fail(
                    f"group {chat_id} topic {topic_id} routes to '{cfg_topic.get('agentId')}', expected '{expected_agent}'"
                )

    print("OK: telegram routing invariants satisfied")
    print(f"- defaultAccount: {default_account}")
    print(f"- bound accounts: {sorted(bound_accounts)}")
    print(f"- bound agents: {sorted(bound_agents)}")
    print(f"- matrix file: {MATRIX}")
    print(f"- required topics: {sorted(required_topics)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
