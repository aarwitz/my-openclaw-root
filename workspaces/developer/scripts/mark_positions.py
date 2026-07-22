#!/usr/bin/env python3
"""developer · mark_positions.py — populate per-position P&L on the canonical book.

The sim engine marks `sim_positions` (its own book) and writes `book_equity`, but the
canonical `positions` table's P&L columns — `unrealized_pnl_pct`, `pnl_ideal`,
`pnl_slippage_adjusted` — were NEVER populated. As a cascade, `compute_attribution.py`
(which reads `unrealized_pnl_pct` as the per-trade return) produced NULL
`realized_edge_vs_spy_bps` for every closed trade, so the desk could not see which
individual trades beat the market. This closes that gap.

Deterministic, sign-aware (shorts profit when price falls):
  * OPEN positions   — marked to the live price (marketdata façade → Massive), giving
    fresh unrealized P&L each pass.
  * CLOSED positions — realized return locked from cost_basis (entry) vs current_price
    (the exit fill sync_fills recorded at close); direction from the opening intent
    (defaults long — the desk has no closed shorts).

Run as a pass stage BEFORE compute_attribution. Writes only the P&L columns on
`positions`; never touches qty/state/lineage. `--dry-run` prints without writing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _db import connect, emit, now_iso  # noqa: E402
from connectors.marketdata import latest_trade  # noqa: E402  (Massive-backed live price)

OPEN_STATES = ("opening", "open", "scaling", "trimming", "closing")
MODELED_SLIPPAGE_BPS = 8.0  # modeled exit half-spread (matches author_intents)


def _pnl(cost_basis: float, price: float, qty_signed: float, direction: int) -> dict:
    """Sign-aware P&L. direction: +1 long, -1 short. qty_signed used for $ amounts."""
    ret_pct = direction * (price - cost_basis) / cost_basis * 100.0 if cost_basis > 0 else None
    pnl_ideal = (price - cost_basis) * qty_signed  # signed qty makes shorts sign-correct
    pnl_slip = pnl_ideal - abs(qty_signed * price) * (MODELED_SLIPPAGE_BPS / 10_000.0)
    return {
        "unrealized_pnl_pct": None if ret_pct is None else round(ret_pct, 4),
        "pnl_ideal": round(pnl_ideal, 2),
        "pnl_slippage_adjusted": round(pnl_slip, 2),
    }


def _closed_directions(conn) -> dict[str, int]:
    """position_id -> +1/-1 from the opening intent's direction (default long)."""
    out: dict[str, int] = {}
    for r in conn.execute(
        "SELECT p.id AS pid, ti.direction AS dir FROM positions p "
        "LEFT JOIN trade_intents ti ON ti.hypothesis_id = p.hypothesis_id AND ti.action='open' "
        "WHERE p.state='closed'"
    ):
        out[r["pid"]] = -1 if (r["dir"] or "long") == "short" else 1
    return out


def mark_open(conn, *, dry_run: bool) -> list[dict]:
    rows = conn.execute(
        f"SELECT id, ticker, qty, cost_basis FROM positions "
        f"WHERE state IN ({','.join('?' * len(OPEN_STATES))})",
        OPEN_STATES,
    ).fetchall()
    out = []
    for r in rows:
        qty = float(r["qty"] or 0.0)
        basis = float(r["cost_basis"] or 0.0)
        if qty == 0 or basis <= 0:
            continue
        lt = latest_trade(r["ticker"])
        price = float(lt["price"]) if lt and lt.get("price") else None
        if not price:
            out.append({"id": r["id"], "ticker": r["ticker"], "skipped": "no_mark"})
            continue
        direction = 1 if qty > 0 else -1
        pnl = _pnl(basis, price, qty, direction)
        rec = {"id": r["id"], "ticker": r["ticker"], "state": "open",
               "current_price": round(price, 4), "current_value": round(qty * price, 2), **pnl}
        if not dry_run:
            conn.execute(
                "UPDATE positions SET current_price=?, current_value=?, "
                "unrealized_pnl_pct=?, pnl_ideal=?, pnl_slippage_adjusted=? WHERE id=?",
                (rec["current_price"], rec["current_value"], pnl["unrealized_pnl_pct"],
                 pnl["pnl_ideal"], pnl["pnl_slippage_adjusted"], r["id"]),
            )
        out.append(rec)
    return out


def finalize_closed(conn, *, dry_run: bool) -> list[dict]:
    """Lock realized P&L on closed positions still missing it (from entry vs exit price)."""
    dirs = _closed_directions(conn)
    rows = conn.execute(
        "SELECT id, ticker, cost_basis, current_price FROM positions "
        "WHERE state='closed' AND unrealized_pnl_pct IS NULL "
        "AND current_price IS NOT NULL AND current_price > 0 AND cost_basis > 0"
    ).fetchall()
    out = []
    for r in rows:
        basis = float(r["cost_basis"])
        price = float(r["current_price"])  # exit fill recorded at close
        direction = dirs.get(r["id"], 1)
        ret_pct = round(direction * (price - basis) / basis * 100.0, 4)
        rec = {"id": r["id"], "ticker": r["ticker"], "state": "closed",
               "realized_return_pct": ret_pct, "direction": "short" if direction < 0 else "long"}
        if not dry_run:
            # qty is 0 after close so $-amounts aren't reconstructable; the RETURN is what
            # attribution needs. Store it in unrealized_pnl_pct (the column the reader uses).
            conn.execute(
                "UPDATE positions SET unrealized_pnl_pct=? WHERE id=?", (ret_pct, r["id"]),
            )
        out.append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    conn = connect()
    opened = mark_open(conn, dry_run=args.dry_run)
    closed = finalize_closed(conn, dry_run=args.dry_run)
    if not args.dry_run:
        conn.commit()
    marked = [o for o in opened if "skipped" not in o]
    emit({
        "marked_open": len(marked),
        "skipped_open": len(opened) - len(marked),
        "finalized_closed": len(closed),
        "dry_run": bool(args.dry_run),
        "generated_at": now_iso(),
        "sample_open": marked[:3],
        "sample_closed": closed[:3],
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
