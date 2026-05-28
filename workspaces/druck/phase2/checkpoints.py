from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import ats_v6, db, intraday_alpha
from .http_util import now_iso

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "sql" / "ats_v6.db"
CHECKPOINTS = {"preopen_0900", "morning_1100", "rerank_1330", "close_1530"}
ENTRY_STYLE_MAP = {"market": "market", "limit": "limit_mid", "skip": "limit_passive"}


@dataclass
class CheckpointResult:
    run_id: str
    checkpoint_name: str
    run_ts: str
    status: str
    created_intent_count: int
    warning_count: int
    warnings: list[str]
    top_replacements: list[dict[str, Any]]
    top_rotations: list[dict[str, Any]]


def _connect(db_path: Path = DEFAULT_DB_PATH, *, write: bool = False) -> sqlite3.Connection:
    ats_v6.initialize_database(db_path)
    return db.connect(db_path, write=write)


def _normalize_name(name: str) -> str:
    normalized = name.strip().lower().replace("-", "_")
    if normalized not in CHECKPOINTS:
        raise ValueError(f"unsupported checkpoint '{name}'")
    return normalized


def _cash_hurdle_pass(idea: dict[str, Any]) -> bool:
    return float(idea.get("forward_alpha_score") or 0) >= 55.0


def _idea_status(idea: dict[str, Any]) -> str:
    rec = idea.get("recommendation_class")
    if rec == "buy_ready":
        return "staged"
    if rec == "conditional_buy":
        return "watch"
    return "rejected"


def _intent_status(idea: dict[str, Any]) -> str:
    rec = idea.get("recommendation_class")
    if rec == "buy_ready" and _cash_hurdle_pass(idea):
        return "ready"
    if rec == "conditional_buy":
        return "pending"
    return "blocked"


def _blocked_reason(idea: dict[str, Any]) -> Optional[str]:
    rec = idea.get("recommendation_class")
    if rec == "buy_ready" and _cash_hurdle_pass(idea):
        return None
    if rec == "conditional_buy":
        return "needs cleaner setup or timing confirmation"
    return "fails recommendation or cash hurdle"


def _target_size_pct(idea: dict[str, Any]) -> float:
    confidence = (idea.get("entry_plan") or {}).get("confidence")
    return 2.0 if confidence == "high" else 1.5


def _upsert_idea_and_intent(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    run_ts: str,
    checkpoint_name: str,
    idea: dict[str, Any],
) -> None:
    ticker = str(idea["ticker"]).upper()
    idea_id = f"{run_id}:idea:{ticker}"
    intent_id = f"{run_id}:intent:{ticker}"
    strategy_id = f"checkpoint_{checkpoint_name}"
    cash_pass = 1 if _cash_hurdle_pass(idea) else 0

    conn.execute(
        """
        INSERT OR REPLACE INTO ideas
        (idea_id, opened_ts, ticker, direction, primary_strategy_id, status,
         aggregate_score, corroboration_count, cash_hurdle_pass, expected_holding_days,
         expected_return_low, expected_return_base, expected_return_high, risk_flags_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            idea_id,
            run_ts,
            ticker,
            "long",
            strategy_id,
            _idea_status(idea),
            float(idea.get("forward_alpha_score") or 0),
            len(idea.get("source_tags") or []),
            cash_pass,
            5,
            None,
            round(float(idea.get("forward_alpha_score") or 0) / 1000.0, 4),
            round(float(idea.get("forward_alpha_score") or 0) / 500.0, 4),
            json.dumps([idea.get("dominant_risk")] if idea.get("dominant_risk") else []),
        ),
    )

    entry_plan = idea.get("entry_plan") or {}
    conn.execute(
        """
        INSERT OR REPLACE INTO trade_intents
        (intent_id, created_ts, idea_id, strategy_id, variant_id, ticker, direction,
         target_size_pct, target_shares, entry_style, entry_limit_price, stop_loss,
         targets_json, trigger_text, invalidator_text, time_stop_days, regime_tag_json,
         expected_edge_pct, cash_hurdle_pass, approval_reason_json, blocked_reason, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            intent_id,
            run_ts,
            idea_id,
            strategy_id,
            "base",
            ticker,
            "long",
            _target_size_pct(idea),
            None,
            ENTRY_STYLE_MAP.get(entry_plan.get("order_type"), "limit_mid"),
            entry_plan.get("limit_price"),
            entry_plan.get("stop"),
            json.dumps([]),
            entry_plan.get("trigger"),
            entry_plan.get("invalidator"),
            5,
            json.dumps([checkpoint_name]),
            round(float(idea.get("forward_alpha_score") or 0) / 100.0, 4),
            cash_pass,
            json.dumps(
                {
                    "checkpoint": checkpoint_name,
                    "recommendation_class": idea.get("recommendation_class"),
                    "reason": idea.get("reason"),
                    "components": idea.get("components") or {},
                    "penalties": idea.get("penalties") or {},
                }
            ),
            _blocked_reason(idea),
            _intent_status(idea),
        ),
    )


def _insert_candidate_decision(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    run_ts: str,
    checkpoint_name: str,
    idea: dict[str, Any],
) -> None:
    ticker = str(idea["ticker"]).upper()
    score = float(idea.get("forward_alpha_score") or 0)
    rec = idea.get("recommendation_class")
    if rec == "buy_ready" and score >= 60:
        decision = "trade"
        reject_reason = None
        watch_until = None
    elif score >= 40:
        decision = "watch"
        reject_reason = None
        watch_until = run_ts
    else:
        decision = "reject"
        reject_reason = idea.get("dominant_risk") or "score below threshold"
        watch_until = None

    conn.execute(
        """
        INSERT OR REPLACE INTO candidate_decisions
        (candidate_id, ts, source, strategy_id, ticker, signal_summary, score_components_json,
         decision, reject_reason, watch_until, price_at_decision)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{run_id}:cand:{ticker}",
            run_ts,
            "checkpoint_runner",
            f"checkpoint_{checkpoint_name}",
            ticker,
            idea.get("reason"),
            json.dumps(
                {
                    "forward_alpha_score": score,
                    "replacement_delta_score": idea.get("replacement_delta_score"),
                    "components": idea.get("components") or {},
                    "penalties": idea.get("penalties") or {},
                }
            ),
            decision,
            reject_reason,
            watch_until,
            (idea.get("entry_plan") or {}).get("reference_price"),
        ),
    )


def run_checkpoint(
    checkpoint_name: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    create_intents: bool = True,
    max_replacements: int = 5,
    max_rotations: int = 3,
    scan_limit: int = 30,
) -> CheckpointResult:
    checkpoint_name = _normalize_name(checkpoint_name)
    run_ts = now_iso()
    safe_ts = run_ts.replace(":", "").replace("-", "").replace("+", "_").replace("T", "_").replace("Z", "")
    run_id = f"checkpoint:{checkpoint_name}:{safe_ts}"
    report = intraday_alpha.run(objective="beat_spy_this_week", max_total=scan_limit)
    top_replacements = list(report.get("ranked_replacements") or [])[:max_replacements]
    top_rotations = list(report.get("proposed_rotations") or [])[:max_rotations]
    warnings: list[str] = []
    created_intent_count = 0

    with _connect(db_path, write=True) as conn:
        for idea in top_replacements:
            if create_intents:
                _upsert_idea_and_intent(
                    conn,
                    run_id=run_id,
                    run_ts=run_ts,
                    checkpoint_name=checkpoint_name,
                    idea=idea,
                )
                created_intent_count += 1
            _insert_candidate_decision(
                conn,
                run_id=run_id,
                run_ts=run_ts,
                checkpoint_name=checkpoint_name,
                idea=idea,
            )

        status = "ok"
        if not top_replacements:
            warnings.append("no ranked replacements produced")
            status = "warning"
        elif not any(i.get("recommendation_class") == "buy_ready" for i in top_replacements):
            warnings.append("no buy_ready replacements at this checkpoint")
            status = "warning"

        conn.execute(
            """
            INSERT OR REPLACE INTO checkpoint_runs
            (run_id, checkpoint_name, run_ts, regime, portfolio_equity, buying_power, cash,
             ranked_holdings_json, ranked_replacements_json, proposed_rotations_json,
             created_intent_count, warning_count, warnings_json, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                checkpoint_name,
                run_ts,
                ((report.get("macro_regime") or {}).get("label")),
                ((report.get("portfolio_state") or {}).get("portfolio_equity")),
                ((report.get("portfolio_state") or {}).get("buying_power")),
                ((report.get("portfolio_state") or {}).get("cash")),
                json.dumps(report.get("ranked_holdings") or []),
                json.dumps(top_replacements),
                json.dumps(top_rotations),
                created_intent_count,
                len(warnings),
                json.dumps(warnings),
                status,
            ),
        )
        conn.commit()

    return CheckpointResult(
        run_id=run_id,
        checkpoint_name=checkpoint_name,
        run_ts=run_ts,
        status=status,
        created_intent_count=created_intent_count,
        warning_count=len(warnings),
        warnings=warnings,
        top_replacements=top_replacements,
        top_rotations=top_rotations,
    )


def result_to_text(result: CheckpointResult) -> str:
    lines = [
        f"DRUCK CHECKPOINT {result.checkpoint_name} — {result.run_ts}",
        f"status={result.status} intents={result.created_intent_count} warnings={result.warning_count}",
        "top replacements:",
    ]
    for item in result.top_replacements[:5]:
        lines.append(
            f"  {item['ticker']:6} {item['recommendation_class']:15} "
            f"alpha={float(item['forward_alpha_score'] or 0):5.1f}"
        )
    if result.top_rotations:
        lines.append("top rotations:")
        for rot in result.top_rotations[:3]:
            sell = (rot.get("sell") or {}).get("ticker")
            buy = (rot.get("buy") or {}).get("ticker")
            lines.append(f"  {sell} -> {buy}  delta={rot.get('rotation_delta_score')}")
    if result.warnings:
        lines.append("warnings:")
        for warning in result.warnings:
            lines.append(f"  - {warning}")
    return "\n".join(lines)
