"""Shared HTTP utilities + raw-response cache for replay."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

CACHE_ROOT = Path(
    os.environ.get(
        "DRUCK_PHASE2_CACHE",
        str(Path.home() / ".openclaw/workspaces/druck/phase2_cache"),
    )
)
RAW_ROOT = CACHE_ROOT / "raw"


def _safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


def cache_path(source: str, ticker: str, endpoint: str, date: Optional[str] = None) -> Path:
    d = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    p = RAW_ROOT / source / d
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{_safe_name(ticker)}.{_safe_name(endpoint)}.json"


def write_cache(source: str, ticker: str, endpoint: str, payload: Any, date: Optional[str] = None) -> Path:
    p = cache_path(source, ticker, endpoint, date)
    p.write_text(json.dumps(payload, indent=2, default=str))
    return p


def read_cache(source: str, ticker: str, endpoint: str, date: Optional[str] = None) -> Optional[Any]:
    p = cache_path(source, ticker, endpoint, date)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def read_latest_cache(source: str, ticker: str, endpoint: str) -> Optional[Any]:
    pattern = f"{_safe_name(ticker)}.{_safe_name(endpoint)}.json"
    root = RAW_ROOT / source
    if not root.exists():
        return None
    candidates = sorted(root.glob(f"*/{pattern}"), reverse=True)
    for path in candidates:
        try:
            return json.loads(path.read_text())
        except Exception:
            continue
    return None


def http_get_json(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    params: Optional[dict[str, Any]] = None,
    timeout: int = 20,
    retries: int = 2,
    backoff: float = 0.6,
) -> dict[str, Any]:
    """GET → JSON, with light retry on 429/5xx. Raises on terminal failure."""
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
    raise RuntimeError(f"http_get_json failed: {url} :: {last_exc}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
