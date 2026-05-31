#!/usr/bin/env python3
"""Reconcile legacy Alpaca paper positions into the trading-intel DB.

Fetches live Alpaca positions and offers two modes:
  --import   Write them into hypotheses + positions tables so the DB
             mirrors reality and the exits_trims_only pause can be lifted.
  --report   Just show what's in Alpaca vs the DB (default).

The import mode creates a placeholder hypothesis for each position so the
state machine has a valid hypothesis_id to reference. These should be
reviewed and replaced with real theses over time.

Usage:
  python3 reconcile_legacy_positions.py [--import] [--clear-pause]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
ALPACA_CRED_PATH = Path(os.path.expanduser("~/.openclaw/credentials/alpaca-api.json"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_alpaca_creds() -> dict:
    return json.loads(ALPACA_CRED_PATH.read_text())


def alpaca_get(path: str, base_url: str, key: str, secret: str) -> dict | list:
    import urllib.request
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def fetch_alpaca_positions(creds: dict) -> list[dict]:
    base_url = (creds.get("endpoint") or creds.get("base_url") or
                creds.get("APCA_API_BASE_URL") or "https://paper-api.alpaca.markets")
    key = (creds.get("api key") or creds.get("key") or
           creds.get("APCA_API_KEY_ID") or creds.get("api_key"))
    secret = (creds.get("secret") or creds.get("APCA_API_SECRET_KEY") or
              creds.get("secret_key"))
    return alpaca_get("/positions", base_url, key, secret)


def get_db_positions(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT ticker FROM positions WHERE state NOT IN ('closed','cancelled')").fetchall()
    return [r[0] for r in rows]


def get_active_pause(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT id, scope, reason FROM system_pauses WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def import_position(conn: sqlite3.Connection, pos: dict) -> str:
    hypo_id = f"hypo_legacy_{pos['symbol'].lower()}_{uuid.uuid4().hex[:8]}"
    pos_id = f"pos_legacy_{pos['symbol'].lower()}_{uuid.uuid4().hex[:8]}"
    qty = float(pos.get("qty", 0))
    market_value = float(pos.get("market_value", 0))
    unrealized_pl = float(pos.get("unrealized_pl", 0))
    side = pos.get("side", "long")
    direction = "long" if side == "long" else "short"
    n = now_iso()

    conn.execute("""
        INSERT OR IGNORE INTO hypotheses
          (id, state, thesis_summary, tickers, direction, created_by, created_at, updated_at, experiment_id)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        hypo_id,
        "active",
        f"Legacy position imported from Alpaca paper — {pos['symbol']}. Requires real thesis.",
        pos["symbol"],
        direction,
        "system",
        n,
        n,
        "reconcile_legacy_import_v1",
    ))

    conn.execute("""
        INSERT OR IGNORE INTO positions
          (id, hypothesis_id, ticker, qty, direction, state,
           current_value, pnl_slippage_adjusted, created_at, updated_at, experiment_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        pos_id,
        hypo_id,
        pos["symbol"],
        qty,
        direction,
        "open",
        market_value,
        unrealized_pl,
        n,
        n,
        "reconcile_legacy_import_v1",
    ))

    conn.execute("""
        INSERT INTO audits
          (id, actor, action, entity_type, entity_id, detail_json, created_at, experiment_id)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        f"audit_{uuid.uuid4().hex}",
        "system",
        "legacy_position_imported",
        "positions",
        pos_id,
        json.dumps({"symbol": pos["symbol"], "qty": qty, "market_value": market_value}),
        n,
        "reconcile_legacy_import_v1",
    ))

    return pos_id


def clear_pause(conn: sqlite3.Connection, pause_id: str) -> None:
    conn.execute(
        "UPDATE system_pauses SET ended_at = ?, reason = reason || ' [cleared by reconcile_legacy_positions.py]' WHERE id = ?",
        (now_iso(), pause_id),
    )
    conn.execute("""
        INSERT INTO audits
          (id, actor, action, entity_type, entity_id, detail_json, created_at, experiment_id)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        f"audit_{uuid.uuid4().hex}",
        "system",
        "system_pause_cleared",
        "system_pauses",
        pause_id,
        json.dumps({"reason": "legacy positions imported, divergence resolved"}),
        now_iso(),
        "reconcile_legacy_import_v1",
    ))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--import", dest="do_import", action="store_true")
    ap.add_argument("--clear-pause", action="store_true")
    args = ap.parse_args(argv[1:])

    if not DB_PATH.exists():
        print(f"db not found: {DB_PATH}", file=sys.stderr)
        return 1

    print("fetching Alpaca positions...")
    try:
        creds = load_alpaca_creds()
        alpaca_positions = fetch_alpaca_positions(creds)
    except Exception as exc:
        print(f"alpaca fetch failed: {exc}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    db_tickers = get_db_positions(conn)
    active_pause = get_active_pause(conn)

    print(f"\nAlpaca live positions: {len(alpaca_positions)}")
    print(f"DB tracked positions: {len(db_tickers)}")
    print(f"Active pause: {active_pause['scope'] if active_pause else 'none'}")
    print()

    missing = [p for p in alpaca_positions if p["symbol"] not in db_tickers]
    print(f"Positions in Alpaca not in DB ({len(missing)}):")
    for p in missing:
        print(f"  {p['symbol']:6} qty={p['qty']:>10} mv={p.get('market_value','?'):>12} pnl={p.get('unrealized_pl','?'):>10}")

    if not args.do_import:
        print("\nRun with --import to write these into the DB as legacy positions.")
        print("Run with --import --clear-pause to also lift the exits_trims_only pause.")
        conn.close()
        return 0

    print("\nImporting legacy positions...")
    for pos in missing:
        pid = import_position(conn, pos)
        print(f"  imported: {pos['symbol']} -> {pid}")

    if args.clear_pause and active_pause:
        clear_pause(conn, active_pause["id"])
        print(f"\nPause {active_pause['id']} cleared.")
    elif active_pause and not args.clear_pause:
        print(f"\nNote: pause {active_pause['scope']} still active. Run with --clear-pause to lift it.")

    conn.commit()
    conn.close()
    print("\nDone. Run summary_report.py to verify.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
