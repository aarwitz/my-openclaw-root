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
from developer_db import audit, connect, emit, now_iso  # noqa: E402


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
    executed = None
    if new_status == "applied":
        # Execute the one proposal class that is a pure DB status flip (calibrate.py
        # files these on every posterior collapse — before 2026-07-28 'applied' only
        # recorded the decision and the mechanism stayed live until someone remembered
        # the UPDATE). Anything else still requires a code/coding-lane apply.
        import re
        # Second executable class (2026-07-29): new-mechanism proposals. Inserted
        # with the proposer's stated prior and status='candidate' — zero live
        # influence until calibration + the human-gated candidate->active promotion.
        try:
            pv = json.loads(str(row["proposed_value"] or ""))
        except (json.JSONDecodeError, TypeError):
            pv = None
        if isinstance(pv, dict) and pv.get("proposal_type") == "new_mechanism" and pv.get("mechanism_id"):
            mid = str(pv["mechanism_id"])
            if conn.execute("SELECT 1 FROM mechanisms WHERE id=?", (mid,)).fetchone():
                executed = {"error": f"mechanism {mid} already exists — decision recorded only"}
            else:
                conn.execute(
                    "INSERT INTO mechanisms (id, created_at, created_by, name, antecedent_class, "
                    "transmission_chain_json, consequent_class, direction, horizon, regime_context, "
                    "prior_alpha, prior_beta, status, notes, experiment_id) "
                    "VALUES (?, ?, 'overseer', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, 'world_model_v1')",
                    (mid, now_iso(), pv.get("name") or mid,
                     pv.get("antecedent_class"), json.dumps(pv.get("transmission_chain") or []),
                     pv.get("consequent_class") or pv.get("antecedent_class"),
                     # schema CHECK allows long/short/neutral/risk_off/risk_on only
                     {"short_or_reduce_long": "short", "long_or_add": "long"}.get(
                         str(pv.get("direction")),
                         pv.get("direction") if pv.get("direction") in
                         ("long", "short", "neutral", "risk_off", "risk_on") else "neutral"),
                     pv.get("horizon"), pv.get("regime_context"),
                     float(pv.get("prior_alpha") or 1.0), float(pv.get("prior_beta") or 1.0),
                     f"created by rule_proposal {proposal_id}"))
                audit(conn, actor="developer", entity_type="mechanism", entity_id=mid,
                      action="create_candidate", before_state=None, after_state="candidate",
                      rationale=f"executed by rule_proposal {proposal_id}")
                executed = {"mechanism": mid, "status": "created as candidate"}
        m = re.fullmatch(r"mechanisms\.(.+)\.status", str(row["target_artifact"] or ""))
        if m and str(row["proposed_value"]) in ("deprecated", "active", "candidate"):
            mid, new_val = m.group(1), str(row["proposed_value"])
            cur = conn.execute("SELECT status FROM mechanisms WHERE id=?", (mid,)).fetchone()
            if cur:
                conn.execute("UPDATE mechanisms SET status=? WHERE id=?", (new_val, mid))
                audit(conn, actor="developer", entity_type="mechanism", entity_id=mid,
                      action="status_change", before_state=cur["status"], after_state=new_val,
                      rationale=f"executed by rule_proposal {proposal_id}")
                executed = {"mechanism": mid, "status": f"{cur['status']} -> {new_val}"}
            else:
                executed = {"error": f"mechanism {mid} not found — decision recorded, nothing executed"}
    conn.commit()
    return {"id": proposal_id, "from": before, "to": new_status,
            "target_artifact": row["target_artifact"],
            "proposed_value": row["proposed_value"],
            **({"executed": executed} if executed else {})}


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
