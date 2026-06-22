#!/usr/bin/env python3
"""Walk-forward episodic retrieval over the named episode library.

At hypothesis/decision time the desk asks: "what past episodes look like this?"
This returns the top-k most relevant episodes — but ONLY those that were already
knowable as of the decision date (`knowable_at < as_of`). That walk-forward gate
is what replaces the old anonymization: real names/dates are fine as long as we
never retrieve an episode that hadn't happened yet.

Relevance score (deterministic, stdlib only) blends:
  - FTS5 BM25 match on free-text query over title/theme/catalyst/lesson
  - ticker overlap (Jaccard)
  - exact mechanism_id match
  - exact theme match
  - a small confidence prior (high > medium > low)

Usage:
  python3 retrieve_episodes.py --query "hot jobs report rate cuts tech selloff"
  python3 retrieve_episodes.py --tickers NVDA,AVGO --theme macro_rates -k 5
  python3 retrieve_episodes.py --mechanism mech_jobs_duration_tech --as-of 2026-06-04
  python3 retrieve_episodes.py --query "..." --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/developer/scripts")
from _db import connect, now_iso  # noqa: E402

CONF_PRIOR = {"high": 0.15, "medium": 0.08, "low": 0.03}

# Weights for blending the relevance components into one score.
W_FTS = 1.0
W_TICKER = 0.6
W_MECHANISM = 0.5
W_THEME = 0.3


def _fts_query(text: str) -> str:
    """Build a safe FTS5 OR-query from free text (alnum tokens >= 3 chars)."""
    toks = [t for t in re.findall(r"[A-Za-z0-9]+", text) if len(t) >= 3]
    return " OR ".join(toks)


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def retrieve(conn, *, query=None, tickers=None, theme=None, mechanism=None,
             as_of=None, k=5, include_controls=True):
    as_of = as_of or now_iso()
    want_tickers = {t.strip().upper() for t in (tickers or []) if t.strip()}

    # Walk-forward gate + optional control filter.
    where = ["knowable_at < ?"]
    params: list = [as_of]
    if not include_controls:
        where.append("is_negative_control = 0")
    rows = conn.execute(
        f"SELECT rowid, * FROM episodes WHERE {' AND '.join(where)}",
        params,
    ).fetchall()

    # FTS BM25 scores (lower bm25 = better; convert to a positive contribution).
    fts_scores: dict[int, float] = {}
    if query:
        q = _fts_query(query)
        if q:
            try:
                for r in conn.execute(
                    "SELECT rowid, bm25(episodes_fts) AS score FROM episodes_fts "
                    "WHERE episodes_fts MATCH ? ORDER BY score",
                    (q,),
                ):
                    # bm25 is negative-ish; map to (0,1] via 1/(1+|score|)
                    fts_scores[r["rowid"]] = 1.0 / (1.0 + abs(r["score"]))
            except sqlite3.OperationalError:
                pass

    scored = []
    for r in rows:
        d = dict(r)
        ep_tickers = set(json.loads(d.get("tickers_json") or "[]"))
        score = 0.0
        reasons = []
        if d["rowid"] in fts_scores:
            s = W_FTS * fts_scores[d["rowid"]]
            score += s
            reasons.append(f"text+{s:.2f}")
        if want_tickers:
            j = _jaccard(want_tickers, ep_tickers)
            if j:
                score += W_TICKER * j
                reasons.append(f"tickers+{W_TICKER*j:.2f}")
        if mechanism and d.get("mechanism_id") == mechanism:
            score += W_MECHANISM
            reasons.append("mechanism")
        if theme and d.get("theme") == theme:
            score += W_THEME
            reasons.append("theme")
        score += CONF_PRIOR.get(d.get("confidence"), 0.0)
        if score <= 0:
            continue
        scored.append((score, reasons, d))

    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, reasons, d in scored[:k]:
        out.append({
            "id": d["id"],
            "title": d["title"],
            "tickers": json.loads(d.get("tickers_json") or "[]"),
            "theme": d.get("theme"),
            "mechanism_id": d.get("mechanism_id"),
            "direction": d.get("direction"),
            "knowable_at": d.get("knowable_at"),
            "outcome": d.get("outcome"),
            "is_negative_control": bool(d.get("is_negative_control")),
            "correct_action": d.get("correct_action"),
            "naive_trap": d.get("naive_trap"),
            "lesson": d.get("lesson_concise"),
            "confidence": d.get("confidence"),
            "relevance": round(score, 3),
            "match": reasons,
        })
    return {"as_of": as_of, "count": len(out), "episodes": out}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--query", default=None)
    p.add_argument("--tickers", default=None, help="comma-separated")
    p.add_argument("--theme", default=None)
    p.add_argument("--mechanism", default=None)
    p.add_argument("--as-of", default=None, help="ISO date; only episodes knowable before this are returned")
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--no-controls", action="store_true", help="exclude negative-control episodes")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if not any([args.query, args.tickers, args.theme, args.mechanism]):
        p.error("provide at least one of --query / --tickers / --theme / --mechanism")

    conn = connect()
    try:
        res = retrieve(
            conn,
            query=args.query,
            tickers=args.tickers.split(",") if args.tickers else None,
            theme=args.theme,
            mechanism=args.mechanism,
            as_of=args.as_of,
            k=args.k,
            include_controls=not args.no_controls,
        )
    finally:
        conn.close()

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"as_of={res['as_of']}  ({res['count']} episodes)\n")
        for e in res["episodes"]:
            flag = " [NEG-CONTROL]" if e["is_negative_control"] else ""
            print(f"● {e['title']}{flag}")
            print(f"   relevance={e['relevance']} match={','.join(e['match'])} "
                  f"dir={e['direction']} conf={e['confidence']} mech={e['mechanism_id']}")
            print(f"   DO: {e['correct_action']}")
            if e["naive_trap"]:
                print(f"   TRAP: {e['naive_trap']}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
