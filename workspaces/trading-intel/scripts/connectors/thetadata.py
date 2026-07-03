#!/usr/bin/env python3
"""ThetaData connector — talks to the LOCAL Theta Terminal (v2 REST, port 25510).

The terminal is a vendor Java process that authenticates upstream and proxies
requests: ~/.openclaw/tools/thetadata/ (jre/ + ThetaTerminalv3.jar + creds.txt).
Start it with `ensure_terminal()`; nothing here talks to thetadata.us directly.

FREE tier (audition, 2026-07-03): 1yr EOD history, 20 req/min. Request sizing:
bulk_hist with exp=0 (all contracts of a root) fits ~1 week per request.

Point-in-time: EOD options data for date D is knowable the evening of D.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:25510"
TOOLS = Path(os.path.expanduser("~/.openclaw/tools/thetadata"))
# Free tier: 20 req/min. Keep a margin so the terminal never queues upstream.
MIN_INTERVAL_S = 3.2
_last_req = 0.0


class ThetaError(RuntimeError):
    pass


def terminal_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/v2/list/roots/option", timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_terminal(wait_s: int = 90) -> None:
    if terminal_up():
        return
    jar, jre, creds = TOOLS / "ThetaTerminalv3.jar", TOOLS / "jre/bin/java", TOOLS / "creds.txt"
    for p in (jar, jre, creds):
        if not p.exists():
            raise ThetaError(f"theta terminal component missing: {p}")
    subprocess.Popen([str(jre), "-jar", str(jar), "--creds-file", str(creds)],
                     cwd=str(TOOLS), stdout=open(TOOLS / "terminal.log", "ab"),
                     stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if terminal_up():
            return
        time.sleep(3)
    raise ThetaError("theta terminal did not become ready")


def _get(path: str, params: dict) -> dict:
    global _last_req
    wait = MIN_INTERVAL_S - (time.time() - _last_req)
    if wait > 0:
        time.sleep(wait)
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE}{path}?{qs}"
    _last_req = time.time()
    with urllib.request.urlopen(url, timeout=600) as r:
        raw = r.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        raise ThetaError(f"non-json from {path}: {raw[:150]!r}")


def bulk_eod(root: str, start_date: str, end_date: str) -> list[dict]:
    """All contracts' EOD rows for `root` in [start,end] (YYYYMMDD). ~1 week max per call.
    Returns [{contract:{root,expiration,strike,right}, ticks:[[...cols...]]}]."""
    d = _get("/v2/bulk_hist/option/eod",
             {"root": root, "exp": 0, "start_date": start_date, "end_date": end_date})
    if str(d.get("header", {}).get("next_page", "null")) not in ("null", "None", ""):
        raise ThetaError(f"unexpected pagination for {root} {start_date}..{end_date}")
    return d.get("response", [])


def bulk_open_interest(root: str, start_date: str, end_date: str) -> list[dict]:
    d = _get("/v2/bulk_hist/option/open_interest",
             {"root": root, "exp": 0, "start_date": start_date, "end_date": end_date})
    if str(d.get("header", {}).get("next_page", "null")) not in ("null", "None", ""):
        raise ThetaError(f"unexpected pagination for {root} {start_date}..{end_date}")
    return d.get("response", [])


EOD_COLS = ["ms_of_day", "ms_of_day2", "open", "high", "low", "close", "volume", "count",
            "bid_size", "bid_exchange", "bid", "bid_condition",
            "ask_size", "ask_exchange", "ask", "ask_condition", "date"]
