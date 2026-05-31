#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()


"""Flatten all open Alpaca paper positions and clear the reconciliation pause.

Steps:
  1. Fetch all open Alpaca positions.
    2. Submit a positions-delete call to close everything at market.
  3. Clear the active exits_trims_only pause in the trading-intel DB.
  4. Write an audit row.

Run with --dry-run to see what would happen without submitting orders.

Usage:
  python3 flatten_and_reset.py [--dry-run]
"""


import argparse
import json
import os
from pathlib import Path
import sqlite3
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
ALPACA_CRED_PATH = Path(os.path.expanduser("~/.openclaw/credentials/alpaca-api.json"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_creds() -> tuple[str, str, str]:
    c = json.loads(ALPACA_CRED_PATH.read_text())
    base = c["endpoint"].rstrip("/")
    key = c["api key"]
    secret = c["secret"]
    return base, key, secret


def alpaca_request(method: str, path: str, base: str, key: str, secret: str) -> dict | list:
    url = base + path
    req = urllib.request.Request(url, method=method, headers={
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read()
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {body.decode()}") from e


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv[1:])

    base, key, secret = load_creds()

    print("fetching open positions...")
    positions = alpaca_request("GET", "/positions", base, key, secret)
    if not positions:
        print("no open positions — nothing to flatten")
    else:
        print(f"{len(positions)} positions to close:")
        for p in positions:
            print(f"  {p['symbol']:6}  qty={p['qty']:>8}  mv={p.get('market_value','?'):>12}  pnl={p.get('unrealized_pl','?'):>10}")

    if args.dry_run:
        print("\n[dry-run] would DELETE /positions (close all at market)")
    else:
        if positions:
            print("\nclosing all positions at market...")
            result = alpaca_request("DELETE", "/positions?cancel_orders=true", base, key, secret)
            if isinstance(result, list):
                ok = sum(1 for r in result if r.get("status") in ("accepted", 200, "200"))
                print(f"submitted {len(result)} close orders ({ok} accepted)")
            else:
                print(f"response: {result}")

    # clear DB pause
    if not DB_PATH.exists():
        print(f"\ndb not found: {DB_PATH} — skipping pause clear")
        return 0

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    pause = conn.execute(
        "SELECT id, scope FROM system_pauses WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    if not pause:
        print("\nno active pause in DB")
    elif args.dry_run:
        print(f"\n[dry-run] would clear pause {pause['id']} ({pause['scope']})")
    else:
        n = now_iso()
        conn.execute(
            "UPDATE system_pauses SET ended_at = ? WHERE id = ?",
            (n, pause["id"]),
        )
        conn.execute("""
            INSERT INTO audits
              (id, actor, action, entity_type, entity_id, after_state, timestamp, experiment_id)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            f"audit_{uuid.uuid4().hex}",
            "system",
            "system_pause_cleared",
            "system_pauses",
            pause["id"],
            json.dumps({"reason": "portfolio flattened, starting clean", "cleared_by": "flatten_and_reset.py"}),
            n,
            "flatten_and_reset_live",
        ))
        conn.commit()
        print(f"\npause {pause['id']} ({pause['scope']}) cleared")

    conn.close()
    print("\nDone. Run summary_report.py to verify clean state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
