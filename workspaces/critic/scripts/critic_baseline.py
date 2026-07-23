#!/usr/bin/env python3
"""Critic · critic_baseline.py

Deterministic baseline critic for scored hypotheses.

Review rule (conservative):
  - hypothesis is in state = scored
  - quant_score >= MIN_REVIEW_SCORE
  - promote only if it also clears the stronger promotion checks:
    evidence count/freshness, regime, rationale quality, exposure, valuation

For every reviewed hypothesis, the script records a fresh `critic_review`.
Passing names move to `ready` with `all_challenges_addressed=1`.
Failing names move to or remain in `challenged` with unresolved challenges.

Idempotent with respect to terminal hypothesis states: rows already in
`ready`/`active`/`resolved` are skipped.

Usage:
    python3 critic_baseline.py            # review all eligible
    python3 critic_baseline.py --dry-run  # show what would happen
    python3 critic_baseline.py --max 10   # cap reviewed rows this pass
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))

MIN_REVIEW_SCORE = 60.0
MIN_SCORE = 65.0
MIN_EVIDENCE = 1
MAX_EVIDENCE_AGE_H = 72.0
MIN_RATIONALE_LEN = 40
# Valuation discipline: a richly-valued name (negative margin of safety, with
# decent valuation confidence) must clear a HIGHER conviction bar to be promoted —
# "don't overpay unless the edge is strong." Advisory only; the risk gate is the
# hard, non-bypassable stop.
VAL_RICH_PENALTY = 8.0
VAL_MIN_CONF = 0.5
VAL_RICH_MOS = -0.10


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hours_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"trading-intel DB missing at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _audit(conn, *, actor, entity_id, action, before_state, after_state, rationale):
    aid = "AUDIT-" + _now_iso().replace(":", "").replace("-", "") + "-" + entity_id[:24]
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (aid, _now_iso(), actor, "hypothesis", entity_id, action,
         before_state, after_state, (rationale or "")[:500]),
    )


def _regime_current(conn) -> str:
    row = conn.execute(
        "SELECT current FROM regime ORDER BY determined_at DESC LIMIT 1"
    ).fetchone()
    return row["current"] if row else "unknown"


def _has_open_exposure(conn, ticker: str) -> bool:
    sym = ticker.upper()
    pos = conn.execute(
        "SELECT 1 FROM positions WHERE UPPER(ticker)=? AND state IN "
        "('opening','open','scaling','trimming','closing')",
        (sym,),
    ).fetchone()
    if pos:
        return True
    intent = conn.execute(
        "SELECT 1 FROM trade_intents WHERE UPPER(ticker)=? AND state IN "
        "('proposed','critic_review','approved','submitted','partial')",
        (sym,),
    ).fetchone()
    return bool(intent)


def _valuation(conn, ticker: str) -> dict | None:
    try:
        row = conn.execute(
            "SELECT margin_of_safety, confidence, zone, implied_growth, growth_assumed "
            "FROM valuations WHERE ticker=? AND applicable=1 ORDER BY as_of DESC LIMIT 1",
            (ticker.upper(),),
        ).fetchone()
    except sqlite3.Error:
        return None
    return dict(row) if row else None


def evaluate(conn, hyp_row) -> dict:
    hid = hyp_row["id"]
    reasons: list[str] = []

    if hyp_row["state"] in ("ready", "active", "resolved"):
        return {"id": hid, "skip": True, "reason": f"already state={hyp_row['state']}"}

    if hyp_row["state"] != "scored":
        return {"id": hid, "skip": True, "reason": f"not promotable from state={hyp_row['state']}"}

    score = hyp_row["quant_score"]
    if score is None or float(score) < MIN_REVIEW_SCORE:
        return {"id": hid, "skip": True, "reason": f"quant_score={score} < {MIN_REVIEW_SCORE}"}

    rationale = (hyp_row["rationale_concise"] or "").strip()
    if len(rationale) < MIN_RATIONALE_LEN:
        return {"id": hid, "fail": True, "primary_ticker": None, "quant_score": float(score),
                "reason": f"rationale_len={len(rationale)} < {MIN_RATIONALE_LEN}"}

    evid = conn.execute(
        "SELECT id, source_url, retrieved_at FROM hypothesis_evidence WHERE hypothesis_id=?",
        (hid,),
    ).fetchall()
    if len(evid) < MIN_EVIDENCE:
        return {"id": hid, "fail": True, "primary_ticker": None, "quant_score": float(score),
                "reason": f"evidence_count={len(evid)} < {MIN_EVIDENCE}"}
    age = _hours_since(max((e["retrieved_at"] for e in evid), default=None))
    if age is None or age > MAX_EVIDENCE_AGE_H:
        return {"id": hid, "fail": True, "primary_ticker": None, "quant_score": float(score),
                "reason": f"latest_evidence_age={age}h > {MAX_EVIDENCE_AGE_H}h"}

    regime = _regime_current(conn)
    if regime == "risk_off":
        return {"id": hid, "fail": True, "primary_ticker": None, "quant_score": float(score),
                "reason": f"regime={regime} blocks new exposure"}

    try:
        tickers = json.loads(hyp_row["tickers"] or "[]")
    except json.JSONDecodeError:
        tickers = []
    if not tickers:
        return {"id": hid, "fail": True, "primary_ticker": None, "quant_score": float(score),
                "reason": "no tickers"}
    primary = str(tickers[0]).upper()
    if _has_open_exposure(conn, primary):
        return {"id": hid, "fail": True, "primary_ticker": primary, "quant_score": float(score),
                "reason": f"open exposure already on {primary}"}

    # Valuation discipline: rich names need stronger conviction to promote.
    val = _valuation(conn, primary)
    val_note = None
    if val and val.get("margin_of_safety") is not None and val.get("confidence") is not None:
        mos, vconf = float(val["margin_of_safety"]), float(val["confidence"])
        if mos <= VAL_RICH_MOS and vconf >= VAL_MIN_CONF:
            bar = MIN_SCORE + VAL_RICH_PENALTY
            ig, ga = val.get("implied_growth"), val.get("growth_assumed")
            val_note = (f"valuation: {val.get('zone')} (MoS {mos*100:+.0f}%, conf {vconf:.2f}"
                        + (f", market implies {ig*100:.0f}% growth vs {ga*100:.0f}% history" if (ig is not None and ga is not None) else "")
                        + f"); rich → conviction bar raised to {bar:.0f}")
            if float(score) < bar:
                return {"id": hid, "fail": True, "primary_ticker": primary, "quant_score": float(score),
                        "reason": f"{primary} richly valued (MoS {mos*100:+.0f}%, conf {vconf:.2f}); "
                                  f"quant_score={score} < raised bar {bar:.0f}"}

    return {
        "id": hid,
        "promote": True,
        "primary_ticker": primary,
        "quant_score": float(score),
        "evidence_count": len(evid),
        "latest_evidence_age_h": round(age, 1),
        "regime": regime,
        "valuation_note": val_note,
        "reasons": reasons,
    }


def promote(conn, ev: dict, hyp_row) -> dict:
    hid = ev["id"]
    rid = "critrev-baseline-" + uuid.uuid4().hex[:24]
    challenges = [
        {
            "challenge": "baseline_promotion",
            "response": (
                f"deterministic baseline critic promoted on quant_score={ev['quant_score']}, "
                f"evidence_count={ev['evidence_count']}, "
                f"latest_evidence_age_h={ev['latest_evidence_age_h']}, "
                f"regime={ev['regime']}"
            ),
            "resolved": True,
        }
    ]
    if ev.get("valuation_note"):
        challenges.append({"challenge": "valuation_discipline",
                           "response": ev["valuation_note"], "resolved": True})
    addressed_json = json.dumps(challenges)
    conn.execute(
        "INSERT INTO critic_reviews (id, target_type, target_id, reviewed_at, "
        "reviewed_by, challenges_json, all_challenges_addressed) "
        "VALUES (?, 'hypothesis', ?, ?, 'critic_baseline', ?, 1)",
        (rid, hid, _now_iso(), addressed_json),
    )
    before = hyp_row["state"]
    conn.execute(
        "UPDATE hypotheses SET state='ready', last_critic_review_at=? WHERE id=?",
        (_now_iso(), hid),
    )
    _audit(conn, actor="critic", entity_id=hid, action="promote_to_ready",
           before_state=before, after_state="ready",
           rationale=(f"baseline_promote {ev['primary_ticker']} "
                      f"score={ev['quant_score']} evid={ev['evidence_count']}"))
    return {"id": hid, "ticker": ev["primary_ticker"], "promoted": True,
            "review_id": rid, "from": before, "to": "ready"}


def challenge(conn, ev: dict, hyp_row) -> dict:
    hid = ev["id"]
    rid = "cr-" + uuid.uuid4().hex[:24]
    reason = ev["reason"]
    primary = ev.get("primary_ticker")
    challenges = [
        {
            "challenge": "baseline_challenge",
            "response": reason,
            "resolved": False,
        }
    ]
    conn.execute(
        "INSERT INTO critic_reviews (id, target_type, target_id, reviewed_at, "
        "reviewed_by, challenges_json, all_challenges_addressed) "
        "VALUES (?, 'hypothesis', ?, ?, 'critic', ?, 0)",
        (rid, hid, _now_iso(), json.dumps(challenges)),
    )
    before = hyp_row["state"]
    conn.execute(
        "UPDATE hypotheses SET state='challenged', last_critic_review_at=? WHERE id=?",
        (_now_iso(), hid),
    )
    _audit(conn, actor="critic", entity_id=hid, action="challenge_hypothesis",
           before_state=before, after_state="challenged", rationale=reason[:500])
    return {"id": hid, "ticker": primary, "challenged": True,
            "review_id": rid, "from": before, "to": "challenged", "reason": reason}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max", type=int, default=10)
    args = p.parse_args(argv)

    conn = _connect()
    rows = conn.execute(
        "SELECT id, tickers, state, quant_score, rationale_concise "
        "FROM hypotheses WHERE state='scored' "
        "AND quant_score >= ? "
        "ORDER BY quant_score DESC NULLS LAST, scored_at DESC NULLS LAST"
    , (MIN_REVIEW_SCORE,)).fetchall()

    results = []
    reviewed = 0
    promoted = 0
    challenged = 0
    for r in rows:
        ev = evaluate(conn, r)
        if reviewed >= args.max:
            results.append({"id": r["id"], "skip": True, "reason": f"max_reviews_reached={args.max}"})
        elif ev.get("promote"):
            if not args.dry_run:
                results.append(promote(conn, ev, r))
            else:
                results.append({"id": r["id"], "ticker": ev["primary_ticker"], "would_promote": True})
            promoted += 1
            reviewed += 1
        elif ev.get("fail"):
            if not args.dry_run:
                results.append(challenge(conn, ev, r))
            else:
                results.append({"id": r["id"], "ticker": ev.get("primary_ticker"),
                                "would_challenge": True, "reason": ev["reason"]})
            challenged += 1
            reviewed += 1
        else:
            results.append(ev)

    if not args.dry_run:
        conn.commit()
    print(json.dumps({
        "processed": len(rows),
        "reviewed": reviewed,
        "promoted": promoted,
        "challenged": challenged,
        "dry_run": bool(args.dry_run),
        "results": results,
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
