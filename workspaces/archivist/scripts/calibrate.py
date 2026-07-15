#!/usr/bin/env python3
"""Archivist · calibrate.py — the world-model learning engine.

Runs the closed feedback loop that turns realized outcomes into model updates.
Three stages, executed in order:

  1. resolve_predictions   — for every unresolved `predictions` row whose
     hypothesis has been graded (`hypotheses.resolved_state` set), compute the
     Brier component, mark the prediction resolved, and emit one append-only
     `mechanism_observations` row per linked mechanism (respecting alignment:
     an *opposing* mechanism scores a HIT when the thesis turned out WRONG).

  2. recompute_mechanisms  — for every mechanism, re-derive its Beta posterior
     from the full `mechanism_observations` ledger with half-life decay, then
     write back observed_hits / observed_misses / posterior_mean / CI.
     This is AUTONOMOUS: data accumulation never needs human approval.

  3. propose_structural    — draft gated `rule_proposals` for changes that DO
     need a human: candidate→active promotion, active→deprecated retirement,
     and a scoring-recalibration review when aggregate Brier degrades.
     Structural changes are proposals only; a human approves/applies them.

Determinism: every number here comes from the data + worldmodel.py. The LLM
never touches the math; it only authors the prose rationale on a proposal it
chooses to surface to the human.

Usage:
    python3 calibrate.py                 # full loop, writes changes
    python3 calibrate.py --dry-run       # compute + report, no writes
    python3 calibrate.py --no-propose    # learn from data, skip proposals
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

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
import worldmodel as wm  # noqa: E402

EXPERIMENT_DEFAULT = "world_model_v1"

# --- structural-proposal thresholds (gated; human approves) -----------------
PROMOTE_MIN_N = 6.0            # decayed effective observations required
PROMOTE_CI_LOW = 0.55         # posterior 5th pct must clear this to promote
PRIOR_DECAY_N = 30.0          # live obs at which the backtest prior's weight halves (two learning rates)
DEPRECATE_CI_HIGH = 0.45      # posterior 95th pct below this → propose retire
BRIER_REVIEW_MIN = 10         # min resolved predictions before judging Brier
BRIER_REVIEW_THRESHOLD = 0.30 # mean Brier above this → propose recalibration

EXIT_OK = 0
EXIT_FAIL_LOUD = 2


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_dt().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_days(then: str | None, now: datetime) -> float:
    dt = _parse_iso(then)
    if dt is None:
        return 0.0
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"trading-intel DB missing at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _audit(conn, *, entity_type, entity_id, action, before, after, rationale, exp):
    # Entity ids are not unique on a 20-char prefix, especially for mechanism ids.
    # Add a random suffix so one calibration run cannot collide with prior or sibling audits.
    aid = (
        "AUDIT-"
        + _now_iso().replace(":", "").replace("-", "")
        + "-"
        + entity_id[:20]
        + "-"
        + uuid.uuid4().hex[:8]
    )
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise, experiment_id) "
        "VALUES (?, ?, 'archivist', ?, ?, ?, ?, ?, ?, ?)",
        (aid, _now_iso(), entity_type, entity_id, action, before, after,
         (rationale or "")[:500], exp),
    )


def _mechanism_links(raw: str | None) -> list[dict]:
    """Tolerate both the legacy list[str] and the enriched list[dict] format."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    for item in data:
        if isinstance(item, str):
            out.append({"id": item, "align": 1})
        elif isinstance(item, dict) and item.get("id"):
            out.append({"id": item["id"], "align": int(item.get("align", 1) or 0) or 1})
    return out


# --------------------------------------------------------------------------- #
# Stage 1 — resolve predictions into Brier + mechanism observations
# --------------------------------------------------------------------------- #
# rp-payoff-aware-grading-20260715: a resolution inside the dead-band is a push,
# not evidence — it earns no Brier and emits no mechanism observations. A +1bp
# excess is coin-flip noise; letting it grade a mechanism 'hit' polluted the
# posteriors the whole learning loop runs on.
EXCESS_DEADBAND_PCT = 0.5  # |SPY-relative excess| in percent


def resolve_predictions(conn, now, exp, dry_run) -> list[dict]:
    rows = conn.execute(
        "SELECT p.id, p.hypothesis_id, p.p_correct, p.mechanism_ids_json, "
        "p.regime_at_prediction, p.realized_excess_pct, h.resolved_state, h.resolved_at "
        "FROM predictions p JOIN hypotheses h ON h.id = p.hypothesis_id "
        "WHERE p.resolved_at IS NULL AND h.resolved_state IS NOT NULL"
    ).fetchall()

    resolved: list[dict] = []
    for r in rows:
        excess = r["realized_excess_pct"]
        if excess is not None and abs(float(excess)) <= EXCESS_DEADBAND_PCT:
            if not dry_run:
                conn.execute(
                    "UPDATE predictions SET realized_outcome='inconclusive', resolved_at=? WHERE id=?",
                    (_now_iso(), r["id"]),
                )
                _audit(conn, entity_type="prediction", entity_id=r["id"],
                       action="resolve", before="unresolved", after="inconclusive",
                       rationale=f"dead-band push: excess {float(excess):+.2f}% within ±{EXCESS_DEADBAND_PCT}% — no Brier, no observations",
                       exp=exp)
            resolved.append({"prediction_id": r["id"], "hypothesis_id": r["hypothesis_id"],
                             "outcome": "inconclusive", "brier": None,
                             "observations_emitted": 0})
            continue
        correct = str(r["resolved_state"]).startswith("correct")
        bit = 1.0 if correct else 0.0
        outcome = "correct" if correct else "incorrect"
        brier = round((float(r["p_correct"]) - bit) ** 2, 6)
        resolved_at = r["resolved_at"] or _now_iso()

        links = _mechanism_links(r["mechanism_ids_json"])
        obs_written = 0
        for link in links:
            align = link["align"]
            # Supporting mechanism is right when the thesis is right; an opposing
            # mechanism is right when the thesis is WRONG.
            mech_correct = correct if align >= 0 else (not correct)
            mobs = "hit" if mech_correct else "miss"
            if not dry_run:
                oid = "mobs-" + uuid.uuid4().hex[:20]
                conn.execute(
                    "INSERT INTO mechanism_observations (id, mechanism_id, observed_at, "
                    "source_type, source_id, outcome, weight, regime_at_obs, notes, "
                    "experiment_id) VALUES (?, ?, ?, 'prediction', ?, ?, 1.0, ?, ?, ?)",
                    (oid, link["id"], resolved_at, r["id"], mobs,
                     r["regime_at_prediction"],
                     f"from prediction {r['id']} (align={align}, thesis={outcome})", exp),
                )
            obs_written += 1

        if not dry_run:
            conn.execute(
                "UPDATE predictions SET realized_outcome=?, brier_component=?, "
                "resolved_at=? WHERE id=?",
                (outcome, brier, _now_iso(), r["id"]),
            )
            _audit(conn, entity_type="prediction", entity_id=r["id"],
                   action="resolve", before="unresolved", after=outcome,
                   rationale=f"thesis={r['resolved_state']} brier={brier} obs+={obs_written}",
                   exp=exp)

        resolved.append({"prediction_id": r["id"], "hypothesis_id": r["hypothesis_id"],
                         "outcome": outcome, "brier": brier,
                         "observations_emitted": obs_written})
    return resolved


# --------------------------------------------------------------------------- #
# Stage 2 — recompute Beta posteriors with half-life decay (autonomous)
# --------------------------------------------------------------------------- #
def recompute_mechanisms(conn, now, exp, dry_run) -> list[dict]:
    mechs = conn.execute(
        "SELECT id, prior_alpha, prior_beta, half_life_days, posterior_mean, "
        "observed_hits, observed_misses, status FROM mechanisms"
    ).fetchall()

    changes: list[dict] = []
    for m in mechs:
        half_life = float(m["half_life_days"] or 180.0)
        obs = conn.execute(
            "SELECT observed_at, outcome, weight FROM mechanism_observations "
            "WHERE mechanism_id=?", (m["id"],)
        ).fetchall()
        hits = 0.0
        misses = 0.0
        for o in obs:
            w = float(o["weight"] or 1.0) * wm.decay_weight(
                _age_days(o["observed_at"], now), half_life)
            oc = o["outcome"]
            if oc == "hit":
                hits += w
            elif oc == "miss":
                misses += w
            elif oc == "partial":
                hits += 0.5 * w
                misses += 0.5 * w

        # Two learning rates: the backtest prior bootstraps belief, but the desk's OWN live track
        # record progressively overrides it. The observation ledger is reset at each calibrated
        # integration, so accrued (hits+misses) here ARE the live evidence — shrink the backtest
        # prior as it grows (PRIOR_DECAY_N live obs -> prior weight halved). At n_live=0 this is a
        # no-op (full backtest prior), so existing behaviour is unchanged until the desk trades.
        n_live = hits + misses
        shrink = PRIOR_DECAY_N / (PRIOR_DECAY_N + n_live)
        alpha = float(m["prior_alpha"]) * shrink + hits
        beta = float(m["prior_beta"]) * shrink + misses
        mean = round(wm.beta_mean(alpha, beta), 6)
        ci_low, ci_high = wm.beta_ci(alpha, beta)
        ci_low, ci_high = round(ci_low, 6), round(ci_high, 6)

        prev_mean = m["posterior_mean"]
        moved = prev_mean is None or abs(float(prev_mean) - mean) > 1e-6 or \
            abs(float(m["observed_hits"] or 0) - hits) > 1e-6 or \
            abs(float(m["observed_misses"] or 0) - misses) > 1e-6
        last_obs_at = max((o["observed_at"] for o in obs), default=None)
        if moved and not dry_run:
            conn.execute(
                "UPDATE mechanisms SET observed_hits=?, observed_misses=?, "
                "posterior_mean=?, posterior_ci_low=?, posterior_ci_high=?, "
                "last_observed_at=COALESCE(?, last_observed_at) WHERE id=?",
                (round(hits, 6), round(misses, 6), mean, ci_low, ci_high,
                 last_obs_at, m["id"]),
            )
            _audit(conn, entity_type="mechanism", entity_id=m["id"],
                   action="recalibrate",
                   before=f"mean={prev_mean}", after=f"mean={mean}",
                   rationale=f"hits={round(hits,3)} misses={round(misses,3)} "
                             f"ci=({ci_low},{ci_high}) n_obs={len(obs)}", exp=exp)
        changes.append({"mechanism_id": m["id"], "status": m["status"],
                        "posterior_mean": mean, "ci": [ci_low, ci_high],
                        "eff_hits": round(hits, 3), "eff_misses": round(misses, 3),
                        "n_obs": len(obs), "moved": bool(moved)})
    return changes


# --------------------------------------------------------------------------- #
# Stage 3 — draft gated structural proposals
# --------------------------------------------------------------------------- #
def _open_proposal_exists(conn, target: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM rule_proposals WHERE target_artifact=? AND status='proposed' "
        "LIMIT 1", (target,)
    ).fetchone() is not None


def _new_proposal(conn, *, target, current, proposed, rationale, evidence, exp):
    pid = "rp-" + uuid.uuid4().hex[:20]
    conn.execute(
        "INSERT INTO rule_proposals (id, created_at, proposer, target_artifact, "
        "current_value, proposed_value, rationale, evidence_refs_json, status, "
        "experiment_id) VALUES (?, ?, 'archivist', ?, ?, ?, ?, ?, 'proposed', ?)",
        (pid, _now_iso(), target, str(current), str(proposed), rationale[:500],
         json.dumps(evidence), exp),
    )
    return pid


def propose_structural(conn, mech_changes, calib, exp, dry_run) -> list[dict]:
    proposals: list[dict] = []

    by_id = {c["mechanism_id"]: c for c in mech_changes}
    rows = conn.execute(
        "SELECT id, status, posterior_ci_low, posterior_ci_high FROM mechanisms"
    ).fetchall()
    for m in rows:
        c = by_id.get(m["id"], {})
        eff_n = c.get("eff_hits", 0.0) + c.get("eff_misses", 0.0)
        ci_low = m["posterior_ci_low"]
        ci_high = m["posterior_ci_high"]
        if ci_low is None or ci_high is None:
            continue

        # candidate → active promotion
        if (m["status"] == "candidate" and eff_n >= PROMOTE_MIN_N
                and ci_low >= PROMOTE_CI_LOW):
            target = f"mechanisms.{m['id']}.status"
            if not _open_proposal_exists(conn, target):
                rationale = (f"Promote candidate→active: ci_low={round(ci_low,3)} "
                             f">= {PROMOTE_CI_LOW} on eff_n={round(eff_n,2)} "
                             f">= {PROMOTE_MIN_N}.")
                ev = {"posterior_ci": [ci_low, ci_high], "eff_n": round(eff_n, 2)}
                if not dry_run:
                    pid = _new_proposal(conn, target=target, current="candidate",
                                        proposed="active", rationale=rationale,
                                        evidence=ev, exp=exp)
                else:
                    pid = "(dry-run)"
                proposals.append({"id": pid, "target": target, "kind": "promote"})

        # active → deprecated retirement
        elif (m["status"] == "active" and eff_n >= PROMOTE_MIN_N
                and ci_high <= DEPRECATE_CI_HIGH):
            target = f"mechanisms.{m['id']}.status"
            if not _open_proposal_exists(conn, target):
                rationale = (f"Deprecate active mechanism: ci_high={round(ci_high,3)} "
                             f"<= {DEPRECATE_CI_HIGH} on eff_n={round(eff_n,2)}; "
                             f"posterior collapsed.")
                ev = {"posterior_ci": [ci_low, ci_high], "eff_n": round(eff_n, 2)}
                if not dry_run:
                    pid = _new_proposal(conn, target=target, current="active",
                                        proposed="deprecated", rationale=rationale,
                                        evidence=ev, exp=exp)
                else:
                    pid = "(dry-run)"
                proposals.append({"id": pid, "target": target, "kind": "deprecate"})

    # aggregate-Brier recalibration review
    if (calib["resolved_count"] >= BRIER_REVIEW_MIN
            and calib["mean_brier"] is not None
            and calib["mean_brier"] > BRIER_REVIEW_THRESHOLD):
        target = "quant.scoring_recalibration"
        if not _open_proposal_exists(conn, target):
            rationale = (f"Aggregate Brier {calib['mean_brier']} > "
                         f"{BRIER_REVIEW_THRESHOLD} over {calib['resolved_count']} "
                         f"resolved predictions — review scoring weights / base rate.")
            ev = {"mean_brier": calib["mean_brier"],
                  "resolved_count": calib["resolved_count"]}
            if not dry_run:
                pid = _new_proposal(conn, target=target, current="current_weights",
                                    proposed="review", rationale=rationale,
                                    evidence=ev, exp=exp)
            else:
                pid = "(dry-run)"
            proposals.append({"id": pid, "target": target, "kind": "brier_review"})

    return proposals


def compute_calibration(conn) -> dict:
    rows = conn.execute(
        "SELECT brier_component FROM predictions WHERE brier_component IS NOT NULL"
    ).fetchall()
    n = len(rows)
    mean = round(sum(float(r["brier_component"]) for r in rows) / n, 6) if n else None
    return {"resolved_count": n, "mean_brier": mean}


def mechanism_expectancy(conn) -> list[dict]:
    """Per-mechanism EXPECTANCY (mean SPY-relative excess of its supporting
    observations) alongside the hit-rate the Beta posterior sees. A mechanism
    that wins +8% and loses -2% at a 45% hit rate is profitable — hit-rate-only
    learning marks it down; this column is where that shows up
    (rp-payoff-aware-grading-20260715). Supporting links only (align>=0 in the
    obs note); needs predictions.realized_excess_pct (migration 0019)."""
    try:
        rows = conn.execute(
            "SELECT o.mechanism_id, m.name, COUNT(*) n, "
            "AVG(p.realized_excess_pct) mean_excess_pct, "
            "AVG(CASE WHEN o.outcome='hit' THEN 1.0 ELSE 0.0 END) hit_rate "
            "FROM mechanism_observations o "
            "JOIN predictions p ON p.id = o.source_id "
            "JOIN mechanisms m ON m.id = o.mechanism_id "
            "WHERE o.source_type='prediction' AND p.realized_excess_pct IS NOT NULL "
            "AND o.notes NOT LIKE '%align=-1%' "
            "GROUP BY o.mechanism_id HAVING n >= 3 "
            "ORDER BY mean_excess_pct DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"mechanism_id": r[0], "name": r[1], "n": r[2],
             "expectancy_pct": round(r[3], 3) if r[3] is not None else None,
             "hit_rate": round(r[4], 3)} for r in rows]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-resolve", action="store_true")
    p.add_argument("--no-recompute", action="store_true")
    p.add_argument("--no-propose", action="store_true")
    p.add_argument("--experiment-id", default=EXPERIMENT_DEFAULT)
    args = p.parse_args(argv)

    now = _now_dt()
    exp = args.experiment_id
    try:
        conn = _connect()
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return EXIT_FAIL_LOUD

    resolved = [] if args.no_resolve else resolve_predictions(conn, now, exp, args.dry_run)
    mech_changes = [] if args.no_recompute else recompute_mechanisms(conn, now, exp, args.dry_run)
    calib = compute_calibration(conn)
    expectancy = mechanism_expectancy(conn)
    proposals = [] if args.no_propose else propose_structural(
        conn, mech_changes, calib, exp, args.dry_run)

    if not args.dry_run:
        conn.commit()

    print(json.dumps({
        "dry_run": bool(args.dry_run),
        "experiment_id": exp,
        "predictions_resolved": len(resolved),
        "mechanisms_recomputed": sum(1 for c in mech_changes if c["moved"]),
        "mechanisms_total": len(mech_changes),
        "proposals_drafted": len(proposals),
        "calibration": calib,
        "mechanism_expectancy": expectancy,
        "resolved": resolved,
        "mechanism_changes": mech_changes,
        "proposals": proposals,
    }, indent=2, default=str))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
