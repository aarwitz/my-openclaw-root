#!/usr/bin/env python3
"""file_rule_proposal.py — deterministic CLI to file a rule_proposal row.

The slow/human-gated learning lane (invariant #4: agents never self-approve)
had no filing tool — proposals were hand-inserted or written only by
calibrate.py. This makes filing auditable and uniform: one row in
`rule_proposals` (status='proposed') + one `audits` row. Approval/apply remain
human steps.

Usage:
    python3 file_rule_proposal.py --id rp-my-change-YYYYMMDD \
        --target-artifact workspaces/trader/scripts/author_intents.py \
        --current-value '...' --proposed-value '...' \
        --rationale '...' --evidence '["ref1","ref2"]' [--proposer overseer]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/home/aaron/.openclaw/state/trading-intel.sqlite")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--id", required=True)
    p.add_argument("--target-artifact", required=True)
    p.add_argument("--current-value", required=True)
    p.add_argument("--proposed-value", required=True)
    p.add_argument("--rationale", required=True)
    p.add_argument("--evidence", default="[]", help="JSON array of evidence refs")
    p.add_argument("--proposer", default="overseer")
    p.add_argument("--experiment-id", default="world_model_v1")
    args = p.parse_args(argv)

    try:
        evidence = json.loads(args.evidence)
        assert isinstance(evidence, list)
    except (ValueError, AssertionError):
        print(json.dumps({"error": "--evidence must be a JSON array"}), file=sys.stderr)
        return 2

    conn = sqlite3.connect(DB_PATH, timeout=30)
    if conn.execute("SELECT 1 FROM rule_proposals WHERE id=?", (args.id,)).fetchone():
        print(json.dumps({"error": f"proposal {args.id} already exists"}), file=sys.stderr)
        return 2
    now = _now()
    conn.execute(
        "INSERT INTO rule_proposals (id, created_at, proposer, target_artifact, "
        "current_value, proposed_value, rationale, evidence_refs_json, status, experiment_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)",
        (args.id, now, args.proposer, args.target_artifact, args.current_value,
         args.proposed_value, args.rationale, json.dumps(evidence), args.experiment_id))
    aid = "AUDIT-" + now.replace(":", "").replace("-", "") + "-" + args.id[:24]
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise, experiment_id) "
        "VALUES (?, ?, ?, 'rule_proposal', ?, 'propose', NULL, 'proposed', ?, ?)",
        (aid, now, args.proposer, args.id, args.rationale[:380], args.experiment_id))
    conn.commit()
    print(json.dumps({"filed": args.id, "status": "proposed", "audit": aid}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
