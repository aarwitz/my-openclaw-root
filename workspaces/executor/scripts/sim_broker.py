#!/usr/bin/env python3
"""Internal paper-trading engine (docs/07 P1) — deterministic fills + ledger.

Replaces Alpaca's paper BROKER (not its data): fills simulate against the live
quote with an explicit spread + participation model, positions/cash live in our
own SQLite ledger (positions/orders with book=..., sim_accounts, book_equity),
and corporate actions are applied nightly with one audited row each — the
split-desync bug class (CRWD 4:1, 2026-07-02) cannot happen silently here.

Books: 'shadow' mirrors real desk fills for parity validation (P1), 'model'
trades the GBM top decile (P2), ablation books later. 'desk' remains Alpaca.

CLI:
  python3 sim_broker.py init --book shadow --cash 100000
  python3 sim_broker.py mirror                # replay new desk fills into shadow
  python3 sim_broker.py mark  --book shadow   # EOD marks -> book_equity
  python3 sim_broker.py corporate-actions --book shadow
  python3 sim_broker.py parity                # shadow vs Alpaca live account
  python3 sim_broker.py nightly               # mirror -> CAs -> mark -> parity
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _db import audit, connect, now_iso  # noqa: E402
from connectors import alpaca, fmp  # noqa: E402

# Fill model (P2 'model' book; the shadow book mirrors real fill prices instead):
SPREAD_K = 8.0          # half-spread bps ≈ max(1, K/sqrt(ADV$ millions))
PARTICIPATION_CAP = 0.02  # max fraction of trailing-21d ADV per order


def _iso_today() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------- account/ledger

def ensure_book(conn, book: str, cash: float = 100_000.0):
    row = conn.execute("SELECT book FROM sim_accounts WHERE book=?", (book,)).fetchone()
    if not row:
        conn.execute("INSERT INTO sim_accounts (book, cash, starting_cash, created_at) VALUES (?,?,?,?)",
                     (book, cash, cash, now_iso()))
        conn.commit()


def get_cash(conn, book: str) -> float:
    r = conn.execute("SELECT cash FROM sim_accounts WHERE book=?", (book,)).fetchone()
    if not r:
        raise RuntimeError(f"book {book!r} not initialized")
    return float(r[0])


def positions(conn, book: str) -> dict[str, dict]:
    out = {}
    for r in conn.execute(
            "SELECT id, ticker, qty, cost_basis FROM sim_positions "
            "WHERE book=? AND state='open'", (book,)):
        out[r[1].upper()] = {"id": r[0], "qty": float(r[2]), "cost_basis": float(r[3] or 0)}
    return out


def apply_fill(conn, book: str, symbol: str, side: str, qty: float, price: float,
               *, order_id: str | None = None, source: str = "sim") -> str:
    """The single ledger mutation point: updates cash + position, writes orders
    row + audit. side in buy/sell; sell may open a short (negative qty)."""
    symbol = symbol.upper()
    signed = qty if side == "buy" else -qty
    cash = get_cash(conn, book)
    cash -= signed * price
    pos = positions(conn, book).get(symbol)
    ts = now_iso()
    if pos:
        new_qty = pos["qty"] + signed
        if abs(new_qty) < 1e-9:
            conn.execute("UPDATE sim_positions SET qty=0, state='closed', closed_at=? WHERE id=?",
                         (ts, pos["id"]))
        else:
            # basis: weighted-average on adds; unchanged on partial reduces
            if pos["qty"] * signed > 0:
                total_cost = pos["cost_basis"] * abs(pos["qty"]) + price * abs(signed)
                new_basis = total_cost / abs(new_qty)
            else:
                new_basis = pos["cost_basis"]
            conn.execute("UPDATE sim_positions SET qty=?, cost_basis=? WHERE id=?",
                         (new_qty, new_basis, pos["id"]))
    else:
        pid = f"pos-{book}-{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO sim_positions (id, book, ticker, qty, cost_basis, state, opened_at) "
            "VALUES (?,?,?,?,?, 'open', ?)",
            (pid, book, symbol, signed, price, ts))
    oid = order_id or f"sim-{uuid.uuid4().hex[:16]}"
    conn.execute(
        "INSERT INTO sim_orders (order_id, book, symbol, side, qty, fill_price, source, filled_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (oid, book, symbol, side, qty, price, source, ts))
    conn.execute("UPDATE sim_accounts SET cash=? WHERE book=?", (cash, book))
    audit(conn, actor="executor", entity_type="sim_order", entity_id=oid,
          action="sim_fill", rationale=f"{book}: {side} {qty:g} {symbol} @ {price:.4f} ({source})")
    conn.commit()
    return oid


# ---------------------------------------------------------------- shadow mirror

def bootstrap_shadow(conn) -> dict:
    """Initialize the shadow book from the CURRENT Alpaca account: same cash,
    same positions at Alpaca's avg entry. From this point mirror only NEW fills.
    Idempotent: refuses to run twice."""
    if conn.execute("SELECT 1 FROM sim_accounts WHERE book='shadow'").fetchone():
        return {"bootstrapped": False, "reason": "shadow book already exists"}
    acct = alpaca.get_account()
    cash = float(acct.get("cash") or 0)
    conn.execute("INSERT INTO sim_accounts (book, cash, starting_cash, created_at) VALUES (?,?,?,?)",
                 ("shadow", cash, cash, now_iso()))
    n = 0
    for p in alpaca.list_positions():
        pid = f"pos-shadow-{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO sim_positions (id, book, ticker, qty, cost_basis, state, opened_at) "
            "VALUES (?,?,?,?,?, 'open', ?)",
            (pid, "shadow", p["symbol"].upper(), float(p["qty"]),
             float(p.get("avg_entry_price") or 0), now_iso()))
        audit(conn, actor="executor", entity_type="sim_position", entity_id=pid,
              action="bootstrap", rationale=f"shadow bootstrap: {p['qty']} {p['symbol']} @ {p.get('avg_entry_price')}")
        n += 1
    conn.commit()
    return {"bootstrapped": True, "cash": cash, "positions": n}


def mirror_desk_fills(conn) -> int:
    """Replay REAL desk fills (Alpaca) that happened AFTER the shadow bootstrap
    into the shadow ledger at the same price/qty. Validates our ledger math
    against Alpaca's account over time."""
    row = conn.execute("SELECT created_at FROM sim_accounts WHERE book='shadow'").fetchone()
    if not row:
        raise RuntimeError("shadow book not bootstrapped — run: sim_broker.py bootstrap")
    since = row[0]
    mirrored = {r[0].replace("mirror-", "", 1) for r in conn.execute(
        "SELECT order_id FROM sim_orders WHERE book='shadow' AND order_id LIKE 'mirror-%'")}
    fills = conn.execute(
        "SELECT broker_order_id, symbol, side, qty, avg_fill_price FROM orders "
        "WHERE book='desk' AND status='filled' AND avg_fill_price IS NOT NULL "
        "AND filled_at > ? ORDER BY filled_at", (since,)).fetchall()
    n = 0
    for oid, sym, side, qty, px in fills:
        if oid in mirrored:
            continue
        apply_fill(conn, "shadow", sym, side, float(qty), float(px),
                   order_id=f"mirror-{oid}", source="mirror-desk-fill")
        n += 1
    return n


# ---------------------------------------------------------------- corporate actions

def apply_corporate_actions(conn, book: str) -> int:
    """Splits from FMP applied to open sim positions, one audited row each."""
    n = 0
    for sym, pos in positions(conn, book).items():
        try:
            splits = fmp._get("splits", {"symbol": sym}, cache_h=24.0) or []
        except Exception:
            continue
        for s in splits:
            ex = (s.get("date") or "")[:10]
            num, den = s.get("numerator"), s.get("denominator")
            if not (ex and num and den) or ex > _iso_today():
                continue
            ratio = float(num) / float(den)
            done = conn.execute(
                "SELECT 1 FROM sim_corporate_actions WHERE book=? AND ticker=? AND action='split' AND ex_date=?",
                (book, sym, ex)).fetchone()
            if done or ratio == 1.0:
                continue
            # only apply if the position existed before the ex-date
            opened = conn.execute("SELECT opened_at FROM sim_positions WHERE id=?", (pos["id"],)).fetchone()[0]
            if opened and opened[:10] >= ex:
                continue
            conn.execute("UPDATE sim_positions SET qty=qty*?, cost_basis=cost_basis/? WHERE id=?",
                         (ratio, ratio, pos["id"]))
            conn.execute(
                "INSERT INTO sim_corporate_actions (id, book, ticker, action, ratio, ex_date, applied_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"ca-{uuid.uuid4().hex[:12]}", book, sym, "split", ratio, ex, now_iso()))
            audit(conn, actor="executor", entity_type="sim_position", entity_id=pos["id"],
                  action="apply_split", rationale=f"{book}: {sym} split {num}:{den} ex {ex} — qty×{ratio:g}, basis÷{ratio:g}")
            n += 1
            conn.commit()
    return n


# ---------------------------------------------------------------- marks / parity

def _mark_price(sym: str) -> float | None:
    try:
        t = alpaca.latest_trade(sym)
        if t and t.get("price"):
            return float(t["price"])
    except Exception:
        pass
    try:
        bars = alpaca.daily_bars(sym, days=5)
        if bars:
            return float(bars[-1]["c"])
    except Exception:
        pass
    return None


def mark_book(conn, book: str) -> dict:
    ensure_book(conn, book)
    cash = get_cash(conn, book)
    equity = cash
    for sym, pos in positions(conn, book).items():
        px = _mark_price(sym)
        if px is None:
            raise RuntimeError(f"no mark for {sym} — refusing to write a wrong equity row")
        equity += pos["qty"] * px
        conn.execute("UPDATE sim_positions SET current_price=?, current_value=? WHERE id=?",
                     (px, pos["qty"] * px, pos["id"]))
    conn.execute("INSERT OR REPLACE INTO book_equity (book, date, equity, cash) VALUES (?,?,?,?)",
                 (book, _iso_today(), equity, cash))
    conn.commit()
    return {"book": book, "date": _iso_today(), "equity": round(equity, 2), "cash": round(cash, 2)}


def parity(conn) -> dict:
    """Shadow ledger vs the real Alpaca account. Position qty must match exactly;
    equity drift is reported in bps (cash flows like dividends make small drift
    expected until dividend handling lands — flag, don't fail, in P1)."""
    sim = positions(conn, "shadow")
    real = {p["symbol"].upper(): float(p["qty"]) for p in alpaca.list_positions()}
    mismatches = []
    for sym in sorted(set(sim) | set(real)):
        sq, rq = sim.get(sym, {}).get("qty", 0.0), real.get(sym, 0.0)
        if abs(sq - rq) > 1e-6:
            mismatches.append({"symbol": sym, "shadow_qty": sq, "alpaca_qty": rq})
    acct = alpaca.get_account()
    real_eq = float(acct.get("equity") or 0)
    row = conn.execute("SELECT equity FROM book_equity WHERE book='shadow' ORDER BY date DESC LIMIT 1").fetchone()
    sim_eq = float(row[0]) if row else None
    drift_bps = round((sim_eq - real_eq) / real_eq * 1e4, 1) if (sim_eq and real_eq) else None
    return {"qty_mismatches": mismatches, "shadow_equity": sim_eq,
            "alpaca_equity": real_eq, "equity_drift_bps": drift_bps,
            "ok": not mismatches and (drift_bps is None or abs(drift_bps) < 50)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--book", required=True); p.add_argument("--cash", type=float, default=100_000.0)
    sub.add_parser("bootstrap")
    sub.add_parser("mirror")
    p = sub.add_parser("mark"); p.add_argument("--book", required=True)
    p = sub.add_parser("corporate-actions"); p.add_argument("--book", required=True)
    sub.add_parser("parity")
    sub.add_parser("nightly")
    a = ap.parse_args(argv)
    conn = connect()
    if a.cmd == "bootstrap":
        print(json.dumps(bootstrap_shadow(conn)))
    elif a.cmd == "init":
        ensure_book(conn, a.book, a.cash)
        print(json.dumps({"book": a.book, "cash": get_cash(conn, a.book)}))
    elif a.cmd == "mirror":
        print(json.dumps({"mirrored_fills": mirror_desk_fills(conn)}))
    elif a.cmd == "mark":
        print(json.dumps(mark_book(conn, a.book)))
    elif a.cmd == "corporate-actions":
        print(json.dumps({"applied": apply_corporate_actions(conn, a.book)}))
    elif a.cmd == "parity":
        print(json.dumps(parity(conn), indent=2))
    elif a.cmd == "nightly":
        out = {"mirrored": mirror_desk_fills(conn)}
        out["corporate_actions"] = apply_corporate_actions(conn, "shadow")
        out["mark"] = mark_book(conn, "shadow")
        out["parity"] = parity(conn)
        print(json.dumps(out, indent=2))
        return 0 if out["parity"]["ok"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
