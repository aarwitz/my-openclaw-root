"""Lightweight intraday cache layer (Priority 7).

Stores expensive results (market scans, sector leaders, benchmark stats) on
disk with TTL. Two access patterns:

    get_or_compute(key, ttl_sec, fn)   # on-demand: serves cached if fresh
    refresh(key, fn)                   # scheduled: always recompute & store

Storage:
    ~/.openclaw/workspaces/druck/phase2_cache/intraday/<key>.json
    {timestamp, ttl_sec, payload}

Refresh cadences (Druck spec, market hours):
    movers          5 min
    sector_leaders  5 min
    catalyst_news   10 min
    benchmark_stats 1 min
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


CACHE_DIR = Path(
    os.environ.get(
        "DRUCK_PHASE2_CACHE",
        str(Path.home() / ".openclaw/workspaces/druck/phase2_cache"),
    )
) / "intraday"


REFRESH_CADENCE_SEC = {
    "movers":           300,
    "sector_leaders":   300,
    "catalyst_news":    600,
    "benchmark_stats":   60,
    "market_scan":      300,
}


def _path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
    return CACHE_DIR / f"{safe}.json"


def _now() -> float:
    return time.time()


def get(key: str) -> Optional[Any]:
    """Return cached payload if fresh (within TTL). Else None."""
    p = _path(key)
    if not p.exists():
        return None
    try:
        rec = json.loads(p.read_text())
    except Exception:
        return None
    age = _now() - rec.get("timestamp", 0)
    if age > rec.get("ttl_sec", 0):
        return None
    return rec.get("payload")


def put(key: str, payload: Any, ttl_sec: int) -> None:
    p = _path(key)
    rec = {
        "timestamp": _now(),
        "iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ttl_sec": ttl_sec,
        "payload": payload,
    }
    p.write_text(json.dumps(rec, default=str))


def get_or_compute(key: str, ttl_sec: int, fn: Callable[[], Any]) -> Any:
    """Return cached if fresh, else call fn() and cache."""
    cached = get(key)
    if cached is not None:
        return cached
    payload = fn()
    put(key, payload, ttl_sec)
    return payload


def refresh(key: str, fn: Callable[[], Any], ttl_sec: Optional[int] = None) -> Any:
    """Force recompute and store. Used by scheduled jobs (cron)."""
    payload = fn()
    ttl = ttl_sec if ttl_sec is not None else REFRESH_CADENCE_SEC.get(key, 300)
    put(key, payload, ttl)
    return payload


def info(key: str) -> Optional[dict]:
    """Inspection helper — return cache record metadata without payload."""
    p = _path(key)
    if not p.exists():
        return None
    try:
        rec = json.loads(p.read_text())
    except Exception:
        return None
    return {
        "key": key,
        "iso": rec.get("iso"),
        "age_sec": round(_now() - rec.get("timestamp", 0)),
        "ttl_sec": rec.get("ttl_sec"),
        "fresh": (_now() - rec.get("timestamp", 0)) <= rec.get("ttl_sec", 0),
        "size_bytes": p.stat().st_size,
    }


def list_all() -> list[dict]:
    if not CACHE_DIR.exists():
        return []
    out = []
    for p in CACHE_DIR.glob("*.json"):
        i = info(p.stem)
        if i:
            out.append(i)
    return out
