#!/usr/bin/env python3
"""X (Twitter) connector — cashtag mention-VOLUME from the full-archive counts API.

A backtestable social signal: daily tweet volume per ticker ($CASHTAG) back to 2006, from
`/2/tweets/counts/all` (requires full-archive access — confirmed on this key). Counts (not tweet
bodies) keep it cheap. The signal is the ABNORMAL-SPIKE z-score of mention volume (attention
spikes accompany/precede moves); featurized point-in-time (count for day D is knowable at EOD D).

stdlib + the shared _http cache. Rate-limited (Pro ~300 req/15min) → paginates with a courtesy sleep.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse

from ._http import ConnectorError, cache_read, cache_write, http_get

_CRED = os.path.expanduser("~/.openclaw/credentials/x-api.json")
_BEARER: str | None = None
COUNTS_ALL = "https://api.twitter.com/2/tweets/counts/all"


def _bearer() -> str:
    global _BEARER
    if _BEARER is None:
        with open(_CRED) as f:
            _BEARER = json.load(f)["bearer_token"]
    return _BEARER


def daily_mention_counts(cashtag: str, start: str, end: str, use_cache: bool = True) -> list[tuple[str, int]]:
    """Daily tweet counts for `$cashtag` between start/end (YYYY-MM-DD). Sorted [(date, count)].
    Full-archive, paginated, cached 24h per (cashtag, start, end)."""
    sym = cashtag.lstrip("$").upper()
    ck = f"x_mentions_{sym}_{start}_{end}"
    if use_cache:
        c = cache_read(ck, max_age_h=24.0)
        if c:
            return [(d, v) for d, v in c.get("series", [])]
    query = f"${sym} lang:en"
    out: dict[str, int] = {}
    next_token = None
    for _ in range(200):  # pagination safety cap
        params = {"query": query, "granularity": "day",
                  "start_time": f"{start}T00:00:00Z", "end_time": f"{end}T00:00:00Z"}
        if next_token:
            params["next_token"] = next_token
        url = COUNTS_ALL + "?" + urllib.parse.urlencode(params)
        raw = http_get(url, headers={"Authorization": f"Bearer {_bearer()}"}, timeout=30.0, retries=2)
        data = json.loads(raw)
        for b in data.get("data", []):
            out[b["start"][:10]] = int(b["tweet_count"])
        next_token = (data.get("meta") or {}).get("next_token")
        if not next_token:
            break
        time.sleep(3.1)  # courtesy pacing for the 15-min window
    if not out:
        raise ConnectorError(f"x: no counts for ${sym} {start}..{end}")
    series = sorted(out.items())
    cache_write(ck, {"series": series, "cashtag": sym})
    return series
