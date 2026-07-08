#!/usr/bin/env python3
"""Track ML-ranker evidence usage and eventual outcome quality.

Purpose:
- Keep ranker advisory (no trading control changes).
- Measure if cited ML evidence agrees/disagrees with final thesis direction.
- Measure whether cited cases later outperform (via resolved_state outcomes).

Usage:
  python3 track_ml_evidence.py
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

TI_DB = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
FEAT_DB = Path(os.path.expanduser("~/.openclaw/state/features.sqlite"))

CITE_PAT = re.compile(r"\b(ml[_ -]?rank|ml[_ -]?score|gbm|top[_ -]?decile|feature store)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_ticker(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        arr = json.loads(raw)
        if isinstance(arr, list) and arr:
            return str(arr[0]).upper()
    except Exception:
        pass
    return None


def _thesis_direction(text: str | None) -> str:
    t = (text or "").lower().strip()
    if re.search(r"\b(short|bearish|sell|fade)\b", t):
        return "short"
    return "long"


def main() -> int:
    ti = sqlite3.connect(TI_DB)
    ti.row_factory = sqlite3.Row

    feat = sqlite3.connect(FEAT_DB)
    feat.row_factory = sqlite3.Row
    as_of = feat.execute("SELECT MAX(as_of) FROM ml_scores").fetchone()[0]
    rank_by_ticker: dict[str, sqlite3.Row] = {}
    if as_of:
        for r in feat.execute("SELECT ticker, score, rank, n_universe, model FROM ml_scores WHERE as_of=?", (as_of,)):
            rank_by_ticker[str(r["ticker"]).upper()] = r

    ti.execute(
        "CREATE TABLE IF NOT EXISTS ml_evidence_tracking ("
        "hypothesis_id TEXT PRIMARY KEY, "
        "ticker TEXT, "
        "cited_ml INTEGER NOT NULL, "
        "ml_as_of TEXT, "
        "ml_rank INTEGER, "
        "ml_score REAL, "
        "ml_model TEXT, "
        "thesis_direction TEXT, "
        "ml_direction TEXT, "
        "agreement TEXT, "
        "resolved_state TEXT, "
        "resolved_outperformed INTEGER, "
        "updated_at TEXT NOT NULL)"
    )

    rows = ti.execute(
        "SELECT id, tickers, thesis_summary, rationale_concise, resolved_state "
        "FROM hypotheses"
    ).fetchall()

    upserts = 0
    cited = 0
    agreed = 0
    disagreed = 0
    cited_resolved = 0
    cited_outperformed = 0

    for h in rows:
        hid = h["id"]
        text = f"{h['thesis_summary'] or ''} {h['rationale_concise'] or ''}"
        cited_ml = 1 if CITE_PAT.search(text) else 0
        ticker = _first_ticker(h["tickers"])
        thesis_dir = _thesis_direction(h["thesis_summary"])

        ml = rank_by_ticker.get((ticker or "").upper()) if ticker else None
        ml_score = float(ml["score"]) if ml and ml["score"] is not None else None
        ml_rank = int(ml["rank"]) if ml and ml["rank"] is not None else None
        ml_model = (ml["model"] if ml else None)
        ml_dir = None
        if ml_score is not None:
            ml_dir = "long" if ml_score >= 0 else "short"

        agreement = "unknown"
        if cited_ml and ml_dir:
            agreement = "agree" if ml_dir == thesis_dir else "disagree"

        resolved_state = h["resolved_state"]
        resolved_outperformed = None
        if resolved_state in ("correct_right_reasons", "correct_wrong_reasons"):
            resolved_outperformed = 1
        elif resolved_state == "wrong":
            resolved_outperformed = 0

        ti.execute(
            "INSERT OR REPLACE INTO ml_evidence_tracking ("
            "hypothesis_id, ticker, cited_ml, ml_as_of, ml_rank, ml_score, ml_model, "
            "thesis_direction, ml_direction, agreement, resolved_state, resolved_outperformed, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                hid,
                ticker,
                cited_ml,
                as_of,
                ml_rank,
                ml_score,
                ml_model,
                thesis_dir,
                ml_dir,
                agreement,
                resolved_state,
                resolved_outperformed,
                _now(),
            ),
        )
        upserts += 1

        if cited_ml:
            cited += 1
            if agreement == "agree":
                agreed += 1
            elif agreement == "disagree":
                disagreed += 1
            if resolved_outperformed is not None:
                cited_resolved += 1
                if resolved_outperformed == 1:
                    cited_outperformed += 1

    ti.commit()
    feat.close()
    ti.close()

    hit_rate = (cited_outperformed / cited_resolved) if cited_resolved else None
    print(json.dumps({
        "as_of_ml": as_of,
        "hypotheses_tracked": upserts,
        "cited_ml": cited,
        "agreement": {
            "agree": agreed,
            "disagree": disagreed,
            "unknown": max(0, cited - agreed - disagreed),
        },
        "resolved_cited": cited_resolved,
        "resolved_outperformed": cited_outperformed,
        "resolved_outperformed_rate": None if hit_rate is None else round(hit_rate, 4),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
