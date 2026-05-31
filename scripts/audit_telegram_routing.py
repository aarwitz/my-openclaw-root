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
TRADING_GROUP_ID = "-1003846579956"
REQUIRED_TRADING_TOPICS = {
    "1": "dwight",
    "641": "trader",
}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def main() -> int:
    if not CFG.exists():
        fail(f"config not found: {CFG}")

    cfg = json.loads(CFG.read_text())

    agents = {a["id"] for a in cfg["agents"]["list"]}
    accounts = cfg["channels"]["telegram"]["accounts"]
    account_ids = set(accounts.keys())
    default_account = cfg["channels"]["telegram"].get("defaultAccount")

    if default_account not in account_ids:
        fail(f"defaultAccount '{default_account}' is missing from channels.telegram.accounts")

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

    # Jerry contract: 'jerry' account should route to main agent.
    jerry_route = [
        b
        for b in bindings
        if b.get("match", {}).get("accountId") == "jerry" and b.get("agentId") == "main"
    ]
    if not jerry_route:
        fail("missing explicit telegram binding: accountId 'jerry' -> agentId 'main'")

    groups = cfg["channels"]["telegram"].get("groups", {})
    if TRADING_GROUP_ID not in groups:
        fail(f"trading group {TRADING_GROUP_ID} missing from channels.telegram.groups")

    trading_topics = groups[TRADING_GROUP_ID].get("topics", {})
    for topic_id, expected_agent in REQUIRED_TRADING_TOPICS.items():
        topic = trading_topics.get(topic_id)
        if not topic:
            fail(f"trading group missing topic {topic_id}")
        if topic.get("agentId") != expected_agent:
            fail(
                f"trading topic {topic_id} routes to '{topic.get('agentId')}', expected '{expected_agent}'"
            )

    print("OK: telegram routing invariants satisfied")
    print(f"- defaultAccount: {default_account}")
    print(f"- bound accounts: {sorted(bound_accounts)}")
    print(f"- bound agents: {sorted(bound_agents)}")
    print(f"- trading topics: {sorted(REQUIRED_TRADING_TOPICS.items())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
