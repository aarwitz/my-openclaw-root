#!/usr/bin/env python3
"""Deterministic probabilistic predictor (quant).

For every eligible scored hypothesis, emit ONE `predictions` row carrying a
calibrated probability the thesis resolves correct (`p_correct`) plus a
P10/P50/P90 return band over the declared horizon.

The probability is computed from the world model: each linked `mechanism`
contributes its Beta posterior (weighted by confidence) in log-odds space,
attenuated by the hypothesis's evidence quality. This is the only sanctioned
writer of `predictions`; the quant LLM turn only supplies *which* mechanisms
apply (via --link / --link-file) and never the number.

Mechanism linkage:
  * Preferred: the quant LLM passes `--link <hyp_id>=<mech_id>,<mech_id>` (repeatable)
    or `--link-file <json>` mapping hypothesis_id -> [mechanism_id, ...].
  * Fallback (headless cron): a deterministic keyword matcher links a hypothesis
    to any mechanism whose antecedent/consequent class tokens appear in the
    thesis summary, so the pass still produces a prediction unattended.

Idempotent: skips a hypothesis that already has an unresolved prediction unless
--force is given.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))

# Import the shared world-model math.
sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
import worldmodel as wm  # noqa: E402

HORIZONS = ("intraday", "swing_1_5d", "position_1_4w", "trend_1_3m", "long_6m_plus")
LEGACY_HORIZON_MAP = {
    "1d": "intraday", "intraday": "intraday",
    "1-3d": "swing_1_5d", "1-5d": "swing_1_5d", "swing": "swing_1_5d", "1w": "swing_1_5d",
    "1-4w": "position_1_4w", "weeks": "position_1_4w", "1m": "position_1_4w", "position": "position_1_4w",
    "1-3m": "trend_1_3m", "months": "trend_1_3m", "3m": "trend_1_3m", "trend": "trend_1_3m",
    "6m+": "long_6m_plus", "1y": "long_6m_plus", "long": "long_6m_plus",
}
_ROOT_STRUCT_TOKENS = {
    "gen", "multi", "long", "short", "hi", "lo",
    "quarter", "month", "63d", "21d", "5d",
    "chg", "level", "ttm", "yoy", "2m", "12", "1",
}

# Direction-conditional base rate (rp-base-rate-empirical-20260715): measured on
# 120 random universe names x monthly entries 2024-26 (n=3,480 15td windows),
# P(name beats SPY) = 0.4664 — the median stock loses to the cap-weighted index,
# so a long thesis's true "no-edge" chance is ~46.6% and a short's (under
# SPY-relative grading) ~53.4%. Re-measure quarterly (FINDINGS 2026-07-15,
# revalidate-by 2026-10-15).
BASE_RATE = {"long": 0.466, "short": 0.534}

# rp-mechanism-family-dedup-20260715: cap the total posterior shift from base
# until at least one linked mechanism family has this many observations —
# correlated generated mechanisms stacked as independent evidence produced
# p=0.78 within a week of the first observation cohort.
MAX_SHIFT_UNPROVEN = 0.15
PROVEN_FAMILY_N = 30.0
EXIT_OK = 0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hours_old(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def normalize_horizon(h: str | None) -> str:
    if not h:
        return "swing_1_5d"
    h = h.strip().lower()
    if h in HORIZONS:
        return h
    return LEGACY_HORIZON_MAP.get(h, "swing_1_5d")


def evidence_quality(conn: sqlite3.Connection, hyp_id: str) -> float:
    """[0,1] quality from evidence count, typing, url presence, freshness."""
    rows = conn.execute(
        "SELECT signal_type, source_url, retrieved_at FROM hypothesis_evidence "
        "WHERE hypothesis_id = ?",
        (hyp_id,),
    ).fetchall()
    if not rows:
        return 0.2  # thin evidence floor
    n = len(rows)
    typed = sum(1 for r in rows if r[0])
    urled = sum(1 for r in rows if r[1])
    fresh = 0
    for r in rows:
        age = _hours_old(r[2])
        if age is not None and age <= 30 * 24:
            fresh += 1
    completeness = min(1.0, n / 3.0)
    return round(0.4 * completeness + 0.2 * (typed / n) + 0.2 * (urled / n) + 0.2 * (fresh / n), 4)


def load_mechanisms(conn: sqlite3.Connection) -> dict[str, dict]:
    out = {}
    for r in conn.execute(
        "SELECT id, name, antecedent_class, consequent_class, direction, horizon, "
        "posterior_mean, posterior_ci_low, posterior_ci_high, status, "
        "observed_hits + observed_misses FROM mechanisms"
    ):
        out[r[0]] = {
            "id": r[0], "name": r[1], "antecedent_class": r[2], "consequent_class": r[3],
            "direction": r[4], "horizon": r[5], "posterior_mean": r[6],
            "ci_low": r[7], "ci_high": r[8], "status": r[9],
            "n_obs": float(r[10] or 0),
        }
    return out


def keyword_match(thesis: str, mech: dict) -> bool:
    text = thesis.lower()
    tokens = set()
    for field in ("antecedent_class", "consequent_class"):
        for part in (mech.get(field) or "").split("_"):
            if len(part) >= 4:
                tokens.add(part)
    hits = sum(1 for t in tokens if t in text)
    return hits >= 2


def thesis_direction(thesis: str) -> str:
    """Infer long/short stance from the thesis summary prose."""
    t = thesis.lower()
    if t.startswith("short") or "bearish" in t[:40] or t.startswith("short/"):
        return "short"
    return "long"


def mechanism_alignment(thesis_dir: str, mech_dir: str) -> int:
    """+1 if the mechanism supports the thesis, -1 if it opposes, 0 if neutral."""
    bullish = mech_dir in ("long", "risk_on")
    bearish = mech_dir in ("short", "risk_off")
    if not bullish and not bearish:
        return 0  # 'neutral' mechanism — treated as mild support downstream
    mech_for_long = bullish
    if thesis_dir == "long":
        return 1 if mech_for_long else -1
    return -1 if mech_for_long else 1


def _mechanism_root_family_key(mech_id: str, mech_dir: str) -> tuple[tuple[str, ...], str]:
    """Group mirrored generated mechanisms that share the same feature root.

    TM-243: the 30d worst linked Brier bucket came from "growth + momentum"
    signals where the deterministic linker attached both hi/lo variants of the
    same generated family. Treating those siblings as independent evidence kept
    p_correct too high for a weak cohort. Collapse them to one root family so
    one feature stack contributes one posterior shift.
    """
    base = (mech_id or "").split("__", 1)[0]
    tokens = tuple(
        token for token in base.split("_")
        if token and token not in _ROOT_STRUCT_TOKENS
    )
    return tokens, mech_dir


def _family_terms(
    mech_ids: list[str],
    mechs: dict[str, dict],
    thesis_dir: str,
    horizon: str,
    *,
    prefer_horizon: bool,
    family_mode: str = "root",
) -> tuple[list[tuple[float, float]], list[str], list[dict]]:
    """Collapse linked mechanisms to one sibling per family.

    Family siblings share the same antecedent/consequent/direction triplet. The
    old selector always took the most-observed sibling, which let a longer-dated
    family member override the exact-horizon sibling on a shorter forecast. When
    prefer_horizon=True, exact horizon match wins before observation count.
    """
    fam_best: dict[tuple, dict] = {}
    for mid in mech_ids:
        m = mechs.get(mid)
        if not m or m["posterior_mean"] is None:
            continue
        status_mult = 1.0 if m["status"] == "active" else 0.6
        weight = wm.confidence_weight(m["ci_low"], m["ci_high"]) * status_mult
        align = mechanism_alignment(thesis_dir, m["direction"])
        posterior = m["posterior_mean"]
        if align < 0:
            # Opposing mechanism: reflect the posterior so a strong opposing
            # mechanism pushes p_correct DOWN.
            effective = 1.0 - posterior
        else:
            effective = posterior
            if align == 0:
                weight *= 0.5  # neutral mechanism: mild support only
        if family_mode == "legacy_class":
            fam = (m["antecedent_class"], m["consequent_class"], m["direction"])
        else:
            fam = _mechanism_root_family_key(mid, m["direction"])
        cand = {
            "mid": mid,
            "align": align,
            "effective": effective,
            "weight": weight,
            "n_obs": m["n_obs"],
            "mech_horizon": normalize_horizon(m["horizon"]),
        }
        horizon_match = int(cand["mech_horizon"] == horizon)
        cand_rank = (horizon_match, cand["n_obs"]) if prefer_horizon else (cand["n_obs"],)
        if fam not in fam_best:
            fam_best[fam] = cand | {"rank": cand_rank}
            continue
        if cand_rank > fam_best[fam]["rank"]:
            fam_best[fam] = cand | {"rank": cand_rank}

    terms: list[tuple[float, float]] = []
    used: list[str] = []
    used_detail: list[dict] = []
    for cand in fam_best.values():
        terms.append((cand["effective"], cand["weight"]))
        used.append(cand["mid"])
        used_detail.append({
            "id": cand["mid"],
            "align": cand["align"],
            "eff": round(cand["effective"], 3),
            "w": round(cand["weight"], 3),
            "n": round(cand["n_obs"], 1),
            "horizon": cand["mech_horizon"],
        })
    return terms, used, used_detail


def build_prediction(
    conn: sqlite3.Connection,
    hyp: sqlite3.Row,
    mech_ids: list[str],
    mechs: dict[str, dict],
    regime: str | None,
    *,
    prefer_horizon: bool = True,
    family_mode: str = "root",
) -> dict:
    hyp_id, thesis, time_horizon = hyp[0], hyp[2], hyp[3]
    horizon = normalize_horizon(time_horizon)
    eq = evidence_quality(conn, hyp_id)
    tdir = thesis_direction(thesis)
    terms, used, used_detail = _family_terms(
        mech_ids,
        mechs,
        tdir,
        horizon,
        prefer_horizon=prefer_horizon,
        family_mode=family_mode,
    )

    base = BASE_RATE.get(tdir, 0.5)
    p_correct, combined_lo = wm.combine_p(base, terms, eq)
    max_n = max((d["n"] for d in used_detail), default=0.0)
    if max_n < PROVEN_FAMILY_N:
        lo_b, hi_b = base - MAX_SHIFT_UNPROVEN, base + MAX_SHIFT_UNPROVEN
        capped = min(max(p_correct, lo_b), hi_b)
        if capped != p_correct:
            p_correct = capped
            combined_lo = math.log(p_correct / (1.0 - p_correct))
    ticker = _first_ticker(hyp[5] if len(hyp) > 5 else None)
    mos, vconf, rvol = _valuation_for(conn, ticker)
    p10, p50, p90 = wm.return_band_v2(
        p_correct, horizon, realized_vol_annual=rvol,
        margin_of_safety=mos, val_confidence=vconf or 0.0,
    )
    return {
        "id": f"pred_{uuid.uuid4().hex[:12]}",
        "hypothesis_id": hyp_id,
        "predicted_at": _now(),
        "horizon": horizon,
        "thesis_direction": tdir,
        "p_correct": round(p_correct, 4),
        "return_p10": round(p10, 3),
        "return_p50": round(p50, 3),
        "return_p90": round(p90, 3),
        "mechanism_ids": used,
        "mechanism_detail": used_detail,
        "regime": regime,
        "evidence_quality": eq,
        "prior_log_odds": round(combined_lo, 4),
    }


def _first_ticker(tickers_json: str | None) -> str | None:
    if not tickers_json:
        return None
    try:
        arr = json.loads(tickers_json)
        return str(arr[0]).upper() if arr else None
    except (json.JSONDecodeError, TypeError, IndexError):
        return None


def _valuation_for(conn: sqlite3.Connection, ticker: str | None) -> tuple[float | None, float | None, float | None]:
    """(margin_of_safety, confidence, realized_vol_annual) from the latest
    applicable valuation for `ticker`, or (None, None, None). The valuations table
    is optional — predictions degrade to the generic band if it's empty/absent."""
    if not ticker:
        return None, None, None
    try:
        row = conn.execute(
            "SELECT margin_of_safety, confidence, realized_vol_annual FROM valuations "
            "WHERE ticker = ? AND applicable = 1 ORDER BY as_of DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    except sqlite3.Error:
        return None, None, None
    if not row:
        return None, None, None
    return row[0], row[1], row[2]


def predict_one(
    conn: sqlite3.Connection,
    hyp: sqlite3.Row,
    mech_ids: list[str],
    mechs: dict[str, dict],
    regime: str | None,
    experiment_id: str | None,
    dry_run: bool,
) -> dict:
    pred = build_prediction(conn, hyp, mech_ids, mechs, regime, prefer_horizon=True)
    if dry_run:
        return pred

    conn.execute(
        "INSERT INTO predictions (id, hypothesis_id, predicted_at, predicted_by, "
        "horizon, p_correct, return_p10, return_p50, return_p90, mechanism_ids_json, "
        "regime_at_prediction, evidence_quality, prior_log_odds, experiment_id) "
        "VALUES (?, ?, ?, 'quant', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pred["id"], pred["hypothesis_id"], pred["predicted_at"], pred["horizon"], pred["p_correct"],
            pred["return_p10"], pred["return_p50"], pred["return_p90"],
            json.dumps(pred["mechanism_detail"]), pred["regime"], pred["evidence_quality"],
            pred["prior_log_odds"], experiment_id,
        ),
    )
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise, journal_ref, experiment_id) "
        "VALUES (?, ?, 'quant', 'hypothesis', ?, 'prediction', NULL, NULL, ?, NULL, ?)",
        (
            f"audit_{uuid.uuid4().hex[:12]}", pred["predicted_at"], pred["hypothesis_id"],
            f"p={pred['p_correct']:.2f} band=[{pred['return_p10']:.1f},{pred['return_p50']:.1f},"
            f"{pred['return_p90']:.1f}]% mech={','.join(pred['mechanism_ids']) or 'none'} "
            f"eq={pred['evidence_quality']:.2f}"[:500],
            experiment_id,
        ),
    )
    conn.commit()
    return pred


def parse_links(link_args: list[str], link_file: str | None) -> dict[str, list[str]]:
    links: dict[str, list[str]] = {}
    if link_file:
        data = json.loads(Path(link_file).read_text())
        for k, v in data.items():
            links[k] = list(v)
    for spec in link_args or []:
        if "=" not in spec:
            continue
        hyp_id, mids = spec.split("=", 1)
        links[hyp_id.strip()] = [m.strip() for m in mids.split(",") if m.strip()]
    return links


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--states", default="scored,challenged,ready",
                    help="hypothesis states eligible for a prediction")
    ap.add_argument("--link", action="append", default=[],
                    help="hyp_id=mech_id,mech_id (repeatable)")
    ap.add_argument("--link-file", default=None, help="JSON {hyp_id: [mech_id,...]}")
    ap.add_argument("--experiment-id", default=None)
    ap.add_argument("--force", action="store_true",
                    help="re-predict even if an unresolved prediction exists")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = None
    mechs = load_mechanisms(conn)
    regime_row = conn.execute("SELECT current FROM regime_current").fetchone()
    regime = regime_row[0] if regime_row else None
    links = parse_links(args.link, args.link_file)

    states = tuple(s.strip() for s in args.states.split(",") if s.strip())
    placeholders = ",".join("?" for _ in states)
    rows = conn.execute(
        f"SELECT id, created_at, thesis_summary, time_horizon, state, tickers FROM hypotheses "
        f"WHERE state IN ({placeholders}) ORDER BY scored_at DESC LIMIT ?",
        (*states, args.limit),
    ).fetchall()

    emitted, skipped = [], []
    for hyp in rows:
        hyp_id = hyp[0]
        if not args.force:
            existing = conn.execute(
                "SELECT 1 FROM predictions WHERE hypothesis_id = ? AND resolved_at IS NULL LIMIT 1",
                (hyp_id,),
            ).fetchone()
            if existing:
                skipped.append(hyp_id)
                continue
        mech_ids = links.get(hyp_id)
        if mech_ids is None:
            # D57: deterministic three-tier linker (name/feature/class vs thesis +
            # evidence indicators) — the old prose-only keyword_match linked ~nothing.
            sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
            import link_mechanisms as _lm
            _text = _lm.hypothesis_text(conn, hyp[0], hyp[2])
            mech_ids = [e["id"] for e in _lm.link(_text, list(mechs.values()), hyp[3])]
        pred = predict_one(conn, hyp, mech_ids, mechs, regime, args.experiment_id, args.dry_run)
        emitted.append(pred)

    conn.close()
    print(json.dumps({
        "predicted": len(emitted),
        "skipped_existing": len(skipped),
        "regime": regime,
        "dry_run": args.dry_run,
        "predictions": emitted,
    }, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
