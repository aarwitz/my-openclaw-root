#!/usr/bin/env python3
"""Deterministic harness for the risk-gate same-day opposition sweep.

Validates three cases without network or broker dependencies:
  1. PAYX-like entry with only positive company-filed evidence is BLOCKED.
  2. Exit/trim actions are NOT blocked by the opposition sweep.
  3. If the adverse article is already reflected in hypothesis_evidence, entry passes.

Usage:
  python3 test_risk_opposition_sweep.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/risk/scripts")
import gate_risk_intents as risk_gate  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    suffix = f" :: {detail}" if detail else ""
    print(("  PASS " if cond else "  FAIL ") + name + suffix)


def setup_db(include_negative_evidence: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE hypotheses (
          id TEXT PRIMARY KEY,
          thesis_summary TEXT,
          rationale_concise TEXT
        );
        CREATE TABLE hypothesis_evidence (
          id TEXT PRIMARY KEY,
          hypothesis_id TEXT,
          indicator TEXT,
          value TEXT,
          source TEXT,
          source_url TEXT,
          retrieved_at TEXT
        );
        CREATE TABLE critic_reviews (
          id TEXT PRIMARY KEY,
          target_type TEXT,
          target_id TEXT,
          reviewed_at TEXT,
          all_challenges_addressed INTEGER,
          challenges_json TEXT
        );
        CREATE TABLE positions (
          id TEXT PRIMARY KEY,
          ticker TEXT,
          state TEXT,
          current_value REAL,
          qty REAL,
          cost_basis REAL
        );
        CREATE TABLE trade_intents (
          id TEXT PRIMARY KEY,
          hypothesis_id TEXT,
          ticker TEXT,
          size REAL,
          entry_price_target REAL,
          state TEXT,
          action TEXT
        );
        """
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO hypotheses (id, thesis_summary, rationale_concise) VALUES (?, ?, ?)",
        (
            "HYPO-PAYX",
            "Long PAYX: Paychex reported Q4/FY26 revenue with Paycor integration and WISE AI launch.",
            "Q4/FY26 revenue and EPS plus integration and AI launch support a rerate.",
        ),
    )
    conn.execute(
        "INSERT INTO hypothesis_evidence (id, hypothesis_id, indicator, value, source, source_url, retrieved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "EVID-POS",
            "HYPO-PAYX",
            "Q4/FY26 earnings",
            "Q4 revenue $1.6055B (+12%); FY revenue $6.512B (+17%)",
            "Paychex Q4/FY26 results (Exhibit 99.1)",
            "https://investor.paychex.com/sec-filings/all-sec-filings/content/0001193125-26-280314/payx-ex99_1.htm",
            now,
        ),
    )
    if include_negative_evidence:
        for row in (
            (
                "EVID-NEG-1",
                "UBS downgrade pressure",
                "UBS cut price target to $98 on revenue growth concerns.",
                "Investing.com analyst ratings",
                "https://www.investing.com/news/analyst-ratings/ubs-cuts-paychex-stock-price-target-to-98-on-revenue-growth-concerns-93CH-4760769",
            ),
            (
                "EVID-NEG-2",
                "JPM underweight pressure",
                "JPMorgan maintained underweight and adjusted price target to $105.",
                "Market Screener analyst ratings",
                "https://www.marketscreener.com/news/jpmorgan-adjusts-price-target-on-paychex-to-105-from-100-maintains-underweight-rating-ce7f5fd8dd8cf325",
            ),
        ):
            conn.execute(
                "INSERT INTO hypothesis_evidence (id, hypothesis_id, indicator, value, source, source_url, retrieved_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row[0], "HYPO-PAYX", row[1], row[2], row[3], row[4], now),
            )
    conn.execute(
        "INSERT INTO critic_reviews (id, target_type, target_id, reviewed_at, all_challenges_addressed, challenges_json) "
        "VALUES (?, 'hypothesis', ?, ?, 1, ?)",
        (
            "CRIT-PAYX",
            "HYPO-PAYX",
            now,
            '[{"challenge":"baseline_promotion","response":"deterministic promotion","resolved":true}]',
        ),
    )
    return conn


def make_intent(action: str = "open") -> dict:
    return {
        "id": f"TI-{action.upper()}",
        "hypothesis_id": "HYPO-PAYX",
        "ticker": "PAYX",
        "size": 12,
        "entry_price_target": 96.955,
        "state": "risk_review",
        "action": action,
    }


def main() -> int:
    same_day = datetime.now(timezone.utc).date().isoformat()
    adverse_news = [
        {
            "date": same_day,
            "title": "UBS cuts Paychex stock price target to $98 on revenue growth concerns By Investing.com",
            "source": "Investing.com",
            "url": "https://www.investing.com/news/analyst-ratings/ubs-cuts-paychex-stock-price-target-to-98-on-revenue-growth-concerns-93CH-4760769",
            "sentiment": 0.34,
        },
        {
            "date": same_day,
            "title": "JPMorgan Adjusts Price Target on Paychex to $105 From $100, Maintains Underweight Rating",
            "source": "Market Screener",
            "url": "https://www.marketscreener.com/news/jpmorgan-adjusts-price-target-on-paychex-to-105-from-100-maintains-underweight-rating-ce7f5fd8dd8cf325",
            "sentiment": 0.07,
        },
    ]

    orig_recent_news = risk_gate.eventregistry.recent_news
    risk_gate.eventregistry.recent_news = lambda *args, **kwargs: adverse_news
    try:
        conn = setup_db(include_negative_evidence=False)
        decision = risk_gate.gate(conn, make_intent("open"), equity=100000.0, day_pl=0.0, regime="neutral")
        check(
            "PAYX-like entry blocks on unresolved same-day adverse flow",
            decision["verdict"] == "blocked" and "same_day_opposition" in decision["breaches"],
            decision["reason"],
        )

        decision = risk_gate.gate(conn, make_intent("trim"), equity=100000.0, day_pl=0.0, regime="neutral")
        check(
            "trim bypasses opposition sweep",
            decision["verdict"] == "approved" and decision["approved_qty"] == 12,
            decision["reason"],
        )

        conn2 = setup_db(include_negative_evidence=True)
        decision = risk_gate.gate(conn2, make_intent("open"), equity=100000.0, day_pl=0.0, regime="neutral")
        check(
            "entry passes when adverse article is already reflected in evidence",
            decision["verdict"] != "blocked" or "same_day_opposition" not in decision["breaches"],
            decision["reason"],
        )
    finally:
        risk_gate.eventregistry.recent_news = orig_recent_news

    print(
        f"\n{'GREEN' if not FAIL else 'RED'}: {len(PASS)} passed, {len(FAIL)} failed"
    )
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
