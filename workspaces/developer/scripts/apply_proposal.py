#!/usr/bin/env python3
"""Bessent · apply_proposal.py

Manage `rule_proposals` lifecycle. Lists proposed rules, lets a human or
developer moves them through approved → applied, and writes audits.

Usage:
  python3 apply_proposal.py --list
  python3 apply_proposal.py --apply RULE-PROP-ID --decider human
  python3 apply_proposal.py --reject RULE-PROP-ID --decider human --reason "..."

Applying does NOT execute the underlying change in any artifact file — it
records the decision in the DB and emits the change spec for a human to
review and commit. (Auto-apply would conflict with sandbox=off + reversibility.)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import audit, connect, emit, now_iso  # noqa: E402


def list_proposed(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, created_at, proposer, target_artifact, current_value, "
        "proposed_value, rationale, status FROM rule_proposals "
        "WHERE status='proposed' ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def _set_status(conn, proposal_id: str, *, new_status: str, decider: str,
                reason: str | None = None) -> dict:
    row = conn.execute(
        "SELECT id, target_artifact, current_value, proposed_value, status "
        "FROM rule_proposals WHERE id=?", (proposal_id,)
    ).fetchone()
    if not row:
        return {"error": "proposal_not_found", "id": proposal_id}
    before = row["status"]
    ts = now_iso()
    applied_at = ts if new_status == "applied" else None
    conn.execute(
        "UPDATE rule_proposals SET status=?, decided_by=?, decided_at=?, applied_at=? "
        "WHERE id=?",
        (new_status, decider, ts, applied_at, proposal_id),
    )
    audit(conn, actor="developer", entity_type="rule_proposal", entity_id=proposal_id,
          action=new_status, before_state=before, after_state=new_status,
          rationale=(reason or f"decider={decider}")[:480])
    conn.commit()
    return {"id": proposal_id, "from": before, "to": new_status,
            "target_artifact": row["target_artifact"],
            "proposed_value": row["proposed_value"]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--apply", metavar="ID")
    g.add_argument("--approve", metavar="ID")
    g.add_argument("--reject", metavar="ID")
    p.add_argument("--decider", default="developer")
    p.add_argument("--reason", default=None)
    args = p.parse_args(argv)

    conn = connect()
    if args.list:
        emit({"proposed": list_proposed(conn)})
        return 0
    if args.approve:
        emit(_set_status(conn, args.approve, new_status="approved", decider=args.decider, reason=args.reason))
        return 0
    if args.apply:
        emit(_set_status(conn, args.apply, new_status="applied", decider=args.decider, reason=args.reason))
        return 0
    if args.reject:
        emit(_set_status(conn, args.reject, new_status="rejected", decider=args.decider, reason=args.reason))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
