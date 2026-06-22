#!/usr/bin/env python3
"""Knowledge graph for the world model — SYMBOLIC, no embeddings.

Makes the relationships our numeric tables only hold implicitly explicit + traversable (multi-hop) with
plain SQL joins. Nodes: ticker | sector | mechanism | regime. Typed, weighted, evidence-bearing edges:
  ticker  -in_sector->        sector            (universe)
  ticker  -correlated_with->  ticker            (126d return corr >= 0.55 — the contagion/co-move backbone)
  mech    -clusters_with->    mech              (mechanism_clusters, same cluster — redundancy structure)
  mech    -conditioned_by->   regime            (mechanism_regime: materially better in that regime)
Everything is exact rows a human can read and a script can recompute. Retrieval = SQL traversal, not
nearest-neighbor. (Phase 2: news co-mention / catalyst edges — needs the raw multi-ticker news tags.)

  python3 knowledge_graph.py build              # (re)build kg_nodes + kg_edges + print a report
  python3 knowledge_graph.py neighbors NVDA     # traverse: a ticker's correlated neighbourhood + sector
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
import signal_scan as ss     # noqa: E402  (reuse _returns/_corr)

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")
CORR_EDGE = 0.45             # store an edge if |126d return corr| >= this (moderate+; feeds signal_scan novelty)
CORR_CLUSTER = 0.60          # union-find components at this threshold (the clean "real baskets" in the report)
TOP_N = 250                  # correlation graph over the N most liquid names (O(N^2))
PMI_MIN = 1.5                # co-mention edge kept only if names co-occur ~4.5x+ more than popularity predicts
THEME_MAX = 40               # a keyword tagging >40 names is generic vocab, not a theme — drop it


def build(top_n=TOP_N):
    c = sqlite3.connect(FEAT)
    c.execute("DROP TABLE IF EXISTS kg_nodes")
    c.execute("DROP TABLE IF EXISTS kg_edges")
    c.execute("CREATE TABLE kg_nodes(id TEXT PRIMARY KEY, type TEXT, label TEXT, attrs_json TEXT)")
    c.execute("CREATE TABLE kg_edges(src TEXT, dst TEXT, rel TEXT, weight REAL, evidence TEXT)")
    nodes, edges = {}, []

    def node(nid, typ, label, attrs=None):
        nodes[nid] = (nid, typ, label, json.dumps(attrs or {}))

    uni = list(c.execute("SELECT symbol, sector, market_cap FROM universe WHERE status='active' "
                         "AND market_cap IS NOT NULL ORDER BY market_cap DESC"))
    for sym, sec, mc in uni:
        node(f"ticker:{sym}", "ticker", sym, {"sector": sec, "market_cap": mc})
        if sec:
            node(f"sector:{sec}", "sector", sec)
            edges.append((f"ticker:{sym}", f"sector:{sec}", "in_sector", 1.0, "universe"))

    # correlation backbone over the top-N liquid names
    top = [s for s, _, _ in uni[:top_n]]
    rets = {t: ss._returns(t, 126) for t in top}
    for i in range(len(top)):
        ri = rets.get(top[i])
        if not ri:
            continue
        for j in range(i + 1, len(top)):
            rj = rets.get(top[j])
            if not rj:
                continue
            cv = ss._corr(ri, rj)
            if cv >= CORR_EDGE:
                edges.append((f"ticker:{top[i]}", f"ticker:{top[j]}", "correlated_with", round(cv, 3), "126d ret"))

    # mechanism nodes + redundancy-cluster edges
    for mid, hz, cid, sz in c.execute("SELECT mechanism_id,horizon,cluster_id,cluster_size FROM mechanism_clusters"):
        node(f"mech:{mid}__{hz}", "mechanism", f"{mid}[{hz}]", {"cluster": cid, "size": sz})
    cl = {}
    for mid, hz, cid, sz in c.execute("SELECT mechanism_id,horizon,cluster_id,cluster_size "
                                      "FROM mechanism_clusters WHERE cluster_size>1"):
        cl.setdefault(cid, []).append(f"mech:{mid}__{hz}")
    for cid, members in cl.items():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                edges.append((members[i], members[j], "clusters_with", 1.0, f"cluster {cid}"))

    # regime nodes + conditioned_by edges (mechanism materially better in a regime)
    for rg in ("vix_hi", "vix_lo", "rate_up", "rate_dn", "rate_flat"):
        node(f"regime:{rg}", "regime", rg)
    ra = {}
    for mid, hz, rg, a in c.execute("SELECT mechanism_id,horizon,regime,alpha_pct FROM mechanism_regime"):
        ra.setdefault((mid, hz), {})[rg] = a
    for (mid, hz), rs in ra.items():
        base = rs.get("ALL")
        if base is None:
            continue
        for rg, a in rs.items():
            if rg != "ALL" and (a - base) > 1.0:
                edges.append((f"mech:{mid}__{hz}", f"regime:{rg}", "conditioned_by", round(a - base, 2),
                              f"alpha {a} vs ALL {base}"))

    c.executemany("INSERT OR REPLACE INTO kg_nodes VALUES(?,?,?,?)", list(nodes.values()))
    c.executemany("INSERT INTO kg_edges VALUES(?,?,?,?,?)", edges)
    c.commit()
    _report(c, top, rets)
    c.close()


def _components(top, rets):
    """Union-find connected components of the correlation graph at CORR_CLUSTER — the real baskets."""
    parent = {t: t for t in top}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i in range(len(top)):
        ri = rets.get(top[i])
        if not ri:
            continue
        for j in range(i + 1, len(top)):
            rj = rets.get(top[j])
            if rj and ss._corr(ri, rj) >= CORR_CLUSTER:
                parent[find(top[i])] = find(top[j])
    comp = {}
    for t in top:
        comp.setdefault(find(t), []).append(t)
    return sorted((v for v in comp.values() if len(v) > 1), key=lambda v: -len(v))


def _report(c, top, rets):
    print("=== KNOWLEDGE GRAPH built ===")
    for typ, n in c.execute("SELECT type, COUNT(*) FROM kg_nodes GROUP BY type ORDER BY 2 DESC"):
        print(f"  nodes {typ:10} {n}")
    for rel, n in c.execute("SELECT rel, COUNT(*) FROM kg_edges GROUP BY rel ORDER BY 2 DESC"):
        print(f"  edges {rel:16} {n}")
    print(f"\n  CORRELATION BASKETS (connected components at corr>={CORR_CLUSTER} — each ≈ one bet):")
    for comp in _components(top, rets)[:8]:
        print("   ", ", ".join(sorted(comp)[:12]) + (" …" if len(comp) > 12 else ""))
    print("\n  most-connected names (highest correlation degree = most systemic / market-beta):")
    deg = c.execute("SELECT label, COUNT(*) d FROM kg_edges e JOIN kg_nodes n ON e.src=n.id OR e.dst=n.id "
                    "WHERE e.rel='correlated_with' AND n.type='ticker' GROUP BY n.id ORDER BY d DESC LIMIT 8").fetchall()
    for lab, d in deg:
        print(f"    {lab:8} degree {d}")


def neighbors(ticker):
    c = sqlite3.connect(FEAT)
    tid = f"ticker:{ticker.upper()}"
    sec = c.execute("SELECT dst FROM kg_edges WHERE src=? AND rel='in_sector'", (tid,)).fetchone()
    print(f"{ticker.upper()} — sector: {sec[0].split(':')[1] if sec else '?'}")
    rows = c.execute("SELECT CASE WHEN src=? THEN dst ELSE src END nb, weight FROM kg_edges "
                     "WHERE rel='correlated_with' AND (src=? OR dst=?) ORDER BY weight DESC LIMIT 15",
                     (tid, tid, tid)).fetchall()
    print(f"  correlated neighbourhood (co-moves with — quantitative contagion):")
    for nb, w in rows:
        print(f"    {nb.split(':')[1]:8} corr {w}")
    com = c.execute("SELECT CASE WHEN src=? THEN dst ELSE src END nb, weight FROM kg_edges "
                    "WHERE rel='co_mentioned_with' AND (src=? OR dst=?) ORDER BY weight DESC LIMIT 12",
                    (tid, tid, tid)).fetchall()
    if com:
        print("  co-mentioned in news with (catalyst/narrative links price-correlation can miss):")
        for nb, w in com:
            print(f"    {nb.split(':')[1]:8} {int(w)} articles")
    th = c.execute("SELECT src, weight FROM kg_edges WHERE dst=? AND rel='catalyst_for' "
                   "ORDER BY weight DESC LIMIT 8", (tid,)).fetchall()
    if th:
        print("  themes:", ", ".join(s.split(':')[1] for s, _ in th))
    c.close()


def build_news_edges(top_n=250, gte="2024-06-01", min_co=3, min_theme=6):
    """Catalyst/contagion layer from the Massive news archive: co_mentioned_with (ticker↔ticker) +
    catalyst_for (theme→ticker). Articles tagging 2-6 universe tickers = a genuine relationship; bigger
    tag sets are listicles and are ignored for co-mention. Appends to the existing kg (idempotent)."""
    from connectors import massive
    c = sqlite3.connect(FEAT)
    syms = [r[0] for r in c.execute("SELECT symbol FROM universe WHERE status='active' AND "
                                    "market_cap IS NOT NULL ORDER BY market_cap DESC LIMIT ?", (top_n,))]
    sym_set = set(syms)
    articles = {}                                       # id -> (tuple universe-tickers, keywords)
    for i, s in enumerate(syms):
        try:
            arts = massive.news_articles(s, gte=gte)
        except Exception:
            continue
        for a in arts:
            aid = a.get("id")
            if not aid or aid in articles:
                continue
            tk = tuple(sorted(t for t in a.get("tickers", []) if t in sym_set))
            articles[aid] = (tk, a.get("keywords", []))
        if (i + 1) % 50 == 0:
            print(f"  news {i+1}/{len(syms)} ({len(articles)} unique articles)", flush=True)

    co, kw = {}, {}
    for tk, kws in articles.values():
        if 2 <= len(tk) <= 6:
            for x in range(len(tk)):
                for y in range(x + 1, len(tk)):
                    co[(tk[x], tk[y])] = co.get((tk[x], tk[y]), 0) + 1
        if 1 <= len(tk) <= 8:
            for k in set(kws):
                d = kw.setdefault(k, {})
                for t in tk:
                    d[t] = d.get(t, 0) + 1

    # document frequency per ticker (popularity) — to normalize co-mention by PMI/lift
    N = max(1, len(articles))
    df = {}
    for tk, _ in articles.values():
        for t in tk:
            df[t] = df.get(t, 0) + 1

    c.execute("DELETE FROM kg_edges WHERE rel IN ('co_mentioned_with','catalyst_for')")
    c.execute("DELETE FROM kg_nodes WHERE type='theme'")
    # co-mention weighted by PMI (lift over popularity): kills the mega-cap "in every article" noise,
    # surfaces names that co-occur FAR more than their individual frequencies predict (real linkage).
    pmi_pairs = []
    for (a, b), n in co.items():
        if n < min_co:
            continue
        pmi = math.log((n * N) / (df.get(a, 1) * df.get(b, 1)))
        if pmi >= PMI_MIN:
            pmi_pairs.append(((a, b), n, round(pmi, 2)))
    pmi_pairs.sort(key=lambda x: -x[2])
    c.executemany("INSERT INTO kg_edges VALUES(?,?,?,?,?)",
                  [(f"ticker:{a}", f"ticker:{b}", "co_mentioned_with", pmi, f"{n} articles")
                   for (a, b), n, pmi in pmi_pairs])
    # themes: keep SPECIFIC keywords only (focused name set, not generic vocab tagging hundreds)
    specific = [(k, td) for k, td in kw.items()
                if 3 <= len(td) <= THEME_MAX and sum(td.values()) >= min_theme]
    tn, te = {}, []
    for k, td in specific:
        tn[f"theme:{k}"] = (f"theme:{k}", "theme", k, "{}")
        te += [(f"theme:{k}", f"ticker:{t}", "catalyst_for", float(n), "co-tagged")
               for t, n in td.items() if n >= 2]
    c.executemany("INSERT OR REPLACE INTO kg_nodes VALUES(?,?,?,?)", list(tn.values()))
    c.executemany("INSERT INTO kg_edges VALUES(?,?,?,?,?)", te)
    c.commit()

    print(f"\n=== NEWS CO-MENTION + THEME layer (PMI-normalized) ===")
    print(f"  {len(articles)} unique articles | co_mentioned_with(PMI) edges: {len(pmi_pairs)} | "
          f"specific themes: {len(tn)} | catalyst_for edges: {len(te)}")
    print("  strongest SPECIFIC co-mention links (high PMI = co-occur far more than popularity predicts):")
    for (a, b), n, pmi in pmi_pairs[:18]:
        print(f"    {a:6} ~ {b:6}  PMI {pmi:.2f}  ({n} articles)")
    print("  most SPECIFIC themes (keyword → its focused name set):")
    for k, td in sorted(specific, key=lambda x: len(x[1]))[:14]:
        print(f"    {k[:26]:26} {len(td):2} names: {', '.join(sorted(td)[:8])}")
    c.close()


if __name__ == "__main__":
    # --top-n N (or --top-n=N) threads into BOTH build and news; default keeps TOP_N (back-compat).
    args = sys.argv[1:]
    top_n = TOP_N
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--top-n":
            top_n = int(args[i + 1])
            del args[i:i + 2]
            continue
        if a.startswith("--top-n="):
            top_n = int(a.split("=", 1)[1])
            del args[i]
            continue
        i += 1
    if len(args) > 1 and args[0] == "neighbors":
        neighbors(args[1])
    elif args and args[0] == "news":
        build_news_edges(top_n=top_n)
    else:
        build(top_n=top_n)
