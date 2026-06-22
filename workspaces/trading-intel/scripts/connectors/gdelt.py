#!/usr/bin/env python3
"""GDELT 2.0 connector — FREE historical news tone/volume (back to 2017+).

The piece that unblocks *historical* news backtesting (Phase C): Event Registry has no archive on
our tier, but GDELT's DOC API exposes daily average tone + article volume for any query, for free.
Noisy + keyword-based (so entity precision is lower than Event Registry), but it makes news-sentiment
mechanisms backtestable. Heavily rate-limited → long cache + backoff. stdlib only.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from ._http import ConnectorError, cache_read, cache_write

DOC = "https://api.gdeltproject.org/api/v2/doc/doc"


def _fetch(params, retries=3):
    url = DOC + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=30.0) as r:
                return json.loads(r.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429 and attempt < retries:
                time.sleep(5.0 * (attempt + 1))          # GDELT throttles hard — back off seconds
                continue
            if attempt < retries:
                time.sleep(2.0)
                continue
            break
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(2.0)
                continue
            break
    raise ConnectorError(f"gdelt fetch failed: {str(last)[:120]}")


def _ymd(d: str) -> str:
    return d.replace("-", "") + "000000"


def historical_tone(query: str, start: str, end: str, cache_h: float = 720.0) -> list[dict]:
    """Daily series for `query` between start/end (YYYY-MM-DD). Returns
    [{date, tone, volume}] — tone = avg article tone (>0 positive), volume = normalized coverage.
    Historical + free → backtestable, point-in-time (each day's value uses only that day's news)."""
    ck = "gdelt_" + urllib.parse.quote_plus(query)[:40] + f"_{start}_{end}"
    hit = cache_read(ck, max_age_h=cache_h)
    if hit is not None and "data" in hit:
        return hit["data"]
    base = {"query": query + " sourcelang:eng", "startdatetime": _ymd(start),
            "enddatetime": _ymd(end), "format": "json"}
    tone = _fetch({**base, "mode": "timelinetone"})
    vol = _fetch({**base, "mode": "timelinevolinfo"})
    tone_pts = (tone.get("timeline") or [{}])[0].get("data", []) if tone.get("timeline") else []
    vol_map = {}
    if vol.get("timeline"):
        for p in vol["timeline"][0].get("data", []):
            vol_map[(p.get("date") or "")[:10].replace("-", "")] = p.get("value")
    out = []
    for p in tone_pts:
        dk = (p.get("date") or "")[:8]
        iso = f"{dk[:4]}-{dk[4:6]}-{dk[6:8]}" if len(dk) >= 8 else p.get("date")
        out.append({"date": iso, "tone": p.get("value"), "volume": vol_map.get(dk)})
    cache_write(ck, {"data": out})
    return out
