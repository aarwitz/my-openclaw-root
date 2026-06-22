"""Common helpers for connectors.

stdlib only (urllib + json + csv) so cron jobs do not depend on third-party
packages and so failures are easy to diagnose.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Force a global socket timeout. urllib.request.urlopen's `timeout` arg covers
# socket ops but on some hosts the TCP connect can hang in SYN_SENT for many
# minutes despite the timeout param. Setting the default socket timeout caps it.
socket.setdefaulttimeout(10.0)

CACHE_DIR = Path(os.path.expanduser("~/.openclaw/state/market-data-cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class ConnectorError(RuntimeError):
    """Raised when a connector cannot return a usable value."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


RATE_LIMIT_CODES = {429, 502, 503, 504}   # rate-limit / transient-server -> back off LONGER and retry


def _request(req: "urllib.request.Request", timeout: float, retries: int) -> bytes:
    """Issue a request with rate-limit-aware backoff. On 429/5xx, honor the `Retry-After` header if
    present, else exponential backoff capped at 30s (so a burst self-heals instead of failing). Other
    transient errors get a short backoff. Raises ConnectorError after exhausting retries."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_err = exc
            if attempt >= retries:
                break
            if exc.code in RATE_LIMIT_CODES:
                ra = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    wait = float(ra) if ra else None
                except (TypeError, ValueError):
                    wait = None
                time.sleep(min(30.0, wait if wait is not None else 2.0 * (2 ** attempt)))  # 2,4,8,16,30
            else:
                time.sleep(0.6 * (2 ** attempt))
        except (urllib.error.URLError, TimeoutError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(0.6 * (2 ** attempt))
    raise ConnectorError(f"http request failed for {getattr(req, 'full_url', '?')}: {last_err}")


def http_get(url: str, headers: dict[str, str] | None = None, timeout: float = 10.0,
             retries: int = 3) -> bytes:
    """GET with 429/Retry-After-aware backoff. Raises ConnectorError on persistent failure."""
    return _request(urllib.request.Request(url, headers=headers or {}), timeout, retries)


def http_post(url: str, payload: Any, headers: dict[str, str] | None = None, timeout: float = 25.0,
              retries: int = 3) -> bytes:
    """POST JSON with the same 429/Retry-After-aware backoff."""
    body = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode()
    h = {"content-type": "application/json", **(headers or {})}
    return _request(urllib.request.Request(url, data=body, headers=h, method="POST"), timeout, retries)


def cache_write(key: str, payload: dict[str, Any]) -> None:
    path = CACHE_DIR / f"{key}.json"
    path.write_text(json.dumps({"cached_at": now_iso(), **payload}))


def cache_read(key: str, max_age_h: float = 6.0) -> dict[str, Any] | None:
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    cached_at = data.get("cached_at")
    if not cached_at:
        return None
    try:
        dt = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    if age_h > max_age_h:
        return None
    return data
