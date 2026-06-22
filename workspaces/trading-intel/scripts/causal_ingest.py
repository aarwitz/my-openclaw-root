#!/usr/bin/env python3
"""causal_ingest.py — BROAD real-world entity/event ingestion into the causal graph (Phase 1, go-broad).

Pulls recent news for a set of broad real-world TOPICS (Fed, tariffs, oil, Taiwan, AI chips, nuclear, …)
from Event Registry, extracts TYPED concepts (person / organization / country / topic), and wires:
  - typed `entities` (person/organization/country/topic) — the real-world nodes,
  - `co_occurs` candidate edges among co-mentioned concepts (the relationship web),
  - `affects` candidate edges from a real-world driver concept -> a public-company TICKER (via an alias map),
so the why-engine can answer "why did <Fed / Taiwan / tariffs> matter" and "why <ticker>" gains real-world
drivers. All edges enter as CANDIDATE (observed, not yet held-out-validated) with an evidence trail.
Symbolic, no embeddings. Reuses causal_graph's entity/edge UPSERT helpers.

  python3 causal_ingest.py            # bounded ingest pass + report
"""

from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
import causal_graph as cg  # noqa: E402  (ent/edge/ensure_schema + FEAT)
from connectors import eventregistry as er  # noqa: E402

TOPICS = [
    "Federal Reserve", "interest rates", "inflation", "tariffs", "oil prices", "Taiwan",
    "China economy", "artificial intelligence chips", "semiconductors", "nuclear energy",
    "lithium supply", "rare earth minerals", "OpenAI", "data centers", "US election",
    "Treasury yields", "defense spending", "uranium", "supply chain", "geopolitical risk",
]
# company concept -> ticker (majors), so real-world drivers link into the trading graph
ALIAS = {
    "nvidia": "NVDA", "apple": "AAPL", "microsoft": "MSFT", "amazon": "AMZN", "alphabet": "GOOGL",
    "google": "GOOGL", "meta platforms": "META", "meta": "META", "tesla": "TSLA", "broadcom": "AVGO",
    "amd": "AMD", "advanced micro devices": "AMD", "micron": "MU", "micron technology": "MU",
    "intel": "INTC", "taiwan semiconductor": "TSM", "tsmc": "TSM", "oracle": "ORCL", "palantir": "PLTR",
    "mp materials": "MP", "lithium americas": "LAC", "constellation energy": "CEG", "nuscale": "SMR",
    "oklo": "OKLO", "cameco": "CCJ", "exxon": "XOM", "chevron": "CVX", "jpmorgan": "JPM",
}
TYPEMAP = {"person": "person", "org": "organization", "loc": "country", "wiki": "topic"}
MAX_CONCEPTS = 6
MIN_SCORE = 2


def ingest(topics=TOPICS, per=12, days=10):
    c = sqlite3.connect(cg.FEAT)
    cg.ensure_schema(c)
    n_art = n_edge = 0
    for topic in topics:
        try:
            arts = er.recent_news(topic, days=days, count=per)
        except Exception as e:
            print(f"  skip {topic}: {str(e)[:50]}", flush=True)
            continue
        for a in arts:
            n_art += 1
            cons = [(lbl, typ) for (lbl, typ, sc) in (a.get("concepts") or [])
                    if lbl and (sc or 0) >= MIN_SCORE][:MAX_CONCEPTS]
            nodes = []
            for lbl, typ in cons:
                low = lbl.lower()
                if low in ALIAS:
                    nodes.append((cg.ent(c, "ticker", ALIAS[low]), "ticker"))
                else:
                    nodes.append((cg.ent(c, TYPEMAP.get(typ, "topic"), lbl), "driver"))
            drivers = [n for n in nodes if n[1] == "driver"]
            tickers = [n for n in nodes if n[1] == "ticker"]
            ev = f"news {a.get('date','')}: {(a.get('title') or '')[:55]}"
            for i in range(len(drivers)):
                for j in range(i + 1, len(drivers)):
                    cg.edge(c, drivers[i][0], drivers[j][0], "co_occurs", evidence=ev,
                            status="candidate", when=a.get("date"))
                    n_edge += 1
                for tk, _ in [(t[0], t[1]) for t in tickers]:
                    cg.edge(c, drivers[i][0], tk, "affects", evidence=ev, status="candidate", when=a.get("date"))
                    n_edge += 1
        print(f"  {topic}: {len(arts)} articles", flush=True)
    c.commit()
    print(f"\ningested {n_art} articles -> {n_edge} candidate edges")
    for typ, n in c.execute("SELECT type,COUNT(*) FROM entities GROUP BY type ORDER BY 2 DESC"):
        print(f"  entities {typ:14} {n}")
    c.close()


if __name__ == "__main__":
    ingest()
