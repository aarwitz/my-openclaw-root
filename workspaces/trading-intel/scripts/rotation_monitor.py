#!/usr/bin/env python3
"""Rotation monitor — makes basket-vs-basket "seesaw" action a first-class,
learnable observable (operator question 2026-07-15: DRAM/NVDA/hardware down
while GOOG/MSFT/META up — "can our system really learn and reason about that?").

Before this script the desk could only see rotation defensively (risk_model's
correlation clusters cap same-bet exposure) — nothing MEASURED capital moving
between baskets, so the world model mislearned seesaw days as "growth
mechanisms missed" instead of "money rotated inside the theme".

Deterministic, feature-store-only (no network). For each monitored AXIS
(pair of equal-weight baskets) it computes per day:
  spread_5d / spread_21d   : basket A minus basket B total return (%)
  corr_21d                 : rolling 21d correlation of daily basket returns
  corr_pctile              : today's corr vs its trailing 252d distribution
  spread_z                 : today's 5d spread vs trailing 252d 5d-spread dist
  seesaw (0/1)             : decoupling — corr_pctile <= 0.05 (pure seesaw:
                             alternating days crater correlation while the NET
                             spread stays small), OR corr_pctile <= 0.20 with
                             |spread_z| >= 1.5 (a trending rotation: one side
                             is persistently winning)

Rows land in rotation_snapshots (idempotent upsert); the pass runs `snapshot`
daily and the researcher/quant read it like the regime. Two rotation mechanisms
are seeded (neutral priors) so theses that cite them get graded into the world
model by the standard linker/calibrate path.

CLI:
  python3 rotation_monitor.py snapshot            # compute + persist today
  python3 rotation_monitor.py history --axis ai_hw_vs_sw --days 20
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_store as fs  # noqa: E402

DB = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")

# Basket definitions are deliberately boring, liquid, and stable. Adding an
# axis = adding an entry here; the table keys on (axis, date).
BASKETS: dict[str, list[str]] = {
    "ai_hardware": ["NVDA", "AMD", "AVGO", "MU", "TSM", "AMAT", "LRCX", "KLAC",
                    "ANET", "MRVL", "SMCI", "VRT"],
    "ai_software": ["MSFT", "GOOGL", "META", "AMZN", "ORCL", "CRM", "NOW", "PLTR"],
    "cyclicals":   ["CAT", "DE", "HON", "GE", "ETN", "PH", "EMR", "URI"],
    "defensives":  ["JNJ", "PG", "KO", "PEP", "MRK", "ABBV", "WMT", "MCD"],
}
AXES: dict[str, tuple[str, str]] = {
    "ai_hw_vs_sw":        ("ai_hardware", "ai_software"),
    "cyclical_vs_defensive": ("cyclicals", "defensives"),
}

LOOKBACK_D = 420          # calendar depth pulled from the feature store
CORR_WIN = 21
SPREAD_WIN_SHORT = 5
SPREAD_WIN_LONG = 21
DIST_WIN = 252            # trailing distribution for z / percentile
SEESAW_CORR_PCTILE = 0.20
SEESAW_SPREAD_Z = 1.5

ROTATION_MECHANISMS = [
    {"id": "mech-rotation-hw-to-sw",
     "name": "AI complex rotation: hardware to software",
     "antecedent": "rotation_hardware_software_seesaw",
     "consequent": "software_megacap_outperform"},
    {"id": "mech-rotation-sw-to-hw",
     "name": "AI complex rotation: software to hardware",
     "antecedent": "rotation_software_hardware_seesaw",
     "consequent": "hardware_semis_outperform"},
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _basket_series(tickers: list[str]) -> tuple[list[str], dict[str, float]]:
    """(sorted trade dates, date -> equal-weight basket daily return)."""
    per: list[tuple[list[str], dict[str, float]]] = []
    for t in tickers:
        try:
            px = fs._prices(t, LOOKBACK_D + 40)
        except Exception:
            continue
        if len(px) < 60:
            continue
        dts = [b["t"][:10] for b in px]
        cls = {b["t"][:10]: float(b["c"]) for b in px}
        rets = {}
        for i in range(1, len(dts)):
            c0, c1 = cls[dts[i - 1]], cls[dts[i]]
            if c0:
                rets[dts[i]] = c1 / c0 - 1.0
        per.append((dts[1:], rets))
    if len(per) < max(3, len(tickers) // 2):
        raise RuntimeError(f"basket too thin: {len(per)}/{len(tickers)} members priced")
    # intersect dates so every member votes on every day
    common = set(per[0][0])
    for dts, _ in per[1:]:
        common &= set(dts)
    dates = sorted(common)[-LOOKBACK_D:]
    out = {d: sum(r[d] for _, r in per) / len(per) for d in dates}
    return dates, out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    vx = sum((a - mx) ** 2 for a in xs)
    vy = sum((b - my) ** 2 for b in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def _axis_metrics(a_name: str, b_name: str) -> list[dict]:
    a_dates, a_ret = _basket_series(BASKETS[a_name])
    b_dates, b_ret = _basket_series(BASKETS[b_name])
    dates = sorted(set(a_dates) & set(b_dates))
    ra = [a_ret[d] for d in dates]
    rb = [b_ret[d] for d in dates]

    def cum(rets: list[float], i: int, w: int) -> float | None:
        if i - w + 1 < 0:
            return None
        prod = 1.0
        for r in rets[i - w + 1:i + 1]:
            prod *= (1 + r)
        return prod - 1.0

    rows = []
    corr_hist: list[float] = []
    spread5_hist: list[float] = []
    for i, d in enumerate(dates):
        corr = _pearson(ra[max(0, i - CORR_WIN + 1):i + 1],
                        rb[max(0, i - CORR_WIN + 1):i + 1]) if i >= CORR_WIN - 1 else None
        s5a, s5b = cum(ra, i, SPREAD_WIN_SHORT), cum(rb, i, SPREAD_WIN_SHORT)
        s21a, s21b = cum(ra, i, SPREAD_WIN_LONG), cum(rb, i, SPREAD_WIN_LONG)
        spread5 = (s5a - s5b) * 100 if (s5a is not None and s5b is not None) else None
        spread21 = (s21a - s21b) * 100 if (s21a is not None and s21b is not None) else None

        spread_z = corr_pctile = None
        if spread5 is not None and len(spread5_hist) >= 60:
            window = spread5_hist[-DIST_WIN:]
            mu = sum(window) / len(window)
            sd = math.sqrt(sum((x - mu) ** 2 for x in window) / max(1, len(window) - 1))
            spread_z = (spread5 - mu) / sd if sd > 0 else None
        if corr is not None and len(corr_hist) >= 60:
            window = corr_hist[-DIST_WIN:]
            corr_pctile = sum(1 for x in window if x <= corr) / len(window)

        # Two distinct regimes both flag: pure seesaw (alternating days -> corr
        # craters but net spread stays small) and trending rotation (decoupled
        # AND one side persistently winning).
        decoupled = corr_pctile is not None and corr_pctile <= 0.05
        trending = (corr_pctile is not None and spread_z is not None
                    and corr_pctile <= SEESAW_CORR_PCTILE
                    and abs(spread_z) >= SEESAW_SPREAD_Z)
        seesaw = int(decoupled or trending)
        rows.append({"date": d, "corr_21d": corr, "spread_5d_pct": spread5,
                     "spread_21d_pct": spread21, "spread_z": spread_z,
                     "corr_pctile": corr_pctile, "seesaw": seesaw})
        if corr is not None:
            corr_hist.append(corr)
        if spread5 is not None:
            spread5_hist.append(spread5)
    return rows


def _ensure_table(conn) -> None:
    conn.execute("""
      CREATE TABLE IF NOT EXISTS rotation_snapshots (
        axis           TEXT NOT NULL,
        date           TEXT NOT NULL,
        corr_21d       REAL,
        spread_5d_pct  REAL,     -- basket A minus basket B, percent
        spread_21d_pct REAL,
        spread_z       REAL,
        corr_pctile    REAL,
        seesaw         INTEGER NOT NULL DEFAULT 0,
        computed_at    TEXT NOT NULL,
        PRIMARY KEY (axis, date)
      )""")


def _seed_mechanisms(conn) -> None:
    for m in ROTATION_MECHANISMS:
        conn.execute(
            "INSERT OR IGNORE INTO mechanisms (id, created_at, created_by, name, antecedent_class, "
            "consequent_class, direction, horizon, prior_alpha, prior_beta, observed_hits, "
            "observed_misses, posterior_mean, half_life_days, status, experiment_id) "
            "VALUES (?, ?, 'quant', ?, ?, ?, 'long', 'position_1_4w', 1.0, 1.0, 0, 0, 0.5, 90, "
            "'active', 'world_model_v1')",
            (m["id"], _now(), m["name"], m["antecedent"], m["consequent"]))


def snapshot(persist_days: int = 30) -> dict:
    conn = sqlite3.connect(DB, timeout=60.0)
    _ensure_table(conn)
    _seed_mechanisms(conn)
    out = {}
    for axis, (a, b) in AXES.items():
        rows = _axis_metrics(a, b)
        for r in rows[-persist_days:]:
            conn.execute(
                "INSERT OR REPLACE INTO rotation_snapshots (axis, date, corr_21d, spread_5d_pct, "
                "spread_21d_pct, spread_z, corr_pctile, seesaw, computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (axis, r["date"], r["corr_21d"], r["spread_5d_pct"], r["spread_21d_pct"],
                 r["spread_z"], r["corr_pctile"], r["seesaw"], _now()))
        last = rows[-1]
        out[axis] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in last.items()}
        out[axis]["leader"] = (a if (last["spread_5d_pct"] or 0) > 0 else b)
    conn.commit()
    conn.close()
    return out


def history(axis: str, days: int) -> list[dict]:
    conn = sqlite3.connect(DB, timeout=60.0)
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM rotation_snapshots WHERE axis=? ORDER BY date DESC LIMIT ?",
        (axis, days))]
    conn.close()
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("snapshot")
    h = sub.add_parser("history")
    h.add_argument("--axis", required=True, choices=sorted(AXES))
    h.add_argument("--days", type=int, default=20)
    a = ap.parse_args(argv)
    if a.cmd == "history":
        print(json.dumps(history(a.axis, a.days), indent=2))
    else:
        print(json.dumps(snapshot(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
