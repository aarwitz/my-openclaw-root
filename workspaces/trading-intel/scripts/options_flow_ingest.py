#!/usr/bin/env python3
"""Massive options flat-file ingest → per-underlying daily flow (D58).

The options audition passed (opt_net_prem pooled IC 0.059 on 63 names × 1yr,
free ThetaData tier); the operator upgraded to Massive Options Developer
(2026-07-09) for the 4-year history. This script consumes Massive's daily
OPRA day-aggregates flat files (one csv.gz per trading day, every contract's
OHLCV) and reduces each to per-underlying rows in features.sqlite::options_daily:

    (ticker, date, call_vol, put_vol, call_prem, put_prem, contracts)

premium ≈ Σ volume × vwap × 100 per side. Contract tickers parse as
O:<UNDERLYING><YYMMDD><C|P><STRIKE*1000>.

Requires S3 flat-file credentials in credentials/massive-api.json:
    "s3_access_key": "...", "s3_secret": "..."
(from the Massive dashboard → Flat Files). Uses the aws CLI (no boto3 needed).

  python3 options_flow_ingest.py day 2026-07-08          # one day
  python3 options_flow_ingest.py backfill 2022-07-01     # from date → today
  python3 options_flow_ingest.py status
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta

CRED = os.path.expanduser("~/.openclaw/credentials/massive-api.json")
FEAT_DB = os.path.expanduser("~/.openclaw/state/features.sqlite")
S3_ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"
# our evaluated universe: the liquid top-600 the ranker scores (falls back to
# options_daily's existing names + current desk universe)
CONTRACT_RE = re.compile(r"^O:([A-Z.]+)(\d{6})([CP])(\d{8})$")


def _s3_env() -> dict | None:
    d = json.loads(open(CRED).read())
    ak, sk = d.get("s3_access_key"), d.get("s3_secret")
    if not ak or not sk:
        return None
    env = dict(os.environ)
    env.update({"AWS_ACCESS_KEY_ID": ak, "AWS_SECRET_ACCESS_KEY": sk})
    return env


def _universe(conn) -> set[str]:
    tickers = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM options_daily")}
    try:
        tickers |= {r[0] for r in conn.execute(
            "SELECT DISTINCT ticker FROM ml_scores WHERE as_of = (SELECT MAX(as_of) FROM ml_scores)")}
    except sqlite3.OperationalError:
        pass
    return {t.upper() for t in tickers}


def fetch_day(day: str) -> int:
    """Ingest one trading day's flat file. Returns rows written (0 = no file/holiday)."""
    env = _s3_env()
    if env is None:
        print("FATAL: s3_access_key/s3_secret missing in credentials/massive-api.json "
              "(Massive dashboard → Flat Files)", file=sys.stderr)
        return -1
    y, m, _ = day.split("-")
    key = f"us_options_opra/day_aggs_v1/{y}/{m}/{day}.csv.gz"
    proc = subprocess.run(
        ["aws", "s3", "cp", f"s3://{BUCKET}/{key}", "-",
         "--endpoint-url", S3_ENDPOINT, "--no-progress"],
        capture_output=True, env=env, timeout=600)
    if proc.returncode != 0:
        err = proc.stderr.decode()[:160]
        if "404" in err or "NoSuchKey" in err or "not found" in err.lower():
            return 0  # holiday / not yet published
        print(f"FATAL s3 fetch {day}: {err}", file=sys.stderr)
        return -1

    conn = sqlite3.connect(FEAT_DB, timeout=60)
    uni = _universe(conn)
    agg: dict[str, list] = {}  # ticker -> [call_vol, put_vol, call_prem, put_prem, n]
    reader = csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(proc.stdout)), "utf-8"))
    for row in reader:
        mth = CONTRACT_RE.match(row.get("ticker", ""))
        if not mth:
            continue
        und, _exp, cp, _strike = mth.groups()
        if und not in uni:
            continue
        vol = float(row.get("volume") or 0)
        vwap = float(row.get("vwap") or row.get("close") or 0)
        if vol <= 0 or vwap <= 0:
            continue
        a = agg.setdefault(und, [0.0, 0.0, 0.0, 0.0, 0])
        prem = vol * vwap * 100.0
        if cp == "C":
            a[0] += vol; a[2] += prem
        else:
            a[1] += vol; a[3] += prem
        a[4] += 1

    for t, (cv, pv, cprem, pprem, n) in agg.items():
        conn.execute(
            "INSERT OR REPLACE INTO options_daily (ticker, date, call_vol, put_vol, call_prem, put_prem, n_contracts, source) "
            "VALUES (?,?,?,?,?,?,?,'massive-flatfile')", (t, day, cv, pv, cprem, pprem, n))
    conn.commit()
    conn.close()
    return len(agg)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "day":
        n = fetch_day(sys.argv[2])
        print(json.dumps({"day": sys.argv[2], "tickers": n}))
        return 0 if n >= 0 else 1
    if cmd == "backfill":
        start = date.fromisoformat(sys.argv[2])
        d, done, skipped = start, 0, 0
        today = date.today()
        while d < today:
            if d.weekday() < 5:
                n = fetch_day(d.isoformat())
                if n < 0:
                    return 1
                done += 1 if n else 0
                skipped += 0 if n else 1
                if done % 25 == 0 and n:
                    print(f"[{d}] {n} tickers ({done} days done, {skipped} skipped)", flush=True)
            d += timedelta(days=1)
        print(json.dumps({"days_ingested": done, "days_skipped": skipped}))
        return 0
    if cmd == "status":
        conn = sqlite3.connect(FEAT_DB)
        r = conn.execute("SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(date), MAX(date) FROM options_daily").fetchone()
        print(json.dumps({"rows": r[0], "tickers": r[1], "from": r[2], "to": r[3]}))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
