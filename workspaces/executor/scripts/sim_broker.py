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
  python3 sim_broker.py rebalance-model [--force]   # P2: top-decile GBM model book
  python3 sim_broker.py nightly               # mirror -> CAs -> mark -> parity
                                              #   -> model CAs/rebalance(monthly)/mark
"""

from __future__ import annotations

import argparse
import json
import os
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
CASH_YIELD_MAX_APY = float(os.environ.get("SIM_CASH_YIELD_MAX_APY", "0.10"))
CASH_YIELD_FALLBACK_APY = float(os.environ.get("SIM_CASH_YIELD_FALLBACK_APY", "0.045"))


def _iso_today() -> str:
    return date.today().isoformat()


def _ensure_cash_yield_tables(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sim_cash_yield_events ("
        "id TEXT PRIMARY KEY, "
        "book TEXT NOT NULL, "
        "as_of_date TEXT NOT NULL, "
        "annual_yield REAL NOT NULL, "
        "cash_start REAL NOT NULL, "
        "credit REAL NOT NULL, "
        "applied_at TEXT NOT NULL, "
        "UNIQUE(book, as_of_date))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS book_return_attribution ("
        "book TEXT NOT NULL, "
        "date TEXT NOT NULL, "
        "equity REAL NOT NULL, "
        "last_equity REAL, "
        "trading_pl REAL NOT NULL, "
        "cash_yield_pl REAL NOT NULL, "
        "total_pl REAL NOT NULL, "
        "trading_return_pct REAL, "
        "cash_yield_return_pct REAL, "
        "total_return_pct REAL, "
        "created_at TEXT NOT NULL, "
        "PRIMARY KEY(book, date))"
    )


def _sgov_proxy_apy() -> float:
    """Estimate risk-free cash yield from SGOV trailing 21d return.

    Falls back to a conservative env-configured APY when SGOV data is missing.
    """
    try:
        bars = alpaca.daily_bars("SGOV", days=45)
        if len(bars) >= 22:
            c0 = float(bars[-22]["c"])
            c1 = float(bars[-1]["c"])
            if c0 > 0 and c1 > 0:
                ret_21d = (c1 / c0) - 1.0
                apy = ret_21d * (252.0 / 21.0)
                # SGOV price alone can understate yield because distributions
                # carry a chunk of the return. Floor at fallback APY so idle
                # cash accounting stays broker-realistic.
                apy = max(CASH_YIELD_FALLBACK_APY, apy)
                return max(0.0, min(CASH_YIELD_MAX_APY, apy))
    except Exception:
        pass
    return max(0.0, min(CASH_YIELD_MAX_APY, CASH_YIELD_FALLBACK_APY))


def _apply_cash_yield_once_per_day(conn, book: str) -> dict:
    today = _iso_today()
    row = conn.execute(
        "SELECT annual_yield, cash_start, credit FROM sim_cash_yield_events "
        "WHERE book=? AND as_of_date=?",
        (book, today),
    ).fetchone()
    if row:
        return {
            "applied": False,
            "already_applied": True,
            "annual_yield": float(row["annual_yield"]),
            "cash_start": float(row["cash_start"]),
            "credit": float(row["credit"]),
        }

    cash_start = get_cash(conn, book)
    annual_yield = _sgov_proxy_apy()
    credit = round(cash_start * annual_yield / 252.0, 6)
    if credit != 0:
        conn.execute("UPDATE sim_accounts SET cash=cash+? WHERE book=?", (credit, book))
    eid = f"cye-{book}-{uuid.uuid4().hex[:14]}"
    conn.execute(
        "INSERT INTO sim_cash_yield_events (id, book, as_of_date, annual_yield, cash_start, credit, applied_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (eid, book, today, annual_yield, cash_start, credit, now_iso()),
    )
    audit(
        conn,
        actor="executor",
        entity_type="sim_account",
        entity_id=book,
        action="cash_yield_credit",
        rationale=f"{book}: SGOV-proxy APY {annual_yield:.4%} on ${cash_start:.2f} -> +${credit:.2f}",
    )
    return {
        "applied": True,
        "already_applied": False,
        "annual_yield": annual_yield,
        "cash_start": cash_start,
        "credit": credit,
    }


def _write_return_attribution(conn, book: str, equity: float, cash_yield_credit: float):
    today = _iso_today()
    prev = conn.execute(
        "SELECT equity FROM book_equity WHERE book=? AND date<? ORDER BY date DESC LIMIT 1",
        (book, today),
    ).fetchone()
    last_equity = float(prev[0]) if prev else None
    total_pl = 0.0 if last_equity in (None, 0.0) else (equity - last_equity)
    trading_pl = total_pl - float(cash_yield_credit)
    trading_ret = None if last_equity in (None, 0.0) else (trading_pl / last_equity) * 100.0
    cash_ret = None if last_equity in (None, 0.0) else (float(cash_yield_credit) / last_equity) * 100.0
    total_ret = None if last_equity in (None, 0.0) else (total_pl / last_equity) * 100.0
    conn.execute(
        "INSERT OR REPLACE INTO book_return_attribution ("
        "book, date, equity, last_equity, trading_pl, cash_yield_pl, total_pl, "
        "trading_return_pct, cash_yield_return_pct, total_return_pct, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            book,
            today,
            equity,
            last_equity,
            trading_pl,
            float(cash_yield_credit),
            total_pl,
            trading_ret,
            cash_ret,
            total_ret,
            now_iso(),
        ),
    )
    return {
        "date": today,
        "equity": round(equity, 2),
        "last_equity": None if last_equity is None else round(last_equity, 2),
        "trading_pl": round(trading_pl, 2),
        "cash_yield_pl": round(float(cash_yield_credit), 2),
        "total_pl": round(total_pl, 2),
        "trading_return_pct": None if trading_ret is None else round(trading_ret, 4),
        "cash_yield_return_pct": None if cash_ret is None else round(cash_ret, 4),
        "total_return_pct": None if total_ret is None else round(total_ret, 4),
    }


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


# ---------------------------------------------------------------- model book (P2)

FEAT_DB = "/home/aaron/.openclaw/state/features.sqlite"
MODEL_BOOK = "model"
MODEL_CASH = 100_000.0
TOP_FRACTION = 0.10          # long-only top decile of the ranked universe
INVEST_FRACTION = 0.98       # keep a small cash buffer
MAX_RANK_AGE_DAYS = 5        # refuse to trade stale ranks


def _latest_ranks(conn_feat) -> tuple[str, list[str]]:
    """(as_of, [tickers ranked 1..N]) from the nightly GBM scorer."""
    as_of = conn_feat.execute("SELECT MAX(as_of) FROM ml_scores").fetchone()[0]
    if not as_of:
        raise RuntimeError("ml_scores is empty")
    rows = conn_feat.execute(
        "SELECT ticker FROM ml_scores WHERE as_of=? ORDER BY rank", (as_of,)).fetchall()
    return as_of, [r[0].upper() for r in rows]


def _adv_dollars(sym: str) -> float | None:
    """Trailing-21d average daily dollar volume (for the spread/participation model)."""
    try:
        bars = alpaca.daily_bars(sym, days=30)[-21:]
        if not bars:
            return None
        return sum(float(b["c"]) * float(b.get("v") or 0) for b in bars) / len(bars)
    except Exception:
        return None


def _fill_price(px: float, side: str, adv_dollars: float | None) -> float:
    """Cost-realistic sim fill: cross half the modeled spread. Thin names cost more."""
    adv_m = (adv_dollars or 0) / 1e6
    half_spread_bps = max(1.0, SPREAD_K / (adv_m ** 0.5)) if adv_m > 0 else 25.0
    adj = half_spread_bps / 1e4
    return px * (1 + adj) if side == "buy" else px * (1 - adj)


def fill_price(symbol: str, side: str, ref_price: float) -> float:
    """Public fill model for the broker adapter (D52 desk cutover)."""
    return round(_fill_price(float(ref_price), side, _adv_dollars(symbol)), 4)


def _last_model_rebalance(conn) -> str | None:
    r = conn.execute(
        "SELECT MAX(filled_at) FROM sim_orders WHERE book=? AND source='model-rebalance'",
        (MODEL_BOOK,)).fetchone()
    return r[0][:10] if r and r[0] else None


def rebalance_model_book(conn, *, force: bool = False) -> dict:
    """Monthly, long-only, equal-weight top decile of the GBM ranks — the live
    forward track record for the ML ranker. Runs from nightly; only trades on
    the first nightly run of a new calendar month (or --force)."""
    import sqlite3 as _sq
    ensure_book(conn, MODEL_BOOK, MODEL_CASH)
    today = _iso_today()
    last = _last_model_rebalance(conn)
    if not force and last and last[:7] == today[:7]:
        return {"rebalanced": False, "reason": f"already rebalanced this month ({last})"}

    feat = _sq.connect(f"file:{FEAT_DB}?mode=ro", uri=True)
    as_of, ranked = _latest_ranks(feat)
    feat.close()
    age = (date.fromisoformat(today) - date.fromisoformat(as_of)).days
    if age > MAX_RANK_AGE_DAYS:
        return {"rebalanced": False, "reason": f"ranks stale: as_of {as_of} ({age}d old) — refusing"}

    n_top = max(1, int(len(ranked) * TOP_FRACTION))
    targets = ranked[:n_top]

    held = positions(conn, MODEL_BOOK)
    # marks + ADV for everything we must touch (current holdings ∪ targets)
    marks: dict[str, float] = {}
    advs: dict[str, float | None] = {}
    untradeable = []
    for sym in sorted(set(held) | set(targets)):
        px = _mark_price(sym)
        if px is None or px <= 0:
            untradeable.append(sym)
            continue
        marks[sym] = px
        advs[sym] = _adv_dollars(sym)
    targets = [t for t in targets if t in marks]
    if not targets:
        raise RuntimeError("no tradeable targets — refusing to empty the model book")

    equity = get_cash(conn, MODEL_BOOK) + sum(
        p["qty"] * marks[s] for s, p in held.items() if s in marks)
    per_name = equity * INVEST_FRACTION / len(targets)

    sells, buys = [], []
    for sym, pos in held.items():
        if sym not in marks:
            continue  # unmarkable holding: hold rather than guess (flagged below)
        tgt_qty = (per_name / marks[sym]) if sym in targets else 0.0
        delta = tgt_qty - pos["qty"]
        (sells if delta < 0 else buys).append((sym, delta))
    for sym in targets:
        if sym not in held:
            buys.append((sym, per_name / marks[sym]))

    n_trades = 0
    for batch, side in ((sells, "sell"), (buys, "buy")):
        for sym, delta in batch:
            qty = round(abs(delta), 4)
            if qty * marks[sym] < 1.0:      # ignore sub-$1 rebalance dust
                continue
            adv = advs.get(sym)
            if adv:
                cap_qty = PARTICIPATION_CAP * adv / marks[sym]
                qty = min(qty, round(cap_qty, 4))
            px = _fill_price(marks[sym], side, adv)
            apply_fill(conn, MODEL_BOOK, sym, side, qty, px, source="model-rebalance")
            n_trades += 1

    audit(conn, actor="executor", entity_type="sim_account", entity_id=MODEL_BOOK,
          action="model_rebalance",
          rationale=f"ranks as_of {as_of}: top {len(targets)} equal-weight, "
                    f"{n_trades} fills, equity {equity:.2f}")
    conn.commit()
    return {"rebalanced": True, "as_of_ranks": as_of, "names": len(targets),
            "trades": n_trades, "equity": round(equity, 2),
            "untradeable_skipped": untradeable}


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
    _ensure_cash_yield_tables(conn)
    cy = _apply_cash_yield_once_per_day(conn, book)
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
    # intraday sample for the 1D/1W chart (D53); 14-day retention
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    conn.execute("INSERT OR REPLACE INTO book_equity_intraday (book, ts, equity) VALUES (?,?,?)",
                 (book, now_ms, equity))
    conn.execute("DELETE FROM book_equity_intraday WHERE book=? AND ts < ?",
                 (book, now_ms - 14 * 86400_000))
    attribution = _write_return_attribution(conn, book, equity, cy.get("credit", 0.0))
    conn.commit()
    return {
        "book": book,
        "date": _iso_today(),
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "cash_yield": {
            "annual_yield": round(float(cy.get("annual_yield") or 0.0), 6),
            "credit": round(float(cy.get("credit") or 0.0), 4),
            "already_applied": bool(cy.get("already_applied")),
        },
        "attribution": attribution,
    }


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
    p = sub.add_parser("rebalance-model"); p.add_argument("--force", action="store_true")
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
    elif a.cmd == "rebalance-model":
        print(json.dumps(rebalance_model_book(conn, force=a.force), indent=2))
    elif a.cmd == "nightly":
        # D52 cutover: the desk book IS the broker. Nightly = corporate
        # actions + EOD marks per live book + model rebalance. Alpaca
        # mirror/parity only while the legacy backend is active.
        import broker as _broker
        out = {"backend": _broker.backend()}
        books_ok = True
        try:
            out["desk_corporate_actions"] = apply_corporate_actions(conn, "desk")
            out["desk_mark"] = mark_book(conn, "desk")
        except Exception as exc:
            books_ok = False
            out["desk_error"] = str(exc)[:300]
        model_ok = True
        try:
            out["model_corporate_actions"] = apply_corporate_actions(conn, MODEL_BOOK)
            out["model_rebalance"] = rebalance_model_book(conn)
            out["model_mark"] = mark_book(conn, MODEL_BOOK)
        except Exception as exc:
            model_ok = False
            out["model_error"] = str(exc)[:300]
        if _broker.backend() == "alpaca":
            out["mirrored"] = mirror_desk_fills(conn)
            out["shadow_corporate_actions"] = apply_corporate_actions(conn, "shadow")
            out["shadow_mark"] = mark_book(conn, "shadow")
            out["parity"] = parity(conn)
        else:
            eq = (out.get("desk_mark") or {}).get("equity")
            out["parity"] = {"ok": bool(eq and eq > 0) and books_ok, "mode": "internal-ledger"}
        print(json.dumps(out, indent=2))
        return 0 if (out["parity"]["ok"] and model_ok) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
