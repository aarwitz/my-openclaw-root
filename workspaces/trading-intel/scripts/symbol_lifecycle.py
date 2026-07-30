#!/usr/bin/env python3
"""Point-in-time symbol aliases and live-feature freshness checks."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config" / "symbol_aliases.json"
MAX_LIVE_FEATURE_AGE_DAYS = 7


@lru_cache(maxsize=1)
def aliases() -> tuple[dict, ...]:
    payload = json.loads(CONFIG.read_text())
    rows = []
    for item in payload.get("aliases", []):
        old = str(item.get("old") or "").upper()
        new = str(item.get("new") or "").upper()
        effective = str(item.get("effective_date") or "")
        if old and new and effective:
            rows.append({**item, "old": old, "new": new,
                         "effective_date": effective})
    return tuple(rows)


def canonical_symbol(symbol: str, as_of: str | None = None) -> str:
    """Return the symbol valid on ``as_of``; default is the live symbol."""
    current = str(symbol or "").strip().upper()
    when = as_of[:10] if as_of else datetime.now(timezone.utc).date().isoformat()
    for _ in range(8):
        replacement = next(
            (row["new"] for row in aliases()
             if row["old"] == current and when >= row["effective_date"]),
            None,
        )
        if not replacement or replacement == current:
            break
        current = replacement
    return current


def is_live_feature_fresh(
    as_of: str | None,
    *,
    today: date | None = None,
    max_age_days: int = MAX_LIVE_FEATURE_AGE_DAYS,
) -> bool:
    if not as_of:
        return False
    try:
        observed = date.fromisoformat(str(as_of)[:10])
    except ValueError:
        return False
    today = today or datetime.now(timezone.utc).date()
    age = (today - observed).days
    return 0 <= age <= max_age_days
