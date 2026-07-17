#!/usr/bin/env python3
"""Gate Evaluator — deterministic critic-stage gate stack.

Runs the 10 production gates on a trade_intent row and writes back the per-gate
columns plus an updated state. This is the deterministic core of the critic
review path; LLM critic commentary (challenges) is layered on separately.

Gates (in order; fail-fast within a category):
  1. regime_gate                 — current regime must allow new exposure
  2. evidence_freshness          — all evidence rows < freshness_max_hours
  3. factor_overlap              — overlap_pct vs open positions < threshold
  4. provenance_completeness     — % of evidence with non-null source_url
  5. counterargument_quality     — critic_reviews.challenges count + addressed
  6. explainability              — rationale_concise present + non-trivial
  7. size_sanity                 — size <= max_fillable_size if set
  8. slippage_modeled            — modeled_slippage_bps present
  9. stop_rule_present           — stop_rule non-empty
 10. tranche_consistency         — open implies starter, add implies confirmation_add or higher

Outputs JSON with per-gate result. Updates trade_intents:
  - evidence_freshness_status
  - factor_overlap_status
  - provenance_completeness_pct
  - counterargument_quality_score
  - explainability_status
  - state: 'risk_review' if all gates pass (the Risk agent's gate_risk_intents.py
    then sizes/approves|blocks); otherwise 'blocked' with blocked_reason

Usage:
  python3 gate_evaluator.py --intent-id ID
  python3 gate_evaluator.py --intent-id ID --dry-run
  python3 gate_evaluator.py --all-proposed   # process every state='proposed' or 'critic_review'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))

# Thresholds — keep in one place for tunability.
FRESHNESS_MAX_HOURS = 72.0
FACTOR_OVERLAP_MAX_PCT = 60.0
MIN_PROVENANCE_PCT = 50.0
MIN_COUNTERARG_SCORE = 50.0
MIN_RATIONALE_LEN = 40


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hours_since(s: str | None) -> float | None:
    dt = _parse_iso(s)
    if dt is None:
        return None
    return (_now() - dt).total_seconds() / 3600.0


def _weekend_discount_hours(start: datetime, end: datetime) -> float:
    """Count weekend hours between two timestamps.

    The freshness threshold stays fixed; this only avoids charging evidence for
    hours when the market was shut and no session-level update was even possible.
    """
    if end <= start:
        return 0.0
    total = 0.0
    day = datetime(start.year, start.month, start.day, tzinfo=start.tzinfo)
    while day < end:
        next_day = day + timedelta(days=1)
        if day.weekday() >= 5:
            seg_start = max(start, day)
            seg_end = min(end, next_day)
            if seg_end > seg_start:
                total += (seg_end - seg_start).total_seconds() / 3600.0
        day = next_day
    return total


def _evidence_freshness_artifacts(
    evid: list[sqlite3.Row], *, as_of: datetime
) -> tuple[bool, list[dict], str | None]:
    if not evid:
        return False, [], "no_evidence"

    stale: list[dict] = []
    stale_codes: list[str] = []
    for e in evid:
        retrieved_at = _parse_iso(e["retrieved_at"])
        artifact = {
            "id": e["id"],
            "source": e["source"],
            "source_url": e["source_url"],
            "retrieved_at": e["retrieved_at"],
        }
        if retrieved_at is None:
            artifact.update({
                "cause": "missing_retrieved_at",
                "raw_hours_old": None,
                "weekend_hours_discount": None,
                "adjusted_hours_old": None,
            })
            stale.append(artifact)
            stale_codes.append(f"missing_ts:{e['id']}")
            continue

        raw_hours = (as_of - retrieved_at).total_seconds() / 3600.0
        weekend_hours = _weekend_discount_hours(retrieved_at, as_of)
        adjusted_hours = max(0.0, raw_hours - weekend_hours)
        if adjusted_hours > FRESHNESS_MAX_HOURS:
            artifact.update({
                "cause": "stale_after_weekend_discount",
                "raw_hours_old": round(raw_hours, 1),
                "weekend_hours_discount": round(weekend_hours, 1),
                "adjusted_hours_old": round(adjusted_hours, 1),
            })
            stale.append(artifact)
            stale_codes.append(f"stale:{e['id']}")

    attribution = "|".join(stale_codes[:4])
    if len(stale_codes) > 4:
        attribution += f"|+{len(stale_codes) - 4}more"
    return not stale, stale, attribution or None


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _load_regime(conn) -> dict:
    row = conn.execute(
        "SELECT current, signals_json FROM regime ORDER BY determined_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {"current": None, "fail_closed": True}
    try:
        sig = json.loads(row["signals_json"] or "{}")
    except json.JSONDecodeError:
        sig = {}
    return {"current": row["current"], "fail_closed": bool(sig.get("fail_closed"))}


def _load_evidence(conn, hypothesis_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, source, source_url, retrieved_at FROM hypothesis_evidence "
        "WHERE hypothesis_id=?",
        (hypothesis_id,),
    ).fetchall()


def _load_open_position_tickers(conn) -> set[str]:
    rows = conn.execute(
        "SELECT ticker FROM positions "
        "WHERE state IN ('opening','open','scaling','trimming','closing')"
    ).fetchall()
    return {r["ticker"].upper() for r in rows}


def _load_latest_critic_review(conn, intent_id: str, hypothesis_id: str) -> dict | None:
    row = conn.execute(
        "SELECT challenges_json, all_challenges_addressed "
        "FROM critic_reviews WHERE target_id IN (?, ?) "
        "ORDER BY reviewed_at DESC LIMIT 1",
        (intent_id, hypothesis_id),
    ).fetchone()
    if not row:
        return None
    try:
        challenges = json.loads(row["challenges_json"] or "[]")
    except json.JSONDecodeError:
        challenges = []
    return {"challenges": challenges, "all_addressed": bool(row["all_challenges_addressed"])}


def _compact_gate_detail(detail: str) -> str:
    text = (detail or "").strip()
    text = re.sub(r"\s+", "", text)
    text = text.replace(",", "&")
    return text[:64] or "none"


def _format_blocked_reason(result: dict) -> str:
    failed = result.get("failed_gates", [])
    if not failed:
        return "gates_failed:"
    failed_gates = [g for g in result.get("gates", []) if not g.get("pass")]
    label_map = {}
    for gate in failed_gates:
        label = gate["name"]
        if gate["name"] == "evidence_freshness" and gate.get("attribution_code"):
            label = f"{label}[{gate['attribution_code']}]"
        label_map[gate["name"]] = label
    prefix = "gates_failed:" + ",".join(label_map.get(n, n) for n in failed)
    detail_map = {
        gate["name"]: _compact_gate_detail(str(gate.get("detail", "")))
        for gate in failed_gates
    }
    detail_suffix = ";".join(
        f"{name}={detail_map.get(name, 'none')}" for name in failed
    )
    return f"{prefix}|inputs={detail_suffix}"[:240]


def evaluate(conn, intent_id: str) -> dict:
    evaluated_at = _now()
    intent = conn.execute(
        "SELECT id, hypothesis_id, action, tranche_type, ticker, vehicle, size, "
        "entry_price_target, stop_rule, time_horizon, edge_scorecard_json, "
        "max_fillable_size, modeled_slippage_bps, state FROM trade_intents WHERE id=?",
        (intent_id,),
    ).fetchone()
    if not intent:
        return {"intent_id": intent_id, "error": "intent_not_found"}

    hypo = conn.execute(
        "SELECT id, rationale_concise, thesis_summary FROM hypotheses WHERE id=?",
        (intent["hypothesis_id"],),
    ).fetchone()

    gates: list[dict] = []

    # Risk-REDUCING intents (exit/trim of an existing position) face only sanity
    # gates, never idea-quality gates. The 10-gate stack exists to vet NEW risk;
    # applying evidence-freshness / counterargument / stop-rule to an exit means
    # an aging losing position can never be closed (2026-07-02: six WHR exit
    # intents blocked on stale evidence). Blocking an exit INCREASES risk.
    risk_reducing = intent["action"] in ("exit", "trim")

    # 1. regime_gate — new risk only; a risk_off regime must never trap an exit
    regime = _load_regime(conn)
    rg_ok = risk_reducing or (
        regime["current"] in ("risk_on", "neutral") and not regime["fail_closed"])
    gates.append({"name": "regime_gate", "pass": rg_ok,
                  "detail": f"current={regime['current']} fail_closed={regime['fail_closed']}"
                            + (" (exempt: risk-reducing)" if risk_reducing else "")})

    # 2. evidence_freshness — idea-quality; exempt for risk-reducing intents
    evid = _load_evidence(conn, intent["hypothesis_id"])
    freshness_ok, stale_artifacts, freshness_attr = _evidence_freshness_artifacts(
        evid, as_of=evaluated_at
    )
    ef_ok = risk_reducing or freshness_ok
    ef_detail = f"evidence={len(evid)} stale={len(stale_artifacts)} max_h={FRESHNESS_MAX_HOURS}"
    if freshness_attr:
        ef_detail += f" attr={freshness_attr}"
    gates.append({
        "name": "evidence_freshness",
        "pass": ef_ok,
        "detail": ef_detail,
        "artifacts": stale_artifacts,
        "attribution_code": freshness_attr,
    })

    # 3. factor_overlap (ticker overlap proxy)
    open_tix = _load_open_position_tickers(conn)
    sym = (intent["ticker"] or "").upper()
    overlap = sym in open_tix
    fo_ok = not overlap  # adding to same name is fine via 'add' tranche, but flag for first 'open'
    if intent["action"] in ("add", "trim", "exit"):
        fo_ok = True
    gates.append({"name": "factor_overlap", "pass": fo_ok,
                  "detail": f"sym={sym} open_position_already={overlap} action={intent['action']}"})

    # 4. provenance_completeness
    if evid:
        with_url = sum(1 for e in evid if (e["source_url"] or "").strip())
        prov_pct = round(100.0 * with_url / len(evid), 1)
    else:
        prov_pct = 0.0
    pc_ok = risk_reducing or prov_pct >= MIN_PROVENANCE_PCT
    gates.append({"name": "provenance_completeness", "pass": pc_ok,
                  "detail": f"{prov_pct}% (min {MIN_PROVENANCE_PCT}%)"})

    # 5. counterargument_quality
    review = _load_latest_critic_review(conn, intent["id"], intent["hypothesis_id"])
    if review is None:
        ca_score = 0.0
        ca_ok = False
        ca_detail = "no_critic_review"
    else:
        n = len(review["challenges"])
        addressed = review["all_addressed"]
        ca_score = 100.0 if addressed and n >= 1 else (60.0 if addressed else 30.0 if n else 0.0)
        ca_ok = ca_score >= MIN_COUNTERARG_SCORE
        ca_detail = f"challenges={n} addressed={addressed} score={ca_score}"
    ca_ok = risk_reducing or ca_ok
    gates.append({"name": "counterargument_quality", "pass": ca_ok, "detail": ca_detail})

    # 6. explainability
    text = (hypo["rationale_concise"] if hypo and hypo["rationale_concise"] else "") or ""
    ex_ok = len(text.strip()) >= MIN_RATIONALE_LEN
    gates.append({"name": "explainability", "pass": ex_ok,
                  "detail": f"rationale_len={len(text)} min={MIN_RATIONALE_LEN}"})

    # 7. size_sanity
    size = float(intent["size"] or 0)
    mx = intent["max_fillable_size"]
    if mx is None:
        ss_ok = size > 0
        ss_detail = f"size={size} (no max set)"
    else:
        ss_ok = 0 < size <= float(mx)
        ss_detail = f"size={size} max_fillable={mx}"
    gates.append({"name": "size_sanity", "pass": ss_ok, "detail": ss_detail})

    # 8. slippage_modeled
    sl_ok = intent["modeled_slippage_bps"] is not None
    gates.append({"name": "slippage_modeled", "pass": sl_ok,
                  "detail": f"modeled_slippage_bps={intent['modeled_slippage_bps']}"})

    # 9. stop_rule_present — an exit IS the stop being honored; never require one
    sr_ok = risk_reducing or bool((intent["stop_rule"] or "").strip())
    gates.append({"name": "stop_rule_present", "pass": sr_ok,
                  "detail": f"stop_rule={'set' if sr_ok else 'missing'}"})

    # 10. tranche_consistency
    tt = intent["tranche_type"]
    act = intent["action"]
    tc_ok = True
    if act == "open" and tt not in ("starter", None):
        tc_ok = False
    if act == "add" and tt not in ("confirmation_add", "conviction_add", "max_conviction", None):
        tc_ok = False
    gates.append({"name": "tranche_consistency", "pass": tc_ok,
                  "detail": f"action={act} tranche={tt}"})

    all_pass = all(g["pass"] for g in gates)
    failed = [g["name"] for g in gates if not g["pass"]]

    return {
        "intent_id": intent_id,
        "hypothesis_id": intent["hypothesis_id"],
        "evaluated_at": evaluated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "all_pass": all_pass,
        "failed_gates": failed,
        "gates": gates,
        "computed": {
            "provenance_completeness_pct": prov_pct,
            "counterargument_quality_score": ca_score,
            "evidence_freshness_status": "pass" if ef_ok else "fail",
            "factor_overlap_status": "pass" if fo_ok else "fail",
            "explainability_status": "pass" if ex_ok else "fail",
        },
        # Passing the critic gate stack hands off to the Risk agent's
        # deterministic gate (gate_risk_intents.py), which sizes + approves.
        "next_state": "risk_review" if all_pass else "blocked",
    }


def apply(conn, intent_id: str, result: dict) -> None:
    if "error" in result:
        return
    c = result["computed"]
    before_row = conn.execute(
        "SELECT state FROM trade_intents WHERE id=?", (intent_id,)
    ).fetchone()
    before_state = before_row["state"] if before_row else None
    blocked_reason = None
    if result["next_state"] == "blocked":
        blocked_reason = _format_blocked_reason(result)
    conn.execute(
        "UPDATE trade_intents SET evidence_freshness_status=?, factor_overlap_status=?, "
        "provenance_completeness_pct=?, counterargument_quality_score=?, "
        "explainability_status=?, state=?, blocked_reason=? WHERE id=?",
        (c["evidence_freshness_status"], c["factor_overlap_status"],
         c["provenance_completeness_pct"], c["counterargument_quality_score"],
         c["explainability_status"], result["next_state"], blocked_reason, intent_id),
    )
    aid = "AUDIT-" + _now_iso().replace(":", "").replace("-", "") + "-" + intent_id[:24]
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (aid, _now_iso(), "critic", "trade_intent", intent_id, "gate_evaluate",
         before_state, result["next_state"],
         ("gates: " + ",".join(g["name"] + ("+" if g["pass"] else "-") for g in result["gates"]))[:500]),
    )
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--intent-id", default=None)
    p.add_argument("--all-proposed", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    conn = connect()
    if args.intent_id:
        ids = [args.intent_id]
    elif args.all_proposed:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM trade_intents WHERE state IN ('proposed','critic_review')"
        )]
    else:
        p.error("must specify --intent-id or --all-proposed")
        return 2

    out = []
    for iid in ids:
        result = evaluate(conn, iid)
        if not args.dry_run and "error" not in result:
            apply(conn, iid, result)
        out.append(result)
    print(json.dumps({"processed": len(out), "dry_run": bool(args.dry_run),
                      "results": out}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
