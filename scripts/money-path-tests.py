#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()

"""Money-path CI (D57): deterministic tests over the math that IS the brokerage.

Since D52 the internal ledger is the account — a silent bug in fill/cash/basis
math corrupts the book directly. These tests run in the nightly learning chain
(failure pages via the chain alert) and are safe anywhere: they build a scratch
DB from sql/schema.sql and monkeypatch all network I/O.
"""

import os
import sqlite3
import tempfile

OC = os.path.expanduser("~/.openclaw")
sys.path.insert(0, f"{OC}/workspaces/executor/scripts")
sys.path.insert(0, f"{OC}/workspaces/trading-intel/scripts")

os.environ.setdefault("OPENCLAW_RUN_WITH_TRACE", "1")

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(f"{name}: {detail}")


def scratch_db():
    path = tempfile.mktemp(suffix=".sqlite", dir="/tmp")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(open(f"{OC}/workspaces/trading-intel/sql/schema.sql").read())
    conn.commit()
    return conn, path


# ---------------------------------------------------------------- 1. fill math
def test_fill_price():
    import sim_broker as sb
    orig = sb._adv_dollars
    try:
        sb._adv_dollars = lambda sym: 50_000_000 if sym == "DEEP" else None
        buy = sb.fill_price("DEEP", "buy", 100.0)
        sell = sb.fill_price("DEEP", "sell", 100.0)
        check("buy fills above ref", buy > 100.0, f"buy={buy}")
        check("sell fills below ref", sell < 100.0, f"sell={sell}")
        # ADV $50M -> half-spread = max(1, 8/sqrt(50)) ≈ 1.13bps
        check("deep-name spread ≈1.1bps", abs(buy - 100.0113) < 0.002, f"buy={buy}")
        thin = sb.fill_price("THIN", "buy", 100.0)
        check("no-ADV name pays 25bps", abs(thin - 100.25) < 0.001, f"thin={thin}")
    finally:
        sb._adv_dollars = orig


# ------------------------------------------------- 2. ledger conservation
def test_apply_fill_ledger():
    import sim_broker as sb
    conn, path = scratch_db()
    try:
        sb.ensure_book(conn, "t", cash=10_000.0)
        sb.apply_fill(conn, "t", "XYZ", "buy", 10, 100.0, order_id="o1", source="test")
        cash = sb.get_cash(conn, "t")
        check("buy debits cash exactly", abs(cash - 9_000.0) < 1e-9, f"cash={cash}")
        pos = sb.positions(conn, "t")["XYZ"]
        check("position qty", pos["qty"] == 10, str(pos))
        check("basis = fill", abs(pos["cost_basis"] - 100.0) < 1e-9, str(pos))
        sb.apply_fill(conn, "t", "XYZ", "buy", 10, 110.0, order_id="o2", source="test")
        pos = sb.positions(conn, "t")["XYZ"]
        check("adds average basis", abs(pos["cost_basis"] - 105.0) < 1e-9, f"basis={pos['cost_basis']}")
        sb.apply_fill(conn, "t", "XYZ", "sell", 5, 120.0, order_id="o3", source="test")
        cash = sb.get_cash(conn, "t")
        check("sell credits cash", abs(cash - (10_000 - 1000 - 1100 + 600)) < 1e-9, f"cash={cash}")
        pos = sb.positions(conn, "t")["XYZ"]
        check("sell reduces qty not basis", pos["qty"] == 15 and abs(pos["cost_basis"] - 105.0) < 1e-9, str(pos))
        check("orders ledger complete", conn.execute(
            "SELECT COUNT(*) FROM sim_orders WHERE book='t'").fetchone()[0] == 3)
    finally:
        conn.close()
        os.unlink(path)


# ------------------------------------------------- 3. stop breach detection
def test_stop_breach():
    import enforce_stops as es
    conn, path = scratch_db()
    try:
        conn.execute("INSERT INTO hypotheses (id, created_at, created_by, tickers, thesis_summary, state) "
                     "VALUES ('h1','2026-01-01T00:00:00Z','trader','[\"AAA\"]','test thesis','active')")
        conn.execute("INSERT INTO positions (id, hypothesis_id, ticker, vehicle, qty, cost_basis, state, opened_at) "
                     "VALUES ('p1','h1','AAA','direct_equity',10,100.0,'open','2026-01-01T00:00:00Z')")
        conn.commit()
        orig_db, orig_lt, orig_bars = es.DB_PATH, es.latest_trade, es.daily_bars
        try:
            es.DB_PATH = path
            es.daily_bars = lambda *a, **k: []          # momentum check fails closed -> hard exit
            es.latest_trade = lambda t: {"price": 91.9}  # -8.1% — breach
            import io, contextlib, json as _json
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                es.main(["--dry-run"])
            out = _json.loads(buf.getvalue())
            check("-8.1% flags breach", len(out["stop_breaches"]) == 1, buf.getvalue()[:120])
            es.latest_trade = lambda t: {"price": 92.1}  # -7.9% — no breach
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                es.main(["--dry-run"])
            out = _json.loads(buf.getvalue())
            check("-7.9% does not flag", len(out["stop_breaches"]) == 0, buf.getvalue()[:120])
        finally:
            es.DB_PATH, es.latest_trade, es.daily_bars = orig_db, orig_lt, orig_bars
    finally:
        conn.close()
        os.unlink(path)


# ------------------------------------------------- 4. reconcile proceeds guard
def test_proceeds_guard():
    import reconcile as rc
    conn, path = scratch_db()
    try:
        conn.execute("INSERT INTO hypotheses (id, created_at, created_by, tickers, thesis_summary, state) "
                     "VALUES ('h1','2026-01-01T00:00:00Z','trader','[\"A\"]','t','active')")
        for i, t in enumerate(("AAA", "BBB", "CCC", "DDD")):
            conn.execute("INSERT INTO positions (id, hypothesis_id, ticker, vehicle, qty, cost_basis, state, opened_at) "
                         f"VALUES ('p{i}','h1','{t}','direct_equity',10,100.0,'open','2026-01-01T00:00:00Z')")
        conn.commit()
        orig_lp, orig_lo = rc.list_positions, rc.list_orders
        try:
            rc.list_positions = lambda: []                       # broker claims: nothing
            rc.list_orders = lambda status="open", limit=100: []  # ...and no sells to explain it
            res = rc.compute_divergences(conn)
            check("mass-vanish + no proceeds => suspect", res.get("broker_data_suspect") is True,
                  str(res.get("suspect_reason"))[:100])
            repairs = rc.apply_repairs(conn, res)
            check("suspect => all repairs refused",
                  repairs["repaired"] == [] and repairs["unresolved"][0]["type"] == "broker_data_suspect",
                  str(repairs)[:120])
            rc.list_orders = lambda status="open", limit=100: (
                [] if status == "open" else
                [{"symbol": t, "side": "sell", "status": "filled"} for t in ("AAA", "BBB", "CCC", "DDD")])
            res = rc.compute_divergences(conn)
            check("vanish WITH sell proceeds => trusted", res.get("broker_data_suspect") is False)
        finally:
            rc.list_positions, rc.list_orders = orig_lp, orig_lo
    finally:
        conn.close()
        os.unlink(path)


# ------------------------------------------------- 5. risk cap sanity
def test_risk_caps():
    import gate_risk_intents as g
    sys.path.insert(0, f"{OC}/workspaces/risk/scripts")
    per_name = getattr(g, "MAX_NAME_PCT", None) or getattr(g, "PER_NAME_CAP", None)
    gross = getattr(g, "MAX_GROSS_PCT", None) or getattr(g, "GROSS_CAP", None)
    names = getattr(g, "MAX_POSITIONS", None)
    check("caps present", all(v is not None for v in (per_name, gross, names)),
          f"per_name={per_name} gross={gross} names={names}")
    if per_name and gross:
        pn = per_name if per_name < 1 else per_name / 100.0
        gr = gross if gross < 1 else gross / 100.0
        check("per-name < gross", pn < gr, f"{pn} vs {gr}")
        check("cap arithmetic sane", 0 < pn <= 0.25 and 0 < gr <= 1.0 and names >= 1)


if __name__ == "__main__":
    sys.path.insert(0, f"{OC}/workspaces/risk/scripts")
    sys.path.insert(0, f"{OC}/workspaces/trader/scripts")
    for t in (test_fill_price, test_apply_fill_ledger, test_stop_breach, test_proceeds_guard, test_risk_caps):
        print(f"== {t.__name__}")
        try:
            t()
        except Exception as e:
            FAILURES.append(f"{t.__name__}: crashed {type(e).__name__}: {e}")
            print(f"  [FAIL] crashed: {e}")
    print(f"\n{'PASS' if not FAILURES else 'FAIL'}: {len(FAILURES)} failure(s)")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1 if FAILURES else 0)
