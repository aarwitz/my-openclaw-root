#!/usr/bin/env python3
"""Point-in-time counterfactual attribution for the AutoTrade selection funnel.

Trade attribution can say whether a filled position beat SPY, but it cannot say
which upstream stage helped: rejected hypotheses never became positions.  This
tool grades every genuine research candidate at fixed close-to-close horizons
and freezes the pipeline state as it stood at the counterfactual entry close.

The grader is deliberately offline.  Ticker returns come from the point-in-time
feature store and SPY closes come from the bounded Massive cache.  Missing data
is reported, never fetched or guessed.

Usage:
  selection_funnel_attribution.py backfill [--dry-run]
  selection_funnel_attribution.py report [--horizon 5d]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
LIVE_DB = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
FEATURE_DB = Path(os.path.expanduser("~/.openclaw/state/features.sqlite"))

sys.path.insert(0, str(ROOT / "workspaces/trading-intel/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from connectors import marketdata, massive  # noqa: E402
from developer_db import audit, connect, now_iso  # noqa: E402
import symbol_lifecycle  # noqa: E402

HORIZONS = {"5d": 5, "21d": 21, "63d": 63}
CANDIDATE_AUTHORS = frozenset({"researcher", "quant", "critic", "human", "overseer"})
STAGES = (
    ("researched", None),
    ("quant_scored", "quant_scored"),
    ("critic_substantive", "critic_substantive_passed"),
    ("predicted", "predicted"),
    ("trader_authored", "intent_authored"),
    ("risk_approved", "risk_approved"),
    ("filled", "filled"),
)
MIN_STAGE_ARM_DATES = 5


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = str(value).strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _before(value: str | None, cutoff: datetime) -> bool:
    parsed = _parse_iso(value)
    return parsed is not None and parsed <= cutoff


def _direction(summary: str | None, intent_direction: str | None = None) -> str:
    if str(intent_direction or "").lower() in {"long", "short"}:
        return str(intent_direction).lower()
    text = str(summary or "").strip().lower()
    if text.startswith("short") or text.startswith("bearish") or " short " in f" {text[:80]} ":
        return "short"
    return "long"


def _primary_ticker(raw: str, decision_at: str) -> tuple[str | None, int]:
    try:
        tickers = [str(x).upper() for x in json.loads(raw or "[]") if str(x).strip()]
    except (json.JSONDecodeError, TypeError):
        tickers = []
    if not tickers:
        return None, 0
    decision = _parse_iso(decision_at)
    as_of = decision.date().isoformat() if decision else None
    return symbol_lifecycle.canonical_symbol(tickers[0], as_of), len(tickers)


def _entry_date(decision_at: str, available_dates: list[str]) -> str | None:
    """First close at which the decision could be followed without look-ahead."""
    decision = _parse_iso(decision_at)
    if decision is None or not available_dates:
        return None
    et = marketdata._et()
    decision_et = decision.astimezone(et)
    day = decision_et.date()
    day_s = day.isoformat()
    available = set(available_dates)
    if day_s in available and marketdata.is_trading_day(day_s):
        close = datetime.combine(day, marketdata._close_time(day), et)
        if decision_et <= close:
            return day_s
    return next((value for value in available_dates if value > day_s), None)


def _entry_cutoff(entry_date: str) -> datetime:
    day = date.fromisoformat(entry_date)
    local = datetime.combine(day, marketdata._close_time(day), marketdata._et())
    return local.astimezone(timezone.utc)


def _spy_closes_from_cache(max_age_h: float = 168.0) -> dict[str, float]:
    bars = massive.cached_daily_bars("SPY", max_age_h=max_age_h)
    return {
        str(row["t"])[:10]: float(row["c"])
        for row in bars
        if row.get("t") and row.get("c") is not None
        and marketdata.daily_bar_complete(str(row["t"])[:10])
    }


def _ticker_returns(conn: sqlite3.Connection, ticker: str, start: str) -> dict[str, float]:
    rows = conn.execute(
        "SELECT as_of,value FROM features WHERE ticker=? AND name='ret_1d' "
        "AND source='price' AND as_of>=? ORDER BY as_of",
        (ticker.upper(), start),
    ).fetchall()
    return {
        str(row[0])[:10]: float(row[1])
        for row in rows
        if row[1] is not None and math.isfinite(float(row[1]))
        and marketdata.daily_bar_complete(str(row[0])[:10])
    }


def counterfactual_outcome(
    *,
    decision_at: str,
    direction: str,
    sessions: int,
    ticker_returns: dict[str, float],
    spy_closes: dict[str, float],
) -> dict:
    available_dates = sorted(ticker_returns)
    entry = _entry_date(decision_at, available_dates)
    if entry is None:
        return {
            "status": "pending",
            "reason": "awaiting_tradable_entry_close",
            "entry_date": None,
            "exit_date": None,
        }
    if entry not in spy_closes:
        # A fresh ticker close can land before the bounded SPY cache refreshes.
        # That is an unripe benchmark window, not a permanent data hole. Only
        # call it blocked when SPY has already advanced beyond the entry date
        # while that exact close remains absent.
        if not spy_closes or entry > max(spy_closes):
            return {
                "status": "pending",
                "reason": "awaiting_spy_entry_close",
                "entry_date": entry,
                "exit_date": None,
            }
        return {
            "status": "data_blocked",
            "reason": "missing_spy_entry_close",
            "entry_date": entry,
            "exit_date": None,
        }
    spy_sessions = sorted(day for day in spy_closes if day > entry)
    if len(spy_sessions) < sessions:
        return {
            "status": "pending",
            "reason": f"horizon_unmatured:{len(spy_sessions)}/{sessions}",
            "entry_date": entry,
            "exit_date": None,
        }
    outcome_dates = spy_sessions[:sessions]
    missing = [day for day in outcome_dates if day not in ticker_returns]
    if missing:
        return {
            "status": "data_blocked",
            "reason": "missing_ticker_sessions:" + ",".join(missing[:5]),
            "entry_date": entry,
            "exit_date": outcome_dates[-1],
        }
    cumulative = 1.0
    for day in outcome_dates:
        daily_pct = ticker_returns[day]
        if abs(daily_pct) > 100:
            return {
                "status": "data_blocked",
                "reason": f"implausible_daily_return:{day}:{daily_pct}",
                "entry_date": entry,
                "exit_date": outcome_dates[-1],
            }
        cumulative *= 1.0 + daily_pct / 100.0
    raw = (cumulative - 1.0) * 100.0
    spy = (spy_closes[outcome_dates[-1]] / spy_closes[entry] - 1.0) * 100.0
    excess = raw - spy
    directional = excess if direction == "long" else -excess
    return {
        "status": "matured",
        "reason": None,
        "entry_date": entry,
        "exit_date": outcome_dates[-1],
        "raw_return_pct": round(raw, 6),
        "spy_return_pct": round(spy, 6),
        "directional_excess_pct": round(directional, 6),
    }


def _latest_before(rows: list[sqlite3.Row], field: str, cutoff: datetime) -> sqlite3.Row | None:
    eligible = [row for row in rows if _before(row[field], cutoff)]
    if not eligible:
        return None
    return max(eligible, key=lambda row: _parse_iso(row[field]) or datetime.min.replace(tzinfo=timezone.utc))


def stage_snapshot(conn: sqlite3.Connection, hyp: sqlite3.Row, entry_date: str | None) -> dict:
    cutoff = _entry_cutoff(entry_date) if entry_date else datetime.now(timezone.utc)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    scored = bool(hyp["quant_score"] is not None and _before(hyp["scored_at"], cutoff))
    critic_rows = conn.execute(
        "SELECT reviewed_at,reviewed_by,all_challenges_addressed FROM critic_reviews "
        "WHERE target_type='hypothesis' AND target_id=?",
        (hyp["id"],),
    ).fetchall()
    latest_critic = _latest_before(critic_rows, "reviewed_at", cutoff)
    substantive_rows = [row for row in critic_rows if row["reviewed_by"] != "critic_baseline"]
    latest_substantive = _latest_before(substantive_rows, "reviewed_at", cutoff)
    critic_passed = bool(latest_critic and int(latest_critic["all_challenges_addressed"] or 0))
    critic_substantive = bool(
        latest_substantive and int(latest_substantive["all_challenges_addressed"] or 0)
    )

    prediction_rows = conn.execute(
        "SELECT id,predicted_at,p_correct,return_p50,evidence_quality,horizon,"
        "mechanism_ids_json FROM predictions WHERE hypothesis_id=?",
        (hyp["id"],),
    ).fetchall()
    prediction = _latest_before(prediction_rows, "predicted_at", cutoff)

    intent_rows = conn.execute(
        "SELECT id,created_at,state,direction,size,blocked_reason FROM trade_intents "
        "WHERE hypothesis_id=? AND action IN ('open','add')",
        (hyp["id"],),
    ).fetchall()
    intent = _latest_before(intent_rows, "created_at", cutoff)
    eligible_intent_ids = [row["id"] for row in intent_rows if _before(row["created_at"], cutoff)]

    risk = None
    fills: list[sqlite3.Row] = []
    if eligible_intent_ids:
        marks = ",".join("?" for _ in eligible_intent_ids)
        risk_rows = conn.execute(
            f"SELECT target_id,reviewed_at,verdict,approved_size,breaches_json "
            f"FROM risk_reviews WHERE target_type='trade_intent' "
            f"AND target_id IN ({marks})",
            eligible_intent_ids,
        ).fetchall()
        risk = _latest_before(risk_rows, "reviewed_at", cutoff)
        fills = conn.execute(
            f"SELECT trade_intent_id,filled_at,avg_fill_price,qty,status FROM orders "
            f"WHERE trade_intent_id IN ({marks}) AND status='filled'",
            eligible_intent_ids,
        ).fetchall()
    eligible_fills = [row for row in fills if _before(row["filled_at"], cutoff)]
    fill = max(
        eligible_fills,
        key=lambda row: _parse_iso(row["filled_at"]) or datetime.min.replace(tzinfo=timezone.utc),
        default=None,
    )

    try:
        multi_ticker_count = len(json.loads(hyp["tickers"] or "[]"))
    except (json.JSONDecodeError, TypeError):
        multi_ticker_count = 0

    return {
        "cutoff": cutoff_iso,
        "created_by": hyp["created_by"],
        "multi_ticker_count": multi_ticker_count,
        "quant_scored": int(scored),
        "quant": {
            "score": hyp["quant_score"] if scored else None,
            "scored_at": hyp["scored_at"] if scored else None,
        },
        "critic_passed": int(critic_passed),
        "critic_substantive_passed": int(critic_substantive),
        "critic": None if latest_substantive is None else {
            "reviewed_at": latest_substantive["reviewed_at"],
            "reviewed_by": latest_substantive["reviewed_by"],
            "passed": bool(latest_substantive["all_challenges_addressed"]),
        },
        "predicted": int(prediction is not None),
        "prediction": None if prediction is None else {
            "id": prediction["id"],
            "predicted_at": prediction["predicted_at"],
            "p_correct": prediction["p_correct"],
            "return_p50": prediction["return_p50"],
            "evidence_quality": prediction["evidence_quality"],
            "horizon": prediction["horizon"],
            "mechanism_ids": json.loads(prediction["mechanism_ids_json"] or "[]"),
        },
        "intent_authored": int(intent is not None),
        "intent": None if intent is None else {
            "id": intent["id"],
            "created_at": intent["created_at"],
            "state": intent["state"],
            "direction": intent["direction"],
            "size": intent["size"],
            "blocked_reason": intent["blocked_reason"],
        },
        "risk_approved": int(risk is not None and risk["verdict"] in {"approved", "resized"}),
        "risk": None if risk is None else {
            "reviewed_at": risk["reviewed_at"],
            "verdict": risk["verdict"],
            "approved_size": risk["approved_size"],
            "breaches": json.loads(risk["breaches_json"] or "[]"),
        },
        "filled": int(fill is not None),
        "fill": None if fill is None else {
            "filled_at": fill["filled_at"],
            "price": fill["avg_fill_price"],
            "qty": fill["qty"],
        },
    }


def _stable_id(hypothesis_id: str, ticker: str, horizon: str) -> str:
    digest = hashlib.sha256(f"{hypothesis_id}|{ticker}|{horizon}".encode()).hexdigest()[:20]
    return "sfo-" + digest


def build_rows(
    conn: sqlite3.Connection,
    feature_conn: sqlite3.Connection,
    spy_closes: dict[str, float],
    horizons: dict[str, int] | None = None,
) -> tuple[list[dict], dict]:
    horizons = horizons or HORIZONS
    rows: list[dict] = []
    excluded = defaultdict(int)
    price_cache: dict[tuple[str, str], dict[str, float]] = {}
    hypotheses = conn.execute("SELECT * FROM hypotheses ORDER BY created_at,id").fetchall()
    for hyp in hypotheses:
        if hyp["created_by"] not in CANDIDATE_AUTHORS:
            excluded[f"created_by:{hyp['created_by']}"] += 1
            continue
        ticker, ticker_count = _primary_ticker(hyp["tickers"], hyp["created_at"])
        if not ticker:
            excluded["missing_primary_ticker"] += 1
            continue
        start = (_parse_iso(hyp["created_at"]) or datetime.now(timezone.utc)).date().isoformat()
        key = (ticker, start)
        if key not in price_cache:
            price_cache[key] = _ticker_returns(feature_conn, ticker, start)
        ticker_returns = price_cache[key]
        tentative_entry = _entry_date(hyp["created_at"], sorted(ticker_returns))
        snapshot = stage_snapshot(conn, hyp, tentative_entry)
        direction = _direction(
            hyp["thesis_summary"],
            (snapshot.get("intent") or {}).get("direction"),
        )
        snapshot["multi_ticker_count"] = ticker_count
        for horizon, sessions in horizons.items():
            outcome = counterfactual_outcome(
                decision_at=hyp["created_at"],
                direction=direction,
                sessions=sessions,
                ticker_returns=ticker_returns,
                spy_closes=spy_closes,
            )
            rows.append({
                "id": _stable_id(hyp["id"], ticker, horizon),
                "hypothesis_id": hyp["id"],
                "ticker": ticker,
                "direction": direction,
                "evaluation_horizon": horizon,
                "sessions": sessions,
                "decision_at": hyp["created_at"],
                "entry_date": outcome.get("entry_date"),
                "exit_date": outcome.get("exit_date"),
                "outcome_status": outcome["status"],
                "data_reason": outcome.get("reason"),
                "raw_return_pct": outcome.get("raw_return_pct"),
                "spy_return_pct": outcome.get("spy_return_pct"),
                "directional_excess_pct": outcome.get("directional_excess_pct"),
                "quant_scored": snapshot["quant_scored"],
                "critic_passed": snapshot["critic_passed"],
                "critic_substantive_passed": snapshot["critic_substantive_passed"],
                "predicted": snapshot["predicted"],
                "intent_authored": snapshot["intent_authored"],
                "risk_approved": snapshot["risk_approved"],
                "filled": snapshot["filled"],
                "stage_snapshot_json": json.dumps(snapshot, sort_keys=True),
                "computed_at": now_iso(),
            })
    return rows, {"excluded": dict(excluded), "candidate_hypotheses": len(hypotheses) - sum(excluded.values())}


_WRITE_COLUMNS = (
    "id", "hypothesis_id", "ticker", "direction", "evaluation_horizon", "sessions",
    "decision_at", "entry_date", "exit_date", "outcome_status", "data_reason",
    "raw_return_pct", "spy_return_pct", "directional_excess_pct", "quant_scored",
    "critic_passed", "critic_substantive_passed", "predicted", "intent_authored",
    "risk_approved", "filled", "stage_snapshot_json", "computed_at",
)


def _same_outcome(existing: sqlite3.Row, row: dict) -> bool:
    for field in ("raw_return_pct", "spy_return_pct", "directional_excess_pct"):
        left, right = existing[field], row[field]
        if left is None or right is None or abs(float(left) - float(right)) > 1e-6:
            return False
    return existing["entry_date"] == row["entry_date"] and existing["exit_date"] == row["exit_date"]


def write_rows(conn: sqlite3.Connection, rows: list[dict], *, dry_run: bool = False) -> dict:
    report = defaultdict(int)
    revision_samples: list[str] = []
    placeholders = ",".join("?" for _ in _WRITE_COLUMNS)
    updates = ",".join(
        f"{column}=excluded.{column}"
        for column in _WRITE_COLUMNS
        if column not in {"id", "hypothesis_id", "ticker", "evaluation_horizon"}
    )
    sql = (
        f"INSERT INTO selection_funnel_outcomes ({','.join(_WRITE_COLUMNS)}) "
        f"VALUES ({placeholders}) ON CONFLICT(hypothesis_id,ticker,evaluation_horizon) "
        f"DO UPDATE SET {updates} "
        "WHERE selection_funnel_outcomes.outcome_status!='matured'"
    )
    for row in rows:
        existing = conn.execute(
            "SELECT * FROM selection_funnel_outcomes WHERE hypothesis_id=? AND ticker=? "
            "AND evaluation_horizon=?",
            (row["hypothesis_id"], row["ticker"], row["evaluation_horizon"]),
        ).fetchone()
        if existing and existing["outcome_status"] == "matured":
            if row["outcome_status"] == "matured" and not _same_outcome(existing, row):
                # Corrected/vendor-restated source rows must never rewrite a
                # frozen counterfactual. Preserve the original and continue
                # refreshing the rest of the funnel; record the drift instead
                # of turning one revision into a total learning-loop outage.
                report["matured_revision_refused"] += 1
                if len(revision_samples) < 10:
                    revision_samples.append(
                        f"{row['hypothesis_id']}:{row['ticker']}:{row['evaluation_horizon']}"
                    )
                continue
            report["frozen_matured"] += 1
            continue
        report["would_write" if dry_run else "written"] += 1
        report[row["outcome_status"]] += 1
        if not dry_run:
            conn.execute(sql, tuple(row[column] for column in _WRITE_COLUMNS))
    if not dry_run and report["matured_revision_refused"]:
        audit(
            conn,
            entity_type="selection_funnel",
            entity_id="selection-funnel-backfill",
            action="counterfactual_revision_refused",
            rationale=(
                f"preserved {report['matured_revision_refused']} immutable matured rows; "
                f"source recomputation differed: {','.join(revision_samples)}"
            ),
        )
    if not dry_run and report["written"]:
        audit(
            conn,
            entity_type="selection_funnel",
            entity_id="selection-funnel-backfill",
            action="counterfactual_grade",
            rationale=(
                f"wrote {report['written']} rows: matured={report['matured']} "
                f"pending={report['pending']} data_blocked={report['data_blocked']}"
            ),
        )
    if not dry_run and (report["written"] or report["matured_revision_refused"]):
        conn.commit()
    result = dict(report)
    if revision_samples:
        result["revision_samples"] = revision_samples
    return result


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _metrics(rows: list[sqlite3.Row]) -> dict:
    values = [float(row["directional_excess_pct"]) for row in rows]
    clustered: dict[str, list[float]] = defaultdict(list)
    for row, value in zip(rows, values):
        clustered[str(row["entry_date"])].append(value)
    date_means = [statistics.fmean(group) for group in clustered.values()]
    t_stat = None
    if len(date_means) >= 2:
        std = statistics.stdev(date_means)
        if std > 0:
            t_stat = statistics.fmean(date_means) / (std / math.sqrt(len(date_means)))
    return {
        "n": len(values),
        "independent_entry_dates": len(date_means),
        "mean_directional_excess_pct": None if not values else round(statistics.fmean(values), 4),
        "median_directional_excess_pct": None if not values else round(statistics.median(values), 4),
        "hit_rate_pct": None if not values else round(100 * sum(v > 0 for v in values) / len(values), 1),
        "date_cluster_t_stat": None if t_stat is None else round(t_stat, 3),
    }


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in pairs)
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return None if den == 0 else num / den


def _eventual_stage_members(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """Retrospective process status used only to separate selection from latency.

    Point-in-time flags remain frozen and authoritative. These sets answer a
    different question: did a zero-flag candidate reach the stage later? They
    never alter an outcome row or trading decision.
    """
    queries = {
        "quant_scored": (
            "SELECT id FROM hypotheses WHERE quant_score IS NOT NULL AND scored_at IS NOT NULL"
        ),
        "critic_substantive_passed": (
            "SELECT DISTINCT target_id FROM critic_reviews WHERE target_type='hypothesis' "
            "AND reviewed_by!='critic_baseline' AND all_challenges_addressed=1"
        ),
        "predicted": "SELECT DISTINCT hypothesis_id FROM predictions",
        "intent_authored": (
            "SELECT DISTINCT hypothesis_id FROM trade_intents WHERE action IN ('open','add')"
        ),
        "risk_approved": (
            "SELECT DISTINCT ti.hypothesis_id FROM risk_reviews rr JOIN trade_intents ti "
            "ON ti.id=rr.target_id WHERE rr.target_type='trade_intent' "
            "AND rr.verdict IN ('approved','resized') AND ti.action IN ('open','add')"
        ),
        "filled": (
            "SELECT DISTINCT ti.hypothesis_id FROM orders o JOIN trade_intents ti "
            "ON ti.id=o.trade_intent_id WHERE o.status='filled' AND ti.action IN ('open','add')"
        ),
    }
    result: dict[str, set[str]] = {}
    for stage, query in queries.items():
        try:
            result[stage] = {str(row[0]) for row in conn.execute(query) if row[0] is not None}
        except sqlite3.Error:
            # Hermetic/report consumers may expose only the frozen outcome
            # table. Unknown eventual status stays "not reached as of report".
            result[stage] = set()
    return result


def _spread_bps(left: dict, right: dict) -> float | None:
    left_mean = left["mean_directional_excess_pct"]
    right_mean = right["mean_directional_excess_pct"]
    if left_mean is None or right_mean is None:
        return None
    return round((left_mean - right_mean) * 100.0, 1)


def funnel_report(conn: sqlite3.Connection, horizon: str) -> dict:
    if horizon not in HORIZONS:
        raise ValueError(f"unknown horizon {horizon!r}")
    rows = conn.execute(
        "SELECT * FROM selection_funnel_outcomes WHERE evaluation_horizon=? "
        "AND outcome_status='matured' ORDER BY entry_date,hypothesis_id",
        (horizon,),
    ).fetchall()
    eventual = _eventual_stage_members(conn)
    current = rows
    stages = []
    for name, flag in STAGES:
        incoming = current
        if flag is None:
            kept, rejected, late, not_reached = incoming, [], [], []
        else:
            kept = [row for row in incoming if int(row[flag] or 0) == 1]
            rejected = [row for row in incoming if int(row[flag] or 0) == 0]
            late = [row for row in rejected if str(row["hypothesis_id"]) in eventual.get(flag, set())]
            not_reached = [
                row for row in rejected if str(row["hypothesis_id"]) not in eventual.get(flag, set())
            ]
        kept_metrics = _metrics(kept)
        rejected_metrics = _metrics(rejected)
        late_metrics = _metrics(late)
        not_reached_metrics = _metrics(not_reached)
        aggregate_spread = _spread_bps(kept_metrics, rejected_metrics)
        selection_spread = _spread_bps(kept_metrics, not_reached_metrics)
        latency_spread = _spread_bps(kept_metrics, late_metrics)
        inference_eligible = bool(
            kept_metrics["independent_entry_dates"] >= MIN_STAGE_ARM_DATES
            and not_reached_metrics["independent_entry_dates"] >= MIN_STAGE_ARM_DATES
        )
        stages.append({
            "stage": name,
            "input_n": len(incoming),
            "kept": kept_metrics,
            # A zero flag can mean explicit rejection or merely that the stage
            # had not run by this point-in-time cutoff. Keep the label honest.
            "not_selected_at_cutoff": rejected_metrics,
            "reached_after_cutoff": late_metrics,
            "not_reached_as_of_report": not_reached_metrics,
            "selection_spread_bps": selection_spread,
            "latency_spread_bps": latency_spread,
            "selection_inference_eligible": inference_eligible,
            # Retained for GUI compatibility; never used to rank stages.
            "selection_or_latency_spread_bps": aggregate_spread,
        })
        current = kept

    diagnostics = {"quant_score_corr_excess": None, "p_correct_corr_excess": None}
    quant_pairs: list[tuple[float, float]] = []
    probability_pairs: list[tuple[float, float]] = []
    by_source: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        snapshot = json.loads(row["stage_snapshot_json"] or "{}")
        excess = float(row["directional_excess_pct"])
        score = (snapshot.get("quant") or {}).get("score")
        probability = (snapshot.get("prediction") or {}).get("p_correct")
        if score is not None:
            quant_pairs.append((float(score), excess))
        if probability is not None:
            probability_pairs.append((float(probability), excess))
        by_source[str(snapshot.get("created_by") or "unknown")].append(row)
    q_corr = _pearson(quant_pairs)
    p_corr = _pearson(probability_pairs)
    diagnostics["quant_score_corr_excess"] = None if q_corr is None else round(q_corr, 4)
    diagnostics["p_correct_corr_excess"] = None if p_corr is None else round(p_corr, 4)
    diagnostics["by_created_by"] = {key: _metrics(value) for key, value in sorted(by_source.items())}

    eligible = [
        stage for stage in stages[1:]
        if stage["selection_inference_eligible"] and stage["selection_spread_bps"] is not None
    ]
    harmful = [stage for stage in eligible if stage["selection_spread_bps"] < 0]
    helpful = [stage for stage in eligible if stage["selection_spread_bps"] > 0]
    worst = min(harmful, key=lambda stage: stage["selection_spread_bps"], default=None)
    best = max(helpful, key=lambda stage: stage["selection_spread_bps"], default=None)
    return {
        "generated_at": now_iso(),
        "horizon": horizon,
        "sessions": HORIZONS[horizon],
        "matured_candidates": len(rows),
        "stages": stages,
        "diagnostics": diagnostics,
        "most_harmful_measured_stage": None if worst is None else {
            "stage": worst["stage"],
            "selection_spread_bps": worst["selection_spread_bps"],
        },
        "most_helpful_measured_stage": None if best is None else {
            "stage": best["stage"],
            "selection_spread_bps": best["selection_spread_bps"],
        },
        "inference_warning": (
            "Point-in-time zero flags are split into later-reached latency and not-reached-as-of-report. "
            f"Stage rankings require at least {MIN_STAGE_ARM_DATES} independent entry dates in both "
            "selected and not-reached arms. Overlapping names/horizons and the short system era still "
            "preclude production-edge claims."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    backfill = sub.add_parser("backfill")
    backfill.add_argument("--dry-run", action="store_true")
    backfill.add_argument("--horizons", default=",".join(HORIZONS))
    report = sub.add_parser("report")
    report.add_argument("--horizon", choices=sorted(HORIZONS), default="5d")
    args = parser.parse_args(argv)

    conn = connect()
    if args.command == "report":
        print(json.dumps(funnel_report(conn, args.horizon), indent=2))
        return 0

    selected = [item.strip() for item in args.horizons.split(",") if item.strip()]
    unknown = sorted(set(selected) - set(HORIZONS))
    if unknown:
        parser.error("unknown horizons: " + ",".join(unknown))
    if not FEATURE_DB.exists():
        raise FileNotFoundError(f"feature DB missing at {FEATURE_DB}")
    feature_conn = sqlite3.connect(f"file:{FEATURE_DB}?mode=ro", uri=True, timeout=60)
    feature_conn.execute("PRAGMA query_only=ON")
    spy_closes = _spy_closes_from_cache()
    if not spy_closes:
        raise RuntimeError("offline SPY cache unavailable; refusing network fallback")
    rows, coverage = build_rows(
        conn,
        feature_conn,
        spy_closes,
        {horizon: HORIZONS[horizon] for horizon in selected},
    )
    write_report = write_rows(conn, rows, dry_run=args.dry_run)
    status_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        status_counts[row["outcome_status"]] += 1
    print(json.dumps({
        "generated_at": now_iso(),
        "dry_run": bool(args.dry_run),
        "offline": True,
        "coverage": coverage,
        "rows_built": len(rows),
        "status": dict(status_counts),
        "write": write_report,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
