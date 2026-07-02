#!/usr/bin/env python3
"""llm_features.py — the LLM feature factory (P3 of the alpha-engine roadmap).

Turns unstructured text we already own into POINT-IN-TIME numeric features by having a
frontier model score it against a FIXED, VERSIONED rubric. The LLM reads and types events;
it never invents numbers downstream — scores are cached per (batch, rubric-version) in
`state/llm-scores.sqlite`, so every read is reproducible forever and only never-seen text
costs a model call.

v1 source: Massive ticker news (title + description, history to ~2021). Rubric `news-v1`
types each article: catalyst class, direction for the equity (-2..+2), materiality (0..1).
Daily aggregates land in features.sqlite as source='llm':

  llm_news_dir          materiality-weighted mean direction, scaled to [-1, 1]
  llm_news_material_ct  count of articles with materiality >= 0.5
  llm_news_neg_mat_ct   count of material articles with direction < 0

as_of = knowable_at = article date (public EOD that day) — same stamping as the lexicon
features, so the two are directly comparable in the panel. The ml_ranker + FDR harness
decide whether these columns carry alpha; nothing here touches live trading.

Engine: headless `claude -p` (Haiku by default — typing headlines is not a hard task).

  python3 llm_features.py backfill --top-n 64 --start 2025-07-01 --end 2026-07-01
  python3 llm_features.py daily --top-n 64          # yesterday's articles (cron step)
"""
from __future__ import annotations

import argparse, hashlib, json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors import massive  # noqa: E402

FEAT_DB = os.path.expanduser("~/.openclaw/state/features.sqlite")
CACHE_DB = os.path.expanduser("~/.openclaw/state/llm-scores.sqlite")
RUBRIC_VERSION = "news-v1"
MODEL = os.environ.get("LLM_FEATURES_MODEL", "claude-haiku-4-5-20251001")
MAX_ARTICLES_PER_BATCH = 60          # cap per ticker-month; keep prompts bounded
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")

RUBRIC = """You are typing financial news for a quantitative dataset. For EACH numbered
article about {ticker}, output one JSON object:
  {{"i": <article number>,
    "class": one of ["earnings","guidance","analyst","product","legal_regulatory",
                     "mna","insider","financing","macro","other_noise"],
    "dir": integer -2..2 — expected effect on {ticker}'s equity value implied by the
           article itself (-2 clearly bad, 0 neutral/unclear, +2 clearly good),
    "mat": 0.0-1.0 — materiality: would a portfolio manager holding {ticker} care?
           (routine listicles/recaps/price-move narration = 0.0-0.2; hard company
           events like earnings, guidance changes, lawsuits, M&A = 0.6-1.0)}}
Judge ONLY from the given title/description. Promotional or hypothetical pieces
("Could X make you a millionaire?") are other_noise with mat<=0.1.
Output STRICTLY a JSON array of these objects, one per article, nothing else."""


def _cache():
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS llm_scores(
        batch_hash TEXT PRIMARY KEY, rubric_version TEXT NOT NULL, ticker TEXT NOT NULL,
        period TEXT NOT NULL, n_articles INTEGER, response_json TEXT NOT NULL,
        model TEXT, scored_at TEXT NOT NULL)""")
    return conn


def _batch_hash(ticker: str, articles: list[dict]) -> str:
    key = RUBRIC_VERSION + "|" + ticker + "|" + "|".join(
        f"{a['date']}~{a['title']}~{a.get('description','')[:200]}" for a in articles)
    return hashlib.sha256(key.encode()).hexdigest()


def _score_batch(ticker: str, articles: list[dict]) -> list[dict] | None:
    """One claude -p call for a batch of articles. Returns per-article score dicts."""
    lines = [f"{i+1}. [{a['date']}] {a['title']} — {(a.get('description') or '')[:220]}"
             for i, a in enumerate(articles)]
    prompt = RUBRIC.format(ticker=ticker) + "\n\nARTICLES:\n" + "\n".join(lines)
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "-p", "--model", MODEL], input=prompt,
            capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        print(f"  claude -p failed rc={r.returncode}: {r.stderr[:150]}", file=sys.stderr)
        return None
    m = re.search(r"\[.*\]", r.stdout, re.DOTALL)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    out = []
    for o in arr:
        try:
            i = int(o["i"]) - 1
            if not (0 <= i < len(articles)):
                continue
            out.append({"i": i, "class": str(o.get("class", "other_noise")),
                        "dir": max(-2, min(2, int(o.get("dir", 0)))),
                        "mat": max(0.0, min(1.0, float(o.get("mat", 0.0))))})
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


def score_ticker_period(conn_cache, ticker: str, start: str, end: str) -> list[tuple]:
    """Score one ticker's articles in [start, end]; returns (date, dir, mat) tuples.
    Cache-first: a batch is scored at most once per rubric version, ever."""
    try:
        arts = massive.ticker_news(ticker, start, end)
    except Exception as e:
        print(f"  {ticker}: news fetch skip ({str(e)[:60]})", file=sys.stderr)
        return []
    if not arts:
        return []
    arts = sorted(arts, key=lambda a: (-float(a.get("relevance") or 0), a["date"]))[:MAX_ARTICLES_PER_BATCH]
    arts = sorted(arts, key=lambda a: a["date"])
    h = _batch_hash(ticker, arts)
    row = conn_cache.execute("SELECT response_json FROM llm_scores WHERE batch_hash=?", (h,)).fetchone()
    if row:
        scores = json.loads(row[0])
    else:
        scores = _score_batch(ticker, arts)
        if scores is None:
            return []
        conn_cache.execute(
            "INSERT OR REPLACE INTO llm_scores VALUES(?,?,?,?,?,?,?,datetime('now'))",
            (h, RUBRIC_VERSION, ticker, f"{start}..{end}", len(arts), json.dumps(scores), MODEL))
        conn_cache.commit()
        time.sleep(0.5)
    return [(arts[s["i"]]["date"], s["dir"], s["mat"]) for s in scores]


def write_features(conn_feat, ticker: str, triples: list[tuple]):
    """Aggregate per day -> 3 feature rows/day, INSERT OR REPLACE (idempotent)."""
    by_day: dict[str, list[tuple]] = {}
    for d, dr, mat in triples:
        by_day.setdefault(d, []).append((dr, mat))
    rows = []
    for d, items in by_day.items():
        wsum = sum(m for _, m in items)
        wdir = (sum(dr * m for dr, m in items) / wsum / 2.0) if wsum > 0 else 0.0
        mat_ct = sum(1 for _, m in items if m >= 0.5)
        neg_ct = sum(1 for dr, m in items if m >= 0.5 and dr < 0)
        rows += [(ticker, d, "llm_news_dir", round(wdir, 4), d, "llm"),
                 (ticker, d, "llm_news_material_ct", float(mat_ct), d, "llm"),
                 (ticker, d, "llm_news_neg_mat_ct", float(neg_ct), d, "llm")]
    if rows:
        conn_feat.executemany(
            "INSERT OR REPLACE INTO features(ticker,as_of,name,value,knowable_at,source) VALUES(?,?,?,?,?,?)", rows)
        conn_feat.commit()
    return len(rows)


def _months(start: str, end: str):
    from datetime import date
    y, m = int(start[:4]), int(start[5:7])
    ye, me = int(end[:4]), int(end[5:7])
    while (y, m) <= (ye, me):
        nm_y, nm_m = (y + 1, 1) if m == 12 else (y, m + 1)
        yield f"{y}-{m:02d}-01", min(f"{nm_y}-{nm_m:02d}-01", end)
        y, m = nm_y, nm_m


def _top_names(n):
    conn = sqlite3.connect(f"file:{FEAT_DB}?mode=ro", uri=True)
    names = [r[0] for r in conn.execute(
        "SELECT symbol FROM universe WHERE status='active' AND market_cap IS NOT NULL "
        "ORDER BY market_cap DESC LIMIT ?", (n,))]
    conn.close()
    return names


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--top-n", type=int, default=64)
    b.add_argument("--names")
    b.add_argument("--start", required=True)
    b.add_argument("--end", required=True)
    d = sub.add_parser("daily")
    d.add_argument("--top-n", type=int, default=64)
    a = ap.parse_args()

    names = ([s.strip().upper() for s in a.names.split(",")] if getattr(a, "names", None)
             else _top_names(a.top_n))
    cache, feat = _cache(), sqlite3.connect(FEAT_DB)
    total = 0
    if a.cmd == "backfill":
        periods = list(_months(a.start, a.end))
        for ti, t in enumerate(names):
            for p0, p1 in periods:
                triples = score_ticker_period(cache, t, p0, p1)
                total += write_features(feat, t, triples)
            print(f"  {t} ({ti+1}/{len(names)}): cumulative {total} feature rows", flush=True)
    else:
        from datetime import date, timedelta
        d1 = date.today().isoformat()
        d0 = (date.today() - timedelta(days=2)).isoformat()
        for t in names:
            triples = score_ticker_period(cache, t, d0, d1)
            total += write_features(feat, t, triples)
        print(f"daily: {total} llm feature rows across {len(names)} names", flush=True)
    print(f"done: {total} feature rows (rubric {RUBRIC_VERSION}, model {MODEL})")
    cache.close(); feat.close()


if __name__ == "__main__":
    main()
