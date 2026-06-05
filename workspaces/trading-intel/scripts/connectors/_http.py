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


def http_get(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    retries: int = 1,
) -> bytes:
    """GET with simple backoff. Raises ConnectorError on persistent failure."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(0.6 * (2 ** attempt))
    raise ConnectorError(f"http_get failed for {url}: {last_err}")


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
