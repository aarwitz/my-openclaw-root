#!/usr/bin/env python3
"""Seed the three TM-293 historical test themes from canonical evidence."""
from __future__ import annotations

import json
import math
import sqlite3

import theme_model as tm

EVENTS = {
    "saas_now": ("mev-74259e0105464f70b232", "NOW", "support"),
    "saas_adbe": ("mev-ddb00ebe9c5a4c369e19", "ADBE", "support"),
    "capex_nbis": ("mev-74259e0105464f70b232", "NBIS", "support"),
    "capex_vrt": ("mev-88e814c06d2e4344805b", "VRT", "support"),
    "aapl_day": ("mev-83e4904e20a6486c805c", "AAPL", "contradict"),
}


def event_evidence(conn: sqlite3.Connection, event_id: str, ticker: str,
                   outcome: str) -> dict:
    row = conn.execute(
        "SELECT event_date,headline,observed_moves_json FROM market_events WHERE id=?",
        (event_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"seed evidence event missing: {event_id}")
    moves = json.loads(row["observed_moves_json"] or "{}")
    if ticker not in moves:
        raise RuntimeError(f"{event_id} has no bar-confirmed {ticker} move")
    move = float(moves[ticker])
    evidence = {
        "source_type": "market_event", "source_id": event_id,
        "event_date": row["event_date"], "headline": row["headline"],
        "ticker": ticker, "move_pct": move, "outcome": outcome,
    }
    return {
        "source_type": "market_event", "source_id": event_id,
        "outcome": outcome, "ticker": ticker, "move_pct": move,
        "as_of": row["event_date"], "evidence": evidence,
        "notes": f"seeded from canonical market event; {ticker} {move:+.3f}%",
    }


def fixed_window_return(feature_conn: sqlite3.Connection, ticker: str,
                        as_of: str, sessions: int) -> float:
    rows = feature_conn.execute(
        "SELECT as_of,value FROM features WHERE ticker=? AND name='ret_1d' "
        "AND as_of<=? ORDER BY as_of DESC LIMIT ?",
        (ticker, as_of, sessions),
    ).fetchall()
    if len(rows) != sessions:
        raise RuntimeError(f"not enough {ticker} feature bars for {sessions}d seed")
    return (math.prod(1.0 + float(row[1]) / 100.0 for row in rows) - 1.0) * 100.0


def main() -> int:
    conn = tm.connect()
    features = sqlite3.connect(f"file:{tm.FEATURE_DB}?mode=ro", uri=True)
    try:
        definitions = [
            (
                "theme-saas-rerating-up",
                "Recurring-revenue software is rerating upward relative to crowded AI hardware.",
                ["MSFT", "ORCL", "CRM", "NOW", "ADBE", "SNOW", "DDOG"],
                ["NVDA", "AMD", "AVGO", "MU", "ARM", "VRT", "NBIS"],
                [EVENTS["saas_now"], EVENTS["saas_adbe"]],
                "2026-07-24T00:00:00Z",
            ),
            (
                "theme-ai-capex-derating",
                "The tape is penalizing AI capex, cash burn, and crowded hardware exposure despite durable AI demand.",
                ["MSFT", "ORCL", "CRM", "NOW", "ADBE"],
                ["NVDA", "AMD", "AVGO", "MU", "ARM", "VRT", "NBIS"],
                [EVENTS["capex_nbis"], EVENTS["capex_vrt"]],
                "2026-07-24T00:00:00Z",
            ),
        ]
        results = []
        for theme_id, statement, beneficiaries, victims, evidences, created_at in definitions:
            for index, (event_id, ticker, outcome) in enumerate(evidences):
                result = tm.file_theme(
                    conn, theme_id=theme_id, statement=statement,
                    beneficiaries=beneficiaries, victims=victims, status="active",
                    source="debrief", created_by="archivist", created_at=created_at,
                    observation=event_evidence(conn, event_id, ticker, outcome),
                )
                if index == len(evidences) - 1:
                    results.append(result)

        aapl_return = fixed_window_return(features, "AAPL", "2026-07-29", 21)
        bar_evidence = {
            "source_type": "bar_move", "source_id": "features:AAPL:2026-07-29:21d",
            "outcome": "support", "ticker": "AAPL", "move_pct": round(aapl_return, 6),
            "as_of": "2026-07-29",
            "evidence": {"ticker": "AAPL", "as_of": "2026-07-29", "sessions": 21,
                         "return_pct": round(aapl_return, 6), "source": "features.sqlite:ret_1d"},
            "notes": "point-in-time 21-session price return; observational historical seed",
        }
        result = tm.file_theme(
            conn, theme_id="theme-aapl-ai-abstinence-rewarded",
            statement="Apple's lower AI-capex posture was rewarded relative to capital-intensive AI peers.",
            beneficiaries=["AAPL"],
            victims=["NVDA", "AMD", "AVGO", "MU", "ARM", "VRT", "NBIS"],
            status="active", source="operator", created_by="human",
            created_at="2026-07-01T00:00:00Z", observation=bar_evidence,
        )
        event_id, ticker, outcome = EVENTS["aapl_day"]
        result = tm.file_theme(
            conn, theme_id="theme-aapl-ai-abstinence-rewarded",
            statement="Apple's lower AI-capex posture was rewarded relative to capital-intensive AI peers.",
            beneficiaries=["AAPL"],
            victims=["NVDA", "AMD", "AVGO", "MU", "ARM", "VRT", "NBIS"],
            status="active", source="operator", created_by="human",
            created_at="2026-07-01T00:00:00Z",
            observation=event_evidence(conn, event_id, ticker, outcome),
        )
        results.append(result)
        conn.commit()
        print(json.dumps({"seeded": results, "aapl_21d_asof_2026_07_29": round(aapl_return, 6)}, indent=2))
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        features.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
