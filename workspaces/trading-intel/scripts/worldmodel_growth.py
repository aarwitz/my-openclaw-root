#!/usr/bin/env python3
"""World-model GROWTH report — deterministic, no-LLM snapshot of how the desk's
built-up knowledge is developing over time.

Each run captures a metrics snapshot to state/worldmodel-growth.jsonl, then
reports the current state AND the delta vs the previous snapshot, so learning is
measurable (not just sensed): live-evidence depth, mechanism posteriors moving
off their backtest priors, KG/causal growth, idea→intent→trade flow, and
realized performance vs SPY.

Pairs with the host-cron wrapper scripts/learning-growth-report.sh (weekly).
Read-only on the trading stores; only writes its own append-only JSONL.

  python3 worldmodel_growth.py [--no-store]   # --no-store: don't append a snapshot
"""
from __future__ import annotations
import argparse, json, os, sqlite3, datetime

OC = os.path.expanduser("~/.openclaw")
DB_TI = os.path.join(OC, "state/trading-intel.sqlite")
DB_FEAT = os.path.join(OC, "state/features.sqlite")
SNAP = os.path.join(OC, "state/worldmodel-growth.jsonl")


def conn(p):
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def scalar(c, sql, default=None):
    try:
        r = c.execute(sql).fetchone()
        return r[0] if r and r[0] is not None else default
    except Exception:
        return default


def collect() -> dict:
    m: dict = {}
    ti = conn(DB_TI)
    ft = conn(DB_FEAT)

    # --- live-evidence depth (the real bottleneck to value) ---
    m["mech_total"] = scalar(ti, "SELECT COUNT(*) FROM mechanisms", 0)
    m["mech_active"] = scalar(ti, "SELECT COUNT(*) FROM mechanisms WHERE status='active'", 0)
    m["mech_live_obs"] = scalar(
        ti, "SELECT COUNT(*) FROM mechanisms WHERE COALESCE(observed_hits,0)+COALESCE(observed_misses,0)>0", 0)
    m["observations"] = scalar(ti, "SELECT COUNT(*) FROM mechanism_observations", 0)
    m["pred_total"] = scalar(ti, "SELECT COUNT(*) FROM predictions", 0)
    m["pred_resolved"] = scalar(ti, "SELECT COUNT(*) FROM predictions WHERE resolved_at IS NOT NULL", 0)
    m["brier_avg"] = scalar(ti, "SELECT ROUND(AVG(brier_component),4) FROM predictions WHERE brier_component IS NOT NULL")
    m["realized_ret_avg"] = scalar(ti, "SELECT ROUND(AVG(realized_return_pct),3) FROM predictions WHERE realized_return_pct IS NOT NULL")

    # --- posteriors moving off the backtest prior ---
    moved, shifts = 0, []
    try:
        for r in ti.execute("SELECT id,prior_alpha,prior_beta,posterior_mean FROM mechanisms"):
            pa, pb, post = r["prior_alpha"], r["prior_beta"], r["posterior_mean"]
            if None not in (pa, pb, post) and (pa + pb) > 0:
                shift = abs(post - pa / (pa + pb))
                if shift > 0.005:
                    moved += 1
                    shifts.append((round(shift, 4), r["id"]))
    except Exception:
        pass
    m["mech_moved_off_prior"] = moved
    m["mech_top_mover"] = (sorted(shifts, reverse=True)[0] if shifts else None)

    # --- knowledge graph + causal layer ---
    m["kg_nodes"] = scalar(ft, "SELECT COUNT(*) FROM kg_nodes", 0)
    m["kg_edges"] = scalar(ft, "SELECT COUNT(*) FROM kg_edges", 0)
    m["causal_edges"] = scalar(ft, "SELECT COUNT(*) FROM causal_edges", 0)
    m["entities"] = scalar(ft, "SELECT COUNT(*) FROM entities", 0)
    m["calibrated"] = scalar(ft, "SELECT COUNT(*) FROM calibrated_mechanisms", 0)
    m["discovered"] = scalar(ft, "SELECT COUNT(*) FROM discovered_mechanisms", 0)

    # --- idea -> intent -> trade flow ---
    m["hypotheses"] = scalar(ti, "SELECT COUNT(*) FROM hypotheses", 0)
    m["trade_intents"] = scalar(ti, "SELECT COUNT(*) FROM trade_intents", 0)
    m["orders"] = scalar(ti, "SELECT COUNT(*) FROM orders", 0)
    m["positions"] = scalar(ti, "SELECT COUNT(*) FROM positions", 0)
    m["episodes"] = scalar(ti, "SELECT COUNT(*) FROM episodes", 0)

    # --- realized performance ---
    m["equity"] = scalar(ti, "SELECT equity FROM portfolio_snapshots ORDER BY captured_at DESC LIMIT 1")
    m["day_pl"] = scalar(ti, "SELECT day_pl FROM portfolio_snapshots ORDER BY captured_at DESC LIMIT 1")
    # Use the cumulative 'all' horizon row (the headline), not whatever horizon
    # happens to be latest by captured_at — the table holds several per capture.
    for k, col in (("port_ret", "portfolio_return_pct"), ("spy_ret", "spy_return_pct"),
                   ("alpha", "alpha_pct"), ("sharpe", "sharpe_estimate")):
        m[k] = scalar(ti, f"SELECT {col} FROM benchmarks WHERE horizon='all' ORDER BY captured_at DESC LIMIT 1")
    return m


def prev_snapshot() -> dict | None:
    try:
        with open(SNAP) as f:
            lines = [ln for ln in f if ln.strip()]
        return json.loads(lines[-1])["metrics"] if lines else None
    except Exception:
        return None


def d(now, prev, key):
    """Signed delta string vs prev snapshot, or '' when not comparable."""
    if prev is None or prev.get(key) is None or now.get(key) is None:
        return ""
    try:
        diff = now[key] - prev[key]
    except TypeError:
        return ""
    if diff == 0:
        return " (±0)"
    return f" ({'+' if diff > 0 else ''}{round(diff, 3)})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-store", action="store_true", help="don't append a snapshot")
    a = ap.parse_args()

    now = collect()
    prev = prev_snapshot()
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    mover = now.get("mech_top_mover")
    mover_s = f"{mover[1]} (+{mover[0]})" if mover else "none yet"

    print(f"===== WORLD-MODEL GROWTH REPORT  {ts} =====")
    print(f"(delta vs previous snapshot{' — none yet, baseline' if prev is None else ''})\n")
    print("LIVE LEARNING (the bottleneck to value):")
    print(f"  mechanisms with live evidence : {now['mech_live_obs']}/{now['mech_total']}{d(now,prev,'mech_live_obs')}")
    print(f"  graded observations           : {now['observations']}{d(now,prev,'observations')}")
    print(f"  predictions resolved/total    : {now['pred_resolved']}/{now['pred_total']}{d(now,prev,'pred_resolved')}")
    print(f"  avg Brier (lower=better)      : {now['brier_avg']}")
    print(f"  avg realized return %         : {now['realized_ret_avg']}")
    print(f"  mechanisms moved off prior    : {now['mech_moved_off_prior']}{d(now,prev,'mech_moved_off_prior')}  top: {mover_s}")
    print("\nKNOWLEDGE GRAPH / CAUSAL:")
    print(f"  kg nodes / edges              : {now['kg_nodes']}{d(now,prev,'kg_nodes')} / {now['kg_edges']}{d(now,prev,'kg_edges')}")
    print(f"  causal edges / entities       : {now['causal_edges']}{d(now,prev,'causal_edges')} / {now['entities']}{d(now,prev,'entities')}")
    print(f"  calibrated / discovered mechs : {now['calibrated']}{d(now,prev,'calibrated')} / {now['discovered']}{d(now,prev,'discovered')}")
    print("\nIDEA -> INTENT -> TRADE FLOW:")
    print(f"  hypotheses / intents / orders : {now['hypotheses']}{d(now,prev,'hypotheses')} / {now['trade_intents']}{d(now,prev,'trade_intents')} / {now['orders']}{d(now,prev,'orders')}")
    print(f"  positions / episodes          : {now['positions']}{d(now,prev,'positions')} / {now['episodes']}{d(now,prev,'episodes')}")
    print("\nREALIZED PERFORMANCE:")
    print(f"  equity                        : {now['equity']}{d(now,prev,'equity')}")
    print(f"  return % vs SPY % (alpha)     : {now['port_ret']} vs {now['spy_ret']}  (alpha {now['alpha']}, Sharpe {now['sharpe']})")

    # Compact Telegram block (extracted by the wrapper between the markers).
    print("\n===TG_START===")
    print("📈 World-model weekly growth")
    print(f"Live: {now['mech_live_obs']}/{now['mech_total']} mechs w/ evidence{d(now,prev,'mech_live_obs')}, "
          f"{now['observations']} obs{d(now,prev,'observations')}, {now['pred_resolved']}/{now['pred_total']} preds resolved{d(now,prev,'pred_resolved')}")
    print(f"Model: {now['mech_moved_off_prior']} mechs moved off prior{d(now,prev,'mech_moved_off_prior')}; "
          f"KG {now['kg_nodes']}n/{now['kg_edges']}e{d(now,prev,'kg_edges')}, causal {now['causal_edges']}{d(now,prev,'causal_edges')}")
    print(f"Flow: {now['hypotheses']} hyps{d(now,prev,'hypotheses')}, {now['trade_intents']} intents, {now['positions']} positions")
    print(f"P&L: equity {now['equity']}{d(now,prev,'equity')}; return {now['port_ret']}% vs SPY {now['spy_ret']}% (alpha {now['alpha']}%, Sharpe {now['sharpe']})")
    print("===TG_END===")

    if not a.no_store:
        with open(SNAP, "a") as f:
            f.write(json.dumps({"ts": ts, "metrics": now}) + "\n")
        print(f"\nsnapshot appended -> {SNAP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
