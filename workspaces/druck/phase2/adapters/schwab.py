"""Schwab adapter — read-only access to cached real-book context.

Phase II uses Schwab strictly for portfolio-fit (sector / factor overlap).
We do NOT refresh OAuth tokens here; that lives in the schwab skill /
gateway. We just read whatever is on disk; if missing, return [].
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

POS_FILE = Path.home() / ".openclaw/workspaces/druck/schwab_positions.json"
ACCT_FILE = Path.home() / ".openclaw/workspaces/druck/schwab_account_raw.json"


def positions() -> list[dict]:
    if not POS_FILE.exists():
        return []
    try:
        data = json.loads(POS_FILE.read_text())
    except Exception:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # tolerate {"positions": [...]}, {"securitiesAccount": {"positions": [...]}}, etc.
        if "positions" in data and isinstance(data["positions"], list):
            return data["positions"]
        sa = data.get("securitiesAccount") or {}
        if isinstance(sa, dict) and isinstance(sa.get("positions"), list):
            return sa["positions"]
    return []


def account_summary() -> Optional[dict]:
    if not ACCT_FILE.exists():
        return None
    try:
        return json.loads(ACCT_FILE.read_text())
    except Exception:
        return None


def position_tickers() -> list[str]:
    out: list[str] = []
    for p in positions():
        # try multiple shapes (raw Schwab vs normalized)
        sym = (
            p.get("symbol")
            or p.get("ticker")
            or (p.get("instrument") or {}).get("symbol")
        )
        if sym:
            out.append(sym.upper())
    return sorted(set(out))
