#!/usr/bin/env python3
"""Mechanism redundancy / orthogonalization analysis for the AutoTrade world model.

PROBLEM: the calibrated mechanism set (table `calibrated_mechanisms`) has ~47 (id x horizon)
cells, but many are near-duplicates that all express the SAME economic theme through slightly
different triggers — e.g. `gen_dist_sma50_hi_long`, `gen_dist_sma200_hi_long`, `gen_mom_12_1_hi_long`,
`momentum_12_1` are all "momentum/trend"; `deep_drawdown`, `gen_drawdown_252_lo_long`,
`oversold_uptrend`, `gen_rsi14_lo_long` are all "mean-reversion/oversold". The world model
over-weights a theme by counting it many ways. This is an analysis-only tool: it quantifies the
redundancy empirically (not by name-matching) and proposes per-mechanism down-weights.

METHOD (pure stdlib + math):
  1. Load `calibrated_mechanisms`, parse `conds_json` ([feature, op, threshold] lists).
  2. Build each mechanism's FIRING VECTOR over a panel of ~150 liquid tickers x a weekly date grid:
     a boolean per (ticker, date) = does `holds(td, conds, d)` fire. (Firing depends only on conds,
     so it is computed once per unique mechanism id and shared across that id's horizons.)
  3. Pairwise similarity = Jaccard overlap of firing (ticker,date) sets (|A&B| / |A|B|).
     Direction-aware: clusters are formed WITHIN a (horizon, direction) group, so opposite-direction
     mechanisms (which are anti-correlated, not redundant) are never merged.
  4. Greedy union-find clustering: any two mechanisms with Jaccard > 0.5 join the same cluster.
  5. redundancy_weight = 1 / cluster_size  (a 10-mechanism momentum cluster each gets ~0.1, so the
     theme is counted ~once, not 10x). "Effective # of independent mechanisms" = sum of weights
     (= the number of distinct clusters).
  6. Write table `mechanism_clusters` (DROP+CREATE) — the only table this script creates/modifies.
  7. Print a clear report: clusters (named by dominant theme), sizes, effective-independent count,
     and the top mechanisms by net_alpha within each cluster.

Reuses load_ticker / fval / holds from mechanism_backtest.py. Reads features.sqlite +
locally-cached prices/macro (no live gateway calls). Run:  python3 mechanism_correlation.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
import mechanism_backtest as mb  # noqa: E402  (reuse load_ticker / fval / holds, don't reimplement)

FEAT_DB = mb.FEAT_DB
N_TICKERS = 150
GRID_START = date(2021, 1, 1)
GRID_END = date(2026, 6, 18)
GRID_STEP_DAYS = 7          # weekly firing-vector cadence
JACCARD_THRESHOLD = 0.5     # >0.5 => same cluster (per spec)

# Feature -> economic theme, used only to LABEL clusters in the report (clustering itself is
# empirical via Jaccard; this is cosmetic). Direction matters for a couple of features.
THEME = {
    "mom_12_1": "momentum/trend",
    "dist_52w_high": "momentum/trend",
    "dist_sma50": "trend",            # hi=trend, lo=pullback (resolved by op below)
    "dist_sma200": "trend",
    "drawdown_252": "mean-reversion/oversold",
    "rsi14": "mean-reversion/oversold",
    "vix_level": "volatility/stress",
    "vol_20d_annual": "volatility/stress",
    "pe_ttm": "value",
    "net_margin_ttm": "quality",
    "revenue_growth_yoy": "growth",
    "eps_surprise_pct": "earnings-event",
    "news_sent_7d": "sentiment",
    "news_sent_30d": "sentiment",
    "news_vol_z": "sentiment",
    "rating_net_90d": "analyst",
    "insider_net_180d": "insider",
    "sector_rel_63d": "sector",
    "rate_10y_chg_63d": "macro/rates",
    "real_yield_chg_63d": "macro/rates",
    "credit_spread_chg_63d": "macro/credit",
    "yield_curve_10y2y": "macro/rates",
}


def feature_theme(feat, op):
    if feat in ("dist_sma50", "dist_sma200"):
        return "trend/momentum" if op == ">" else "mean-reversion/oversold"
    return THEME.get(feat, feat)


def cluster_theme(member_conds):
    """Most common feature-theme across all conds of all members in the cluster."""
    c = Counter()
    for conds in member_conds:
        for feat, op, _thr in conds:
            c[feature_theme(feat, op)] += 1
    if not c:
        return "?"
    top = c.most_common()
    label = top[0][0]
    if len(top) > 1 and top[1][1] == top[0][1]:
        label = "+".join(t for t, _ in top[:2])
    return label


def weekly_grid():
    out, d = [], GRID_START
    while d <= GRID_END:
        out.append(d.isoformat())
        d += timedelta(days=GRID_STEP_DAYS)
    return out


# ---- union-find -------------------------------------------------------------------
def uf_find(parent, x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def uf_union(parent, a, b):
    ra, rb = uf_find(parent, a), uf_find(parent, b)
    if ra != rb:
        parent[rb] = ra


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / (len(a) + len(b) - inter)


def main():
    t_start = time.time()
    conn = sqlite3.connect(FEAT_DB, timeout=120.0)

    # ---- 1. load calibrated mechanisms -------------------------------------------
    rows = conn.execute(
        "SELECT id, horizon, direction, conds_json, net_alpha_pct, posterior_mean "
        "FROM calibrated_mechanisms"
    ).fetchall()
    cells = []  # one per (id, horizon) row
    id_conds = {}      # id -> conds list
    id_direction = {}  # id -> direction
    for mid, horizon, direction, conds_json, net_alpha, post in rows:
        conds = json.loads(conds_json)
        cells.append({"id": mid, "horizon": horizon, "direction": direction,
                      "conds": conds, "net_alpha": net_alpha, "post": post})
        id_conds[mid] = conds
        id_direction[mid] = direction
    uniq_ids = sorted(id_conds)
    print(f"loaded {len(cells)} calibrated (mechanism x horizon) cells; "
          f"{len(uniq_ids)} unique mechanism ids", flush=True)

    # ---- 2. universe: ~150 most liquid (largest-cap) active names ----------------
    universe = [r[0] for r in conn.execute(
        "SELECT symbol FROM universe WHERE status='active' AND market_cap IS NOT NULL "
        "ORDER BY market_cap DESC LIMIT ?", (N_TICKERS,)).fetchall()]
    grid = weekly_grid()
    print(f"panel: {len(universe)} tickers x {len(grid)} weekly dates "
          f"({GRID_START} .. {GRID_END}); Jaccard>{JACCARD_THRESHOLD} => same cluster", flush=True)

    # ---- 2b. build firing sets (stream one ticker at a time) ---------------------
    # firing[id] = set of integer cell-ids (ticker_idx * len(grid) + date_idx)
    firing = {mid: set() for mid in uniq_ids}
    ng = len(grid)
    nseen = 0
    for ti, t in enumerate(universe):
        try:
            td = mb.load_ticker(conn, t)
        except Exception:
            continue
        dts = td["dates"]
        if not dts:
            continue
        nseen += 1
        lo, hi = dts[0], dts[-1]          # only evaluate while the name has live data
        base = ti * ng
        for di, g in enumerate(grid):
            if g < lo or g > hi:
                continue
            cell = base + di
            for mid in uniq_ids:
                if mb.holds(td, id_conds[mid], g):
                    firing[mid].add(cell)
        td = None                          # free per stream
        if nseen % 25 == 0:
            print(f"  ... {nseen} tickers evaluated ({time.time()-t_start:.0f}s)", flush=True)
    print(f"firing vectors built over {nseen} tickers with data ({time.time()-t_start:.0f}s)", flush=True)

    # ---- 3/4. cluster WITHIN each (horizon, direction) group ---------------------
    groups = defaultdict(list)   # (horizon, direction) -> list of cell indices
    for ci, c in enumerate(cells):
        groups[(c["horizon"], c["direction"])].append(ci)

    def cluster_at(threshold):
        """Greedy union-find clustering (Jaccard > threshold) within each (horizon,direction)
        group. Returns group_clusters with globally-unique, deterministic cluster ids."""
        gc, next_cluster_id = {}, 0
        for key in sorted(groups):
            members = groups[key]
            parent = {ci: ci for ci in members}
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = members[i], members[j]
                    if jaccard(firing[cells[a]["id"]], firing[cells[b]["id"]]) > threshold:
                        uf_union(parent, a, b)
            by_root = defaultdict(list)
            for ci in members:
                by_root[uf_find(parent, ci)].append(ci)
            clist = []
            for root in sorted(by_root, key=lambda r: -len(by_root[r])):  # stable: big clusters first
                clist.append({"cid": next_cluster_id, "members": by_root[root],
                              "size": len(by_root[root])})
                next_cluster_id += 1
            gc[key] = clist
        return gc

    group_clusters = cluster_at(JACCARD_THRESHOLD)

    # ---- 5. redundancy weights + effective independent count ---------------------
    # assignment per cell -> (cluster_id, cluster_size, redundancy_weight)
    cell_cluster = {}
    for key, clist in group_clusters.items():
        for cl in clist:
            w = 1.0 / cl["size"]
            for ci in cl["members"]:
                cell_cluster[ci] = (cl["cid"], cl["size"], w)
    effective_total = sum(w for (_cid, _sz, w) in cell_cluster.values())

    # ---- 6. persist mechanism_clusters ------------------------------------------
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("DROP TABLE IF EXISTS mechanism_clusters")
    conn.execute("""CREATE TABLE mechanism_clusters(
        mechanism_id TEXT, horizon TEXT, cluster_id INT, cluster_size INT,
        redundancy_weight REAL, created_at TEXT)""")
    for ci, c in enumerate(cells):
        cid, sz, w = cell_cluster[ci]
        conn.execute("INSERT INTO mechanism_clusters VALUES(?,?,?,?,?,?)",
                     (c["id"], c["horizon"], cid, sz, round(w, 4), now))
    conn.commit()

    # ---- 7. report ---------------------------------------------------------------
    _report(cells, group_clusters, cell_cluster, firing, effective_total, nseen, len(grid))
    _sensitivity(cells, groups, firing, cluster_at)
    _top_pairs(cells, groups, firing)
    conn.close()
    print(f"\n[mechanism_clusters written to {FEAT_DB} — {len(cells)} rows]")
    print(f"[done in {time.time()-t_start:.0f}s]")


def _report(cells, group_clusters, cell_cluster, firing, effective_total, nseen, ngrid):
    print("\n" + "=" * 78)
    print("MECHANISM REDUNDANCY / ORTHOGONALIZATION REPORT")
    print("=" * 78)
    total_cells = len(cells)
    total_clusters = sum(len(cl) for cl in group_clusters.values())
    print(f"calibrated cells (mechanism x horizon) : {total_cells}")
    print(f"distinct firing clusters               : {total_clusters}")
    print(f"effective # independent mechanisms     : {effective_total:.2f}   "
          f"(sum of 1/cluster_size; = # clusters)")
    redundancy = total_cells / effective_total if effective_total else float("nan")
    print(f"redundancy multiple                    : {redundancy:.2f}x   "
          f"(cells per independent idea — the world model's theme over-count factor)")

    for key in sorted(group_clusters):
        horizon, direction = key
        clist = sorted(group_clusters[key], key=lambda c: -c["size"])
        grp_eff = sum(1.0 / c["size"] for c in clist)
        n_in_grp = sum(c["size"] for c in clist)
        print("\n" + "-" * 78)
        print(f"HORIZON {horizon}  /  {direction.upper()}   "
              f"({n_in_grp} cells -> {len(clist)} clusters, effective={grp_eff:.2f})")
        print("-" * 78)
        for cl in clist:
            mem = sorted(cl["members"], key=lambda ci: -(cells[ci]["net_alpha"] or 0))
            theme = cluster_theme([cells[ci]["conds"] for ci in mem])
            w = 1.0 / cl["size"]
            tag = "  <-- REDUNDANT" if cl["size"] >= 3 else ("  <- pair" if cl["size"] == 2 else "")
            print(f"\n  cluster #{cl['cid']:<3} size={cl['size']}  weight={w:.3f}  "
                  f"theme=[{theme}]{tag}")
            for rank, ci in enumerate(mem):
                c = cells[ci]
                fires = len(firing[c["id"]])
                lead = "  *top*" if rank == 0 else "       "
                print(f"   {lead} {c['id']:<40} net_alpha={c['net_alpha']:>6}%  "
                      f"post={c['post']:<6}  fires={fires:>5}")

    # most-over-counted themes (largest clusters across all groups)
    print("\n" + "-" * 78)
    print("BIGGEST REDUNDANCY CLUSTERS (most over-counted themes):")
    print("-" * 78)
    allcl = []
    for key, clist in group_clusters.items():
        for cl in clist:
            if cl["size"] >= 2:
                theme = cluster_theme([cells[ci]["conds"] for ci in cl["members"]])
                allcl.append((cl["size"], key, theme, cl))
    for size, key, theme, cl in sorted(allcl, key=lambda x: -x[0]):
        ids = ", ".join(cells[ci]["id"] for ci in
                        sorted(cl["members"], key=lambda ci: -(cells[ci]["net_alpha"] or 0)))
        print(f"  x{size}  {key[0]:<11} {key[1]:<5} [{theme}]  -> each weighted {1.0/size:.2f}")
        print(f"        {ids}")
    if not allcl:
        print("  (no multi-member clusters — mechanisms are already ~orthogonal)")


def _sensitivity(cells, groups, firing, cluster_at):
    """How redundancy collapses as the merge threshold loosens. The persisted table uses 0.5
    (spec), but at 0.5 only same-feature aliases merge; lower thresholds expose the thematic
    over-counting (e.g. all the trend/momentum triggers, all the oversold triggers)."""
    print("\n" + "-" * 78)
    print("THRESHOLD SENSITIVITY  (persisted table uses Jaccard>0.50)")
    print("-" * 78)
    total_cells = len(cells)
    # effective-independent = sum of per-mechanism redundancy_weights (1/cluster_size), which
    # equals the number of clusters; redundancy multiple = cells / effective-independent.
    print(f"  {'Jaccard>':>9} {'eff_indep':>10} {'redundancy':>11}")
    for thr in (0.30, 0.40, 0.50, 0.60, 0.70):
        gc = cluster_at(thr)
        eff = sum(1.0 / c["size"] for cl in gc.values() for c in cl for _ in c["members"])
        red = total_cells / eff if eff else float("nan")
        mark = "  <- persisted" if abs(thr - JACCARD_THRESHOLD) < 1e-9 else ""
        print(f"  {thr:>9.2f} {eff:>10.2f} {red:>10.2f}x{mark}")


def _top_pairs(cells, groups, firing, topn=18):
    """Highest pairwise Jaccard overlaps (same horizon+direction) — the empirical near-duplicates,
    including the thematic cousins that sit just below the 0.50 merge line."""
    print("\n" + "-" * 78)
    print(f"TOP {topn} PAIRWISE FIRING OVERLAPS (same horizon+direction):")
    print("-" * 78)
    pairs = []
    for key, members in groups.items():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                jac = jaccard(firing[cells[a]["id"]], firing[cells[b]["id"]])
                if jac > 0:
                    pairs.append((jac, key, cells[a]["id"], cells[b]["id"]))
    for jac, key, ida, idb in sorted(pairs, key=lambda x: -x[0])[:topn]:
        merged = "MERGE" if jac > JACCARD_THRESHOLD else "  -  "
        print(f"  {jac:5.3f} [{merged}] {key[0]:<11} {key[1]:<5} {ida:<38} ~ {idb}")


if __name__ == "__main__":
    main()
