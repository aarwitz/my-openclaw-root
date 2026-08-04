#!/usr/bin/env python3
"""Live integration of the empirically-calibrated mechanism set into the LIVE world model.

INCREMENTAL BY DEFAULT (2026-06-19) — this NEVER wipes the live learning ledger:
  * mechanism that PERSISTS (same id)   -> UPDATE its backtest prior (prior_alpha/prior_beta) but
    PRESERVE its observed_hits/observed_misses/last_observed_at; posterior = beta_mean(prior+obs).
  * mechanism that is NEW               -> INSERT with the backtest prior, zero live obs.
  * live mechanism no longer calibrated -> mark status='deprecated' (kept for history; stops being used).
  * `predictions`, `mechanism_observations`, `attribution`, `postmortems`, `patterns`, hypothesis
    grades are LEFT UNTOUCHED — the desk keeps everything it has learned from its own trades.
This is what lets us refresh the mechanism set (after a new discovery run) AND keep accruing live learning.
The two-learning-rates blend (calibrate.py) then shrinks the refreshed prior as live obs accumulate.

  python3 integrate_calibrated.py --approval-manifest config/approved-strategies/DNNN.json
                                             # exact approved artifact; preserves the ledger

Adding TICKERS never goes through this script at all — tickers enter via the feature store + the live
scan watchlist, and feed the ledger by trading + being graded (observations are keyed by mechanism).
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

LIVE = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")
# Backtest observations are highly date/sector-correlated. Keep the bootstrap
# prior deliberately weak so live outcomes can override it quickly.
PSEUDO_N = 10.0
HZN = {"swing_5d": "swing_1_5d", "month_21d": "position_1_4w", "quarter_63d": "trend_1_3m"}
DIRMAP = {"long": "long", "short": "short", "long_short": "long"}

KW = [
    ("oversold", ("oversold pullback dip drawdown", "mean reversion rebound")),
    ("drawdown", ("deep drawdown oversold selloff", "mean reversion recovery")),
    ("rsi", ("oversold overbought rsi reversal", "short-term reversal")),
    ("vol_20d_annual_hi", ("high volatility elevated risk", "volatility rebound")),
    ("vol_20d_annual_lo", ("low volatility quiet", "low-vol underperformance")),
    ("pe_ttm_lo", ("cheap low pe valuation undervalued value", "valuation rerating")),
    ("pe_ttm_hi", ("expensive rich high pe valuation", "multiple compression")),
    ("cheap_pe", ("cheap low pe valuation undervalued value", "valuation rerating")),
    ("net_margin", ("low margin profitability turnaround", "margin recovery")),
    ("revenue_growth", ("revenue growth sales growth", "growth continuation")),
    ("growth", ("revenue growth sales growth", "growth continuation")),
    ("momentum", ("price momentum trend uptrend", "trend continuation")),
    ("mom_12_1", ("price momentum trend uptrend", "trend continuation")),
    ("dist_sma", ("trend moving average uptrend momentum", "trend continuation")),
    ("earnings", ("earnings beat surprise guidance raise", "post-earnings drift")),
    ("vix", ("high volatility fear capitulation vix", "fear rebound")),
    ("rate_", ("interest rates duration macro", "rate-driven move")),
    ("credit", ("credit spreads risk-off macro", "risk-off move")),
    ("sentiment", ("news sentiment narrative", "sentiment drift")),
    ("days_to_cover", ("short interest crowded squeeze", "short squeeze")),
    ("short_int", ("short interest crowded squeeze", "short squeeze")),
    ("sector", ("sector strength rotation", "sector tailwind")),
    ("rating", ("analyst upgrade rating revision", "rating drift")),
    ("insider", ("insider buying selling form4", "insider signal")),
]


def tokens(mid):
    for key, pair in KW:
        if key in mid:
            return pair
    return ("market signal", "outperformance")


def integration_eligibility(cal: sqlite3.Connection) -> dict:
    """Return the source-controlled live-integration gate without mutating state."""
    labels = {
        str(r[0] or "")
        for r in cal.execute(
            "SELECT DISTINCT evaluation_label FROM discovered_mechanisms"
        )
    }
    calibrated_count = cal.execute(
        "SELECT COUNT(*) FROM calibrated_mechanisms"
    ).fetchone()[0]
    survivors = [dict(r) for r in cal.execute(
        "SELECT * FROM calibrated_mechanisms "
        "WHERE posterior_mean IS NOT NULL AND source != 'cross' AND bonf_sig=1 "
        "ORDER BY net_alpha_pct DESC"
    )]
    development_artifact = any(
        token in label.lower()
        for label in labels
        for token in ("development", "reused", "smoke")
    )
    blockers = []
    if development_artifact:
        blockers.append("development/reused holdout provenance")
    if not survivors:
        blockers.append("zero Bonferroni survivors with a measured probability posterior")
    return {
        "eligible": not blockers,
        "calibrated_count": calibrated_count,
        "eligible_survivors": survivors,
        "evaluation_labels": sorted(labels),
        "development_artifact": development_artifact,
        "blockers": blockers,
    }


def selected_survivors_for_mode(eligibility: dict, *, deprecate_all: bool) -> list[dict]:
    """A quarantine can never preserve or reactivate an analytical survivor."""
    return [] if deprecate_all else list(eligibility["eligible_survivors"])


def normalize_approved_candidates(rows: list[dict]) -> list[dict]:
    """Map the exact forward artifact payload to the runtime library schema."""
    normalized = []
    for raw in rows:
        row = dict(raw)
        conditions = row.get("conditions", row.get("conds_json", []))
        if isinstance(conditions, str):
            conditions = json.loads(conditions)
        normalized.append({
            **row,
            "conds_json": json.dumps(conditions, separators=(",", ":")),
            "source": str(row["source"]),
            "bonf_sig": int(bool(row["bonf_sig"])),
            "skew_edge": int(bool(row.get("skew_edge"))),
        })
    return normalized


def approved_candidate_note(row: dict, approval: dict, refreshed_at: str) -> str:
    """Freeze the executable candidate inside the live authorization row.

    The feature-store calibration table is mutable research state.  A live
    scanner must be able to reconstruct and verify its exact executable spec
    from the approved live row alone, including expiry and an artifact digest.
    """
    from promotion_gate import candidate_set_sha256

    conditions = row.get("conditions", row.get("conds_json", []))
    if isinstance(conditions, str):
        conditions = json.loads(conditions)
    candidate = {
        "id": row["id"],
        "horizon": row["horizon"],
        "direction": row["direction"],
        "kind": row["kind"],
        "source": row["source"],
        "conditions": conditions,
        "rationale": row.get("rationale"),
        "net_alpha_pct": row["net_alpha_pct"],
        "beta_neutral_alpha_pct": row["beta_neutral_alpha_pct"],
        "test_p": row["test_p"],
        "bonf_sig": int(bool(row["bonf_sig"])),
        "hit_te": row["hit_te"],
        "te_n": row["te_n"],
        "cluster_n": row["cluster_n"],
        "ticker_n": row["ticker_n"],
        "posterior_mean": row["posterior_mean"],
        "skew_edge": int(bool(row.get("skew_edge"))),
    }
    return json.dumps({
        "calibrated": True,
        "runtime_candidate": candidate,
        "runtime_candidate_sha256": candidate_set_sha256([candidate]),
        "approval_decision_id": approval["decision_id"],
        "approval_manifest_sha256": approval["_manifest_sha256"],
        "source_artifact_sha256": approval["_source_artifact_sha256"],
        "approval_expires_at": approval["expires_at"],
        "refreshed": refreshed_at,
    }, sort_keys=True, separators=(",", ":"))


def quarantine_live_for_staging(path: str, approval: dict) -> int:
    """Disable every scanner authorization before replacing its offline library.

    Cross-database atomic commits are not portable in WAL mode.  Quarantining
    the live ids first makes every partial failure safe: an offline staging row
    cannot fire until the final live transaction explicitly reactivates it.
    """
    conn = sqlite3.connect(path, timeout=60.0)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    active = conn.execute(
        "SELECT COUNT(*) FROM mechanisms WHERE status IN ('active','crowded')"
    ).fetchone()[0]
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE mechanisms SET status='deprecated' "
            "WHERE status IN ('active','crowded')"
        )
        conn.execute(
            "INSERT INTO audits(id,timestamp,actor,entity_type,entity_id,action,"
            "before_state,after_state,rationale_concise,experiment_id) "
            "VALUES(?,?,'developer','mechanism_set','approved_strategy_artifact',"
            "'quarantine_before_artifact_stage','active','deprecated',?,?)",
            (
                "AUDIT-" + uuid.uuid4().hex,
                now,
                (
                    f"fail-closed staging for {approval['decision_id']} source "
                    f"{approval['_source_artifact_sha256']}"
                )[:500],
                "preproduction_hardening_20260730",
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        conn.close()
        raise
    conn.close()
    return int(active)


def stage_approved_candidates(path: str, rows: list[dict], approval: dict) -> None:
    """Replace the scanner library with only the exact approved artifact rows."""
    conn = sqlite3.connect(path, timeout=60.0)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TABLE IF EXISTS calibrated_mechanisms")
        conn.execute("""CREATE TABLE calibrated_mechanisms(
            id TEXT, horizon TEXT, direction TEXT, kind TEXT, source TEXT,
            conds_json TEXT, rationale TEXT, net_alpha_pct REAL, test_p REAL,
            bonf_sig INT, hit_te REAL, te_n INT, cluster_n INT,
            posterior_mean REAL, skew_edge INT, created_at TEXT)""")
        for row in rows:
            conn.execute(
                "INSERT INTO calibrated_mechanisms VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["id"], row["horizon"], row["direction"], row["kind"],
                    row["source"], row["conds_json"], row.get("rationale"),
                    row["net_alpha_pct"], row["test_p"], row["bonf_sig"],
                    row["hit_te"], row["te_n"], row["cluster_n"],
                    row["posterior_mean"], row["skew_edge"], now,
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        conn.close()
        raise
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--deprecate-all",
        action="store_true",
        help="explicit fail-closed quarantine: deprecate every live mechanism while preserving history",
    )
    ap.add_argument(
        "--check-only",
        action="store_true",
        help="report live-integration eligibility without writing the live database",
    )
    ap.add_argument(
        "--approval-manifest",
        help=(
            "source-controlled manifest binding an exact completed locked-forward "
            "artifact and exact candidate set; required for every risk-adding write"
        ),
    )
    ap.add_argument("--reason", default="", help="required rationale for --deprecate-all")
    a = ap.parse_args()
    if a.check_only and a.deprecate_all:
        raise SystemExit("--check-only and --deprecate-all are mutually exclusive")
    if a.deprecate_all and len(a.reason.strip()) < 20:
        raise SystemExit("--deprecate-all requires a specific --reason (>=20 characters)")

    cal = sqlite3.connect(FEAT)
    cal.row_factory = sqlite3.Row
    eligibility = integration_eligibility(cal)
    cal.close()
    survivors = []
    approval = None
    approval_error = None
    if not a.deprecate_all:
        if not a.approval_manifest:
            approval_error = "no source-controlled approved strategy manifest"
        else:
            try:
                from promotion_gate import validate_approval_manifest

                approval = validate_approval_manifest(Path(a.approval_manifest))
                survivors = normalize_approved_candidates(
                    approval["_promotion_candidates"]
                )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                approval_error = f"invalid approval manifest: {exc}"
    if a.check_only:
        verdict = (
            "ELIGIBLE"
            if approval is not None and survivors and approval_error is None
            else "BLOCKED"
        )
        print(f"LIVE INTEGRATION ELIGIBILITY: {verdict}")
        print(f"  analytical survivors: {eligibility['calibrated_count']}")
        print(f"  live-eligible survivors: {len(survivors)}")
        print(f"  evaluation labels: {eligibility['evaluation_labels']}")
        if eligibility["blockers"] and approval is None:
            print("  blockers: " + "; ".join(eligibility["blockers"]))
        if approval_error:
            print("  promotion blocker: " + approval_error)
        elif approval:
            print(
                "  approved artifact: "
                f"{approval['decision_id']} manifest={approval['_manifest_sha256'][:12]} "
                f"source={approval['_source_artifact_sha256'][:12]}"
            )
        print("  live mechanism ledger: unchanged")
        return
    if not a.deprecate_all and approval_error:
        raise SystemExit(f"refusing live integration: {approval_error}")
    if not survivors and not a.deprecate_all:
        raise SystemExit(
            "no robust calibrated mechanisms (bonf_sig=1); preserving the "
            "quarantined live set"
        )

    if not a.deprecate_all:
        quarantine_live_for_staging(LIVE, approval)
        stage_approved_candidates(FEAT, survivors, approval)

    conn = sqlite3.connect(LIVE, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    sys.path.insert(0, os.path.dirname(__file__))
    import worldmodel as wm
    exp = (conn.execute("SELECT experiment_id FROM mechanisms WHERE experiment_id IS NOT NULL LIMIT 1").fetchone()
           or conn.execute("SELECT experiment_id FROM hypotheses WHERE experiment_id IS NOT NULL LIMIT 1").fetchone())
    exp = exp[0] if exp else "default"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def count(t):
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            return "—"

    led_before = {t: count(t) for t in ("mechanism_observations", "predictions", "attribution")}
    existing = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, observed_hits, observed_misses, created_at, status FROM mechanisms")}
    added = updated = deprecated = 0
    try:
        conn.execute("BEGIN")
        cal_ids = set()
        for s in survivors:
            mid = f'{s["id"]}__{s["horizon"]}'
            cal_ids.add(mid)
            pm = float(s["posterior_mean"])
            pa, pb = round(pm * PSEUDO_N, 4), round((1 - pm) * PSEUDO_N, 4)
            ant, cons = tokens(s["id"])
            note = approved_candidate_note(s, approval, now)
            if mid in existing:
                # PRESERVE live observations; refresh the backtest prior; re-blend the posterior
                hits = float(existing[mid].get("observed_hits") or 0.0)
                misses = float(existing[mid].get("observed_misses") or 0.0)
                post = round(wm.beta_mean(pa + hits, pb + misses), 6)
                ci_low, ci_high = wm.beta_ci(pa + hits, pb + misses)
                conn.execute(
                    "UPDATE mechanisms SET prior_alpha=?, prior_beta=?, name=?, antecedent_class=?, "
                    "consequent_class=?, direction=?, horizon=?, posterior_mean=?, posterior_ci_low=?, "
                    "posterior_ci_high=?, status='active', notes=? WHERE id=?",
                    (pa, pb, s["rationale"], ant, cons, DIRMAP.get(s["direction"], "long"),
                     HZN.get(s["horizon"], "position_1_4w"), post, round(ci_low, 6), round(ci_high, 6), note, mid))
                updated += 1
            else:
                ci_low, ci_high = wm.beta_ci(pa, pb)
                conn.execute(
                    "INSERT INTO mechanisms(id, created_at, created_by, name, antecedent_class, "
                    "transmission_chain_json, consequent_class, direction, horizon, regime_context, "
                    "prior_alpha, prior_beta, observed_hits, observed_misses, posterior_mean, "
                    "posterior_ci_low, posterior_ci_high, half_life_days, last_observed_at, status, "
                    "notes, experiment_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, now, "archivist", s["rationale"], ant, "[]", cons,
                     DIRMAP.get(s["direction"], "long"), HZN.get(s["horizon"], "position_1_4w"), "any",
                     pa, pb, 0.0, 0.0, round(pm, 6), round(ci_low, 6), round(ci_high, 6), 180.0, None,
                     "active", note, exp))
                added += 1

        # Deprecate mechanisms no longer in the accepted set (KEEP observations/history).
        for mid in existing:
            if mid not in cal_ids:
                cursor = conn.execute(
                    "UPDATE mechanisms SET status='deprecated' "
                    "WHERE id=? AND status!='deprecated'",
                    (mid,),
                )
                deprecated += cursor.rowcount
        if a.deprecate_all:
            conn.execute(
                "INSERT INTO audits(id,timestamp,actor,entity_type,entity_id,action,"
                "before_state,after_state,rationale_concise,experiment_id) "
                "VALUES(?,?,'developer','mechanism_set','all','quarantine_no_robust_edge',"
                "'active','deprecated',?,?)",
                (
                    "AUDIT-" + uuid.uuid4().hex,
                    now,
                    a.reason.strip()[:500],
                    "preproduction_hardening_20260730",
                ),
            )
        else:
            conn.execute(
                "INSERT INTO audits(id,timestamp,actor,entity_type,entity_id,action,"
                "before_state,after_state,rationale_concise,experiment_id) "
                "VALUES(?,?,'developer','mechanism_set','approved_strategy_artifact',"
                "'integrate_approved_artifact','quarantined','active',?,?)",
                (
                    "AUDIT-" + uuid.uuid4().hex,
                    now,
                    (
                        f"{approval['decision_id']} manifest "
                        f"{approval['_manifest_sha256']} source "
                        f"{approval['_source_artifact_sha256']}"
                    )[:500],
                    exp,
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    led_after = {t: count(t) for t in led_before}
    mode = "FAIL-CLOSED QUARANTINE" if a.deprecate_all else "incremental — ledger preserved"
    print("LIVE INTEGRATION COMPLETE", f"({mode})")
    print(f"  experiment_id={exp}")
    mechanism_total = count("mechanisms")
    mechanism_active = conn.execute(
        "SELECT COUNT(*) FROM mechanisms WHERE status IN ('active','crowded')"
    ).fetchone()[0]
    print(f"  mechanisms: +{added} added, {updated} updated (priors refreshed, obs preserved), "
          f"{deprecated} deprecated -> {mechanism_total} retained ({mechanism_active} active)")
    print("  LEDGER (preserved):")
    for t in led_before:
        flag = "" if led_before[t] == led_after[t] else "  <-- CHANGED?!"
        print(f"    {t:24} {str(led_before[t]):>6} -> {led_after[t]}{flag}")
    conn.close()


if __name__ == "__main__":
    main()
