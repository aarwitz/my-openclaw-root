#!/usr/bin/env python3
"""WSB archive extractor + 21d pooled-IC panel.

Methodology
1. Input is raw WallStreetBets submission archives. The parser accepts local
   `.zst`, `.jsonl`/`.ndjson`, and `.json` files that contain one Reddit
   submission object per record. When no local archive is available, `fetch`
   can pull the same submission stream from Arctic Shift's public archive API
   into a local JSONL cache.
2. Only `r/wallstreetbets` submissions are used. The extractor reads each post's
   `title` + `selftext`, normalizes text, and counts at most one mention per
   ticker per post to avoid single-post spam dominating the panel.
3. Tickers are matched conservatively against the desk's equity universe:
   explicit cashtags (`$NVDA`) and uppercase ticker tokens (`NVDA`) are kept;
   lowercase free text is ignored to reduce false positives.
4. The analysis converts posts into daily ticker mention counts, then builds two
   21-day constructions on each rebalance date:
     - `mention_share`: ticker mentions / total ticker mentions in the trailing
       21 calendar days.
     - `mentions_z`: trailing-21d mention count z-scored versus the ticker's
       own prior 252 calendar days of trailing-21d counts.
5. Rebalances run every 21 SPY trading days. Forward returns are raw close-to-
   close 21-trading-day returns starting on the rebalance date. Pooled IC is the
   mean Spearman rank IC across rebalance dates; the t-stat is the mean IC
   divided by its standard error across rebalance dates.
6. Stability check splits the panel into pre-2025 and 2025+ rebalance cohorts.

Examples
  python3 wsb_archive_ic.py fetch \
      --after 2023-01-01 --before 2025-12-31 \
      --out ~/.openclaw/state/market-data-cache/wsb_posts_2023_2025.jsonl

  python3 wsb_archive_ic.py analyze \
      --input ~/.openclaw/state/market-data-cache/wsb_posts_2023_2025.jsonl \
      --output ~/.openclaw/tmp/wsb_ic_results.json

  python3 wsb_archive_ic.py run \
      --after 2023-01-01 --before 2025-12-31 \
      --cache ~/.openclaw/state/market-data-cache/wsb_posts_2023_2025.jsonl \
      --output ~/.openclaw/tmp/wsb_ic_results.json
"""

from __future__ import annotations

import argparse
import bisect
import concurrent.futures
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_store as fs  # noqa: E402

ARCTIC = "https://arctic-shift.photon-reddit.com/api/posts/search"
FEAT_DB = Path(os.path.expanduser("~/.openclaw/state/features.sqlite"))
TOKEN_RE = re.compile(r"\$?[A-Za-z]{1,5}(?:\.[A-Za-z])?")


@dataclass(frozen=True)
class PostRow:
    created_utc: int
    post_id: str
    title: str
    selftext: str


def iso_utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def ymd(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="pull WSB submissions from Arctic Shift into JSONL")
    p_fetch.add_argument("--after", required=True, help="YYYY-MM-DD")
    p_fetch.add_argument("--before", required=True, help="YYYY-MM-DD")
    p_fetch.add_argument("--out", required=True)

    p_an = sub.add_parser("analyze", help="analyze a local WSB archive/cache file")
    p_an.add_argument("--input", action="append", required=True,
                      help="archive path (.zst/.jsonl/.ndjson/.json); may be repeated")
    p_an.add_argument("--output", required=True)
    p_an.add_argument("--rebalance-step", type=int, default=21,
                      help="SPY trading days between rebalances")
    p_an.add_argument("--lookback-days", type=int, default=21,
                      help="calendar-day mention lookback window")
    p_an.add_argument("--history-days", type=int, default=252,
                      help="calendar-day history for mentions_z")
    p_an.add_argument("--min-window-mentions", type=int, default=2,
                      help="minimum trailing-window mentions for inclusion")

    p_run = sub.add_parser("run", help="fetch to cache if needed, then analyze")
    p_run.add_argument("--after", required=True, help="YYYY-MM-DD")
    p_run.add_argument("--before", required=True, help="YYYY-MM-DD")
    p_run.add_argument("--cache", required=True)
    p_run.add_argument("--output", required=True)
    p_run.add_argument("--rebalance-step", type=int, default=21)
    p_run.add_argument("--lookback-days", type=int, default=21)
    p_run.add_argument("--history-days", type=int, default=252)
    p_run.add_argument("--min-window-mentions", type=int, default=2)
    return ap.parse_args()


def load_symbols() -> set[str]:
    conn = sqlite3.connect(FEAT_DB)
    try:
        rows = conn.execute("SELECT DISTINCT symbol FROM universe WHERE symbol IS NOT NULL").fetchall()
    finally:
        conn.close()
    return {r[0].upper() for r in rows if r[0]}


def iter_json_lines(handle: Iterable[str]) -> Iterator[dict]:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def open_archive(path: Path):
    suffix = path.suffix.lower()
    if suffix in {".jsonl", ".ndjson"}:
        return path.open("rt", encoding="utf-8", errors="replace")
    if suffix == ".json":
        text = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(text)
        if isinstance(data, list):
            return data
        return [data]
    if suffix == ".zst":
        try:
            import zstandard  # type: ignore
        except ImportError:
            proc = subprocess.Popen(
                ["zstd", "-dc", str(path)],
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if proc.stdout is None:
                raise RuntimeError(f"failed to open {path}")
            return proc.stdout
        fh = path.open("rb")
        dctx = zstandard.ZstdDecompressor()
        stream = dctx.stream_reader(fh)
        return (line.decode("utf-8", "replace") for line in stream)
    raise ValueError(f"unsupported archive suffix: {path}")


def normalize_post(obj: dict) -> PostRow | None:
    if {"created_utc", "id", "title", "selftext"} <= set(obj):
        return PostRow(int(obj["created_utc"]), str(obj["id"]),
                       str(obj.get("title") or ""), str(obj.get("selftext") or ""))
    if obj.get("subreddit", "").lower() != "wallstreetbets":
        return None
    title = (obj.get("title") or "").strip()
    selftext = (obj.get("selftext") or "").strip()
    created_utc = obj.get("created_utc")
    post_id = obj.get("id")
    if not title and not selftext:
        return None
    if created_utc is None or not post_id:
        return None
    return PostRow(int(created_utc), str(post_id), title, selftext)


def fetch_posts(after: str, before: str, out_path: Path) -> dict:
    def fetch_window(day_start: datetime) -> tuple[list[str], int]:
        cur = day_start
        day_stop = min(day_start + timedelta(days=1), stop)
        day_lines: list[str] = []
        day_pages = 0
        while cur < day_stop:
            window_stop = day_stop
            while True:
                params = {
                    "subreddit": "wallstreetbets",
                    "after": cur.strftime("%Y-%m-%dT%H:%M:%S"),
                    "before": window_stop.strftime("%Y-%m-%dT%H:%M:%S"),
                    "limit": "auto",
                    "sort": "asc",
                }
                resp = requests.get(ARCTIC, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=120)
                if resp.status_code == 422:
                    span = window_stop - cur
                    if span <= min_window:
                        resp.raise_for_status()
                    window_stop = cur + span / 2
                    continue
                resp.raise_for_status()
                batch = resp.json().get("data") or []
                day_pages += 1
                if not batch:
                    cur = window_stop
                    break
                for obj in batch:
                    row = normalize_post(obj)
                    if row is None:
                        continue
                    day_lines.append(json.dumps({
                        "created_utc": row.created_utc,
                        "id": row.post_id,
                        "title": row.title,
                        "selftext": row.selftext,
                    }, ensure_ascii=True) + "\n")
                last_ts = int(batch[-1]["created_utc"])
                nxt = datetime.fromtimestamp(last_ts + 1, tz=timezone.utc)
                if nxt <= cur:
                    break
                if len(batch) < 1000:
                    cur = window_stop
                    break
                cur = nxt
        return day_lines, day_pages

    out_path.parent.mkdir(parents=True, exist_ok=True)
    start = datetime.fromisoformat(after).replace(tzinfo=timezone.utc)
    stop = datetime.fromisoformat(before).replace(tzinfo=timezone.utc)
    rows = 0
    pages = 0
    min_window = timedelta(hours=1)
    day_starts: list[datetime] = []
    cur = start
    while cur < stop:
        day_starts.append(cur)
        cur = min(cur + timedelta(days=1), stop)
    with out_path.open("w", encoding="utf-8") as out:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            for day_lines, day_pages in ex.map(fetch_window, day_starts):
                pages += day_pages
                rows += len(day_lines)
                out.writelines(day_lines)
    return {"rows": rows, "pages": pages, "out": str(out_path)}


def iter_posts(paths: list[Path]) -> Iterator[PostRow]:
    for path in paths:
        opened = open_archive(path)
        try:
            if isinstance(opened, list):
                for obj in opened:
                    row = normalize_post(obj)
                    if row is not None:
                        yield row
            else:
                for obj in iter_json_lines(opened):
                    row = normalize_post(obj)
                    if row is not None:
                        yield row
        finally:
            closer = getattr(opened, "close", None)
            if callable(closer):
                closer()


def extract_tickers(text: str, symbols: set[str]) -> set[str]:
    seen: set[str] = set()
    for token in TOKEN_RE.findall(text):
        if not token:
            continue
        raw = token.strip()
        sym = raw.lstrip("$").upper()
        if sym not in symbols:
            continue
        if raw.startswith("$"):
            if len(sym) >= 1:
                seen.add(sym)
            continue
        if len(raw) == 1:
            continue
        if raw.upper() == raw:
            seen.add(sym)
    return seen


def build_daily_counts(posts: Iterable[PostRow], symbols: set[str]) -> tuple[dict[str, Counter], dict[str, int]]:
    day_counts: dict[str, Counter] = defaultdict(Counter)
    daily_total_mentions: dict[str, int] = defaultdict(int)
    for row in posts:
        tickers = extract_tickers(f"{row.title}\n{row.selftext}", symbols)
        if not tickers:
            continue
        day = ymd(row.created_utc)
        for ticker in tickers:
            day_counts[ticker][day] += 1
            daily_total_mentions[day] += 1
    return day_counts, daily_total_mentions


def rolling_window_sum(counter: Counter, day_idx: dict[str, int], all_days: list[str], lookback_days: int) -> list[int]:
    vals = [counter.get(day, 0) for day in all_days]
    out = [0] * len(vals)
    run = 0
    left = 0
    for i, day in enumerate(all_days):
        cur_date = date.fromisoformat(day)
        run += vals[i]
        while left <= i and (cur_date - date.fromisoformat(all_days[left])).days >= lookback_days:
            run -= vals[left]
            left += 1
        out[i] = run
    return out


def load_price_series(tickers: set[str]) -> dict[str, dict[str, list | dict]]:
    series = {}
    need = sorted(set(tickers) | {"SPY"})
    for i, ticker in enumerate(need, 1):
        bars = fs._prices(ticker, days=4000)
        if not bars:
            continue
        dates = [b["t"][:10] for b in bars]
        close = {b["t"][:10]: float(b["c"]) for b in bars if b.get("c")}
        if len(dates) >= 300:
            series[ticker] = {"dates": dates, "close": close}
        if i % 100 == 0:
            print(f"loaded prices {i}/{len(need)}", file=sys.stderr)
    return series


def spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(vals: list[float]) -> list[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        out = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i + 1
            while j < len(order) and vals[order[j]] == vals[order[i]]:
                j += 1
            rank = (i + j - 1) / 2.0
            for k in range(i, j):
                out[order[k]] = rank
            i = j
        return out

    rx = ranks(xs)
    ry = ranks(ys)
    n = len(xs)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx and dy else 0.0


def summarize_ic(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"pooled_ic": None, "t_stat": None, "n_rebalances": 0}
    mean = sum(values) / len(values)
    if len(values) == 1:
        return {"pooled_ic": round(mean, 4), "t_stat": None, "n_rebalances": 1}
    sd = math.sqrt(sum((x - mean) ** 2 for x in values) / (len(values) - 1))
    t_stat = None if sd == 0 else mean / (sd / math.sqrt(len(values)))
    return {
        "pooled_ic": round(mean, 4),
        "t_stat": None if t_stat is None else round(t_stat, 2),
        "n_rebalances": len(values),
    }


def analyze(paths: list[Path], output: Path, rebalance_step: int, lookback_days: int,
            history_days: int, min_window_mentions: int) -> dict:
    symbols = load_symbols()
    day_counts, daily_total_mentions = build_daily_counts(iter_posts(paths), symbols)
    covered_tickers = {ticker for ticker, counts in day_counts.items() if sum(counts.values()) >= min_window_mentions}
    price_series = load_price_series(covered_tickers)
    tickers = sorted(set(price_series) & set(day_counts))
    all_days = sorted({day for counts in day_counts.values() for day in counts})
    day_idx = {day: i for i, day in enumerate(all_days)}

    ticker_windows: dict[str, list[int]] = {}
    for ticker in tickers:
        ticker_windows[ticker] = rolling_window_sum(day_counts[ticker], day_idx, all_days, lookback_days)
    total_counter = Counter(daily_total_mentions)
    total_windows = rolling_window_sum(total_counter, day_idx, all_days, lookback_days)

    spy = price_series["SPY"]["dates"]
    rebalance_dates = spy[252::rebalance_step]
    min_hist_obs = max(20, min(63, history_days // 2))
    panel = {"mention_share": [], "mentions_z": []}
    per_day = {"mention_share": [], "mentions_z": []}
    split = {
        "pre_2025": {"mention_share": [], "mentions_z": []},
        "post_2025": {"mention_share": [], "mentions_z": []},
    }

    for rebalance_day in rebalance_dates:
        idx = bisect.bisect_left(all_days, rebalance_day) - 1
        if idx < 0:
            continue
        xs_share, xs_z, ys = [], [], []
        for ticker in tickers:
            ps = price_series[ticker]
            dates = ps["dates"]
            pos = bisect.bisect_left(dates, rebalance_day)
            if pos >= len(dates) or dates[pos] != rebalance_day or pos + 21 >= len(dates):
                continue
            mention_count = ticker_windows[ticker][idx]
            if mention_count < min_window_mentions:
                continue
            total_mentions = total_windows[idx]
            if total_mentions <= 0:
                continue
            hist_left_day = (date.fromisoformat(rebalance_day) - timedelta(days=history_days)).isoformat()
            hist_start = bisect.bisect_left(all_days, hist_left_day)
            hist_vals = ticker_windows[ticker][hist_start:idx]
            hist_nonzero = [x for x in hist_vals if x > 0]
            if len(hist_nonzero) < min_hist_obs:
                continue
            hist_mean = sum(hist_nonzero) / len(hist_nonzero)
            hist_sd = math.sqrt(sum((x - hist_mean) ** 2 for x in hist_nonzero) / len(hist_nonzero))
            if hist_sd == 0:
                continue
            share = mention_count / total_mentions
            mentions_z = (mention_count - hist_mean) / hist_sd
            c0 = ps["close"][dates[pos]]
            c1 = ps["close"][dates[pos + 21]]
            ret21 = c1 / c0 - 1.0
            xs_share.append(share)
            xs_z.append(mentions_z)
            ys.append(ret21)

        if len(ys) < 15:
            continue
        share_ic = spearman(xs_share, ys)
        z_ic = spearman(xs_z, ys)
        panel["mention_share"].append(share_ic)
        panel["mentions_z"].append(z_ic)
        bucket = "pre_2025" if rebalance_day < "2025-01-01" else "post_2025"
        split[bucket]["mention_share"].append(share_ic)
        split[bucket]["mentions_z"].append(z_ic)
        per_day["mention_share"].append({"rebalance_day": rebalance_day, "ic": round(share_ic, 6), "n": len(ys)})
        per_day["mentions_z"].append({"rebalance_day": rebalance_day, "ic": round(z_ic, 6), "n": len(ys)})

    result = {
        "methodology": {
            "source": "wallstreetbets submissions only (title + selftext)",
            "mention_rule": "one count per ticker per post; cashtags and uppercase ticker tokens only",
            "lookback_days": lookback_days,
            "history_days_for_mentions_z": history_days,
            "rebalance_step_trading_days": rebalance_step,
            "forward_return_horizon_trading_days": 21,
            "min_window_mentions": min_window_mentions,
        },
        "coverage": {
            "tickers_with_mentions": len(day_counts),
            "tickers_scored": len(tickers),
            "first_post_day": all_days[0] if all_days else None,
            "last_post_day": all_days[-1] if all_days else None,
        },
        "results": {
            "mention_share": summarize_ic(panel["mention_share"]),
            "mentions_z": summarize_ic(panel["mentions_z"]),
        },
        "stability_check": {
            "pre_2025": {
                "mention_share": summarize_ic(split["pre_2025"]["mention_share"]),
                "mentions_z": summarize_ic(split["pre_2025"]["mentions_z"]),
            },
            "post_2025": {
                "mention_share": summarize_ic(split["post_2025"]["mention_share"]),
                "mentions_z": summarize_ic(split["post_2025"]["mentions_z"]),
            },
        },
        "per_rebalance": per_day,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    args = parse_args()
    if args.cmd == "fetch":
        out = fetch_posts(args.after, args.before, Path(args.out))
        print(json.dumps(out, indent=2))
        return 0
    if args.cmd == "analyze":
        result = analyze([Path(p) for p in args.input], Path(args.output), args.rebalance_step,
                         args.lookback_days, args.history_days, args.min_window_mentions)
        print(json.dumps(result["results"], indent=2))
        print(json.dumps(result["stability_check"], indent=2))
        return 0
    cache = Path(args.cache)
    if not cache.exists():
        meta = fetch_posts(args.after, args.before, cache)
        print(json.dumps(meta, indent=2), file=sys.stderr)
    result = analyze([cache], Path(args.output), args.rebalance_step,
                     args.lookback_days, args.history_days, args.min_window_mentions)
    print(json.dumps(result["results"], indent=2))
    print(json.dumps(result["stability_check"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
