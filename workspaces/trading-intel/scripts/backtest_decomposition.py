#!/usr/bin/env python3
"""backtest_decomposition.py — walk-forward validation of the LLM decomposition engine.

The live edge lines (`big_story_direction`, `selection_alpha`) take WEEKS of live outcomes
to move. This validates the new decomposition engine NOW, the same way the quant layer was
validated: point-in-time replay. Take big single-name moves from a historical window, give
the model ONLY information knowable that day (headlines dated <= event date, price history
ending at the event close), ask for the same stance JSON `decompose_events.py` produces
live, then grade against the ACTUAL forward market-relative return we already have bars for.

LOOK-AHEAD HONESTY (the classic LLM-backtest trap, addressed head-on):
  * The model memorized market history up to its training cutoff (~Jan 2026). Events before
    that are contaminated — it may "predict" outcomes it remembers. The test window
    therefore starts 2026-03-01: post-cutoff, genuinely blind. Do not lower WINDOW_START.
  * The prompt hard-blinds the date and forbids post-date knowledge anyway (belt+braces).
  * Remaining caveats, stated in the report: the news corpus is headlines (thinner than the
    primary sources the live engine will grow into), and the tracked universe is mildly
    selection-biased toward names the desk noticed. This validates the ENGINE'S REASONING,
    not a tradeable strategy by itself.

Grading: stance at event-day close -> +10td and +21td excess vs SPY. Baselines on the SAME
events: momentum (follow the move) and fade (oppose it). The engine earns belief only if it
beats both naive baselines. no_trade honesty is reported (was there really no edge?).

Offline analytics only — writes nothing to the live store; LLM responses cached in
state/decomp-backtest-cache.sqlite so reruns are free.

  python3 backtest_decomposition.py --find-only          # list events, no LLM
  python3 backtest_decomposition.py --max 24             # the real run (background it)
  DECOMP_MODEL=claude-opus-4-8 python3 backtest_decomposition.py --max 24
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors import fmp, massive  # noqa: E402

LIVE_DB = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
CACHE_DB = os.path.expanduser("~/.openclaw/state/decomp-backtest-cache.sqlite")
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
MODEL = os.environ.get("DECOMP_MODEL", "claude-sonnet-5")
RUBRIC_VERSION = "decomp-corpus"  # cache namespace; the PROMPT HASH does real invalidation

WINDOW_START = "2026-03-01"   # post-training-cutoff: the model cannot have memorized outcomes
WINDOW_END = "2026-07-08"     # leaves >= 10 trading days of forward bars to grade
EVENT_MOVE_PCT = 4.0          # 1-day close-to-close move that counts as a "big story"
MAX_MOVE_PCT = 30.0           # above this = relisting/split artifacts or untradeable chaos
MIN_PRICE = 5.0
MIN_DOLLAR_VOL = 5_000_000.0  # event-day tradability floor (price * volume)
_INDEX = {"SPY", "QQQ", "SOXX", "IWM", "DIA", "VIX", "GLD", "TLT"}

PROMPT = """You are a disciplined buy-side analyst setting the desk's stance after a big single-name
move. Today is {date}. You know NOTHING about markets, news, or prices after {date} — reason only
from what is below.

{ticker} moved {move:+.1f}% today ({date}).

EMPIRICAL BASE RATE (measured, not opinion): large single-day moves on liquid names CONTINUE in the
same direction more often than they reverse over the following 2-4 weeks (post-event drift).
Following the move is the DEFAULT that must be argued away. Fading requires SPECIFIC, checkable
disconfirming evidence from the record below — the size of the move alone is NEVER a reason to fade.

RECENT HEADLINES (dated on/before {date}):
{headlines}

EARNINGS CONTEXT (point-in-time):
{earnings_ctx}

ANALYST ACTIONS (14 days up to {date}):
{analyst_actions}

PRICE CONTEXT (ending at today's close):
{price_ctx}

Decompose:
1. FIRST-ORDER: the consensus story for this move, one sentence.
2. SECOND-ORDER: the real driver, decomposed — company-specific vs sector/macro flow; genuinely new
   information vs repricing of the known; durable driver vs one-off.
3. CONTINUATION vs REVERSAL: does the evidence support drift (fundamental, thesis-confirming news:
   beat-and-raise, contract win, structural change) or reversal (mechanical/flow-driven: sympathy
   move, forced positioning, no fundamental change)? Name which.
4. STANCE over the NEXT 2-4 WEEKS, market-relative: long / short / no_trade.
   - Default to the move's direction when the driver is fundamental and durable.
   - Fade only with specific disconfirmation, never on valuation feel or move size.
   - no_trade when the evidence is genuinely two-sided OR the move is pure flow with no
     fundamental anchor in the record. But a clear fundamental driver (e.g. a real earnings
     surprise with a raised outlook) DEMANDS a side — sitting those out is also a losing behavior.

Output STRICT JSON only:
{{"stance":"long|short|no_trade","conviction":0.0-1.0,"first_order":"...",
  "second_order":"...","priced_in":"...","falsifier":"...","thesis":"..."}}"""


# ---------------------------------------------------------------- data helpers
def _bars(sym: str) -> list[dict]:
    return [b for b in massive.daily_bars(sym) if b.get("c")]


def _tracked_universe() -> list[str]:
    conn = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    names: set[str] = set()
    for r in conn.execute("SELECT DISTINCT UPPER(ticker) t FROM positions"):
        if r["t"]:
            names.add(r["t"])
    for r in conn.execute("SELECT tickers FROM hypotheses"):
        try:
            names.update(str(t).upper() for t in json.loads(r["tickers"] or "[]"))
        except (ValueError, TypeError):
            continue
    conn.close()
    return sorted(names - _INDEX)


def find_events(max_n: int) -> list[dict]:
    """Biggest 1-day |move| >= EVENT_MOVE_PCT per ticker inside the window (one per ticker)."""
    events = []
    for tk in _tracked_universe():
        try:
            bars = _bars(tk)
        except Exception:
            continue
        cands = []
        for i in range(1, len(bars)):
            d = bars[i]["t"]
            if not (WINDOW_START <= d <= WINDOW_END):
                continue
            p0, p1 = float(bars[i - 1]["c"]), float(bars[i]["c"])
            if p1 < MIN_PRICE or p0 <= 0 or i + 21 >= len(bars):
                continue  # need forward bars to grade
            if p1 * float(bars[i].get("v") or 0) < MIN_DOLLAR_VOL:
                continue  # untradeable micro-cap / bad print
            mv = (p1 / p0 - 1.0) * 100.0
            if not (EVENT_MOVE_PCT <= abs(mv) <= MAX_MOVE_PCT):
                continue  # too small to be a "story" / too big to be clean data
            fwd10 = (float(bars[i + 10]["c"]) / p1 - 1.0) * 100.0
            fwd21 = (float(bars[i + 21]["c"]) / p1 - 1.0) * 100.0
            cands.append({"ticker": tk, "date": d, "move": round(mv, 2), "close": p1,
                          "fwd10": round(fwd10, 2), "fwd21": round(fwd21, 2), "bar_i": i})
        # up to 2 events per ticker, spaced >= 10 trading days so they are distinct episodes
        cands.sort(key=lambda e: -abs(e["move"]))
        picked = []
        for c in cands:
            if len(picked) >= 2:
                break
            if all(abs(c["bar_i"] - q["bar_i"]) >= 10 for q in picked):
                picked.append(c)
        events.extend(picked)
    events.sort(key=lambda e: -abs(e["move"]))
    return events[:max_n]


def _spy_fwd(date: str) -> tuple[float, float] | None:
    bars = _bars("SPY")
    for i, b in enumerate(bars):
        if b["t"] >= date:
            if i + 21 >= len(bars):
                return None
            p = float(bars[i]["c"])
            return ((float(bars[i + 10]["c"]) / p - 1.0) * 100.0,
                    (float(bars[i + 21]["c"]) / p - 1.0) * 100.0)
    return None


def _price_ctx(ticker: str, bar_i: int) -> str:
    bars = _bars(ticker)
    c = [float(b["c"]) for b in bars[: bar_i + 1]]
    if len(c) < 64:
        return f"close ${c[-1]:.2f}"
    return (f"close ${c[-1]:.2f}; trailing 5d {((c[-1]/c[-6])-1)*100:+.1f}%, "
            f"21d {((c[-1]/c[-22])-1)*100:+.1f}%, 63d {((c[-1]/c[-64])-1)*100:+.1f}%")


def _headlines(ticker: str, date: str) -> str:
    try:
        arts = massive.ticker_news(ticker, gte="2026-01-01", to=date)
    except Exception:
        arts = []
    arts = [a for a in arts if a.get("date") and a["date"] <= date][-15:]
    if not arts:
        return "(no headlines available)"
    return "\n".join(f"- [{a['date']}] {a['title']} — {(a.get('description') or '')[:400]}" for a in arts)


# ---------------------------------------------------------------- LLM w/ cache
def _earnings_context(ticker: str, date: str) -> str:
    """Earnings prints on/before the event date — surprise magnitude is the decisive
    continuation-vs-reversal datum the headline diet was missing."""
    try:
        rows = [r for r in fmp.earnings(ticker, limit=40) if r.get("date") and r["date"] <= date]
    except Exception:
        return "(unavailable)"
    rows.sort(key=lambda r: r["date"])
    if not rows:
        return "(no earnings data)"
    from datetime import date as _d
    def _days(a, b):
        try:
            return abs((_d.fromisoformat(a) - _d.fromisoformat(b)).days)
        except ValueError:
            return 999
    out = []
    if _days(rows[-1]["date"], date) <= 3:
        out.append(">>> TODAY'S MOVE IS MOST LIKELY THIS EARNINGS REPORT <<<")
    for r in rows[-2:]:
        ea, ee = r.get("epsActual"), r.get("epsEstimated")
        surp = f"{(ea - ee) / abs(ee) * 100:+.0f}%" if (ea is not None and ee) else "?"
        out.append(f"- report {r['date']}: EPS {ea} vs est {ee} (surprise {surp}); "
                   f"revenue {r.get('revenueActual')} vs est {r.get('revenueEstimated')}")
    return "\n".join(out)


def _analyst_actions(ticker: str, date: str) -> str:
    """Dated analyst grade changes in the 14 days up to the event (point-in-time)."""
    try:
        rows = fmp.upgrades_downgrades(ticker, limit=400)
    except Exception:
        return "(unavailable)"
    from datetime import date as _d, timedelta
    try:
        lo = (_d.fromisoformat(date) - timedelta(days=14)).isoformat()
    except ValueError:
        return "(unavailable)"
    keep = [r for r in rows if r.get("date") and lo <= r["date"] <= date]
    keep.sort(key=lambda r: r["date"], reverse=True)
    if not keep:
        return "(none in the last 14 days)"
    return "\n".join(f"- {r['date']} {r.get('gradingCompany','?')}: {r.get('action','?')} "
                      f"{r.get('previousGrade','?')} -> {r.get('newGrade','?')}" for r in keep[:8])


def _cache() -> sqlite3.Connection:
    conn = sqlite3.connect(CACHE_DB, timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS responses ("
                 "key TEXT PRIMARY KEY, rubric TEXT, model TEXT, response_json TEXT, scored_at TEXT)")
    return conn


def decompose_event(cache: sqlite3.Connection, ev: dict, prompt: str) -> dict | None:
    phash = hashlib.sha256(prompt.encode()).hexdigest()[:16]  # prompt fix ⇒ cache miss ⇒ fresh call
    key = hashlib.sha256(f"{RUBRIC_VERSION}|{MODEL}|{ev['ticker']}|{ev['date']}|{phash}".encode()).hexdigest()
    row = cache.execute("SELECT response_json FROM responses WHERE key=?", (key,)).fetchone()
    if row:
        return json.loads(row[0])
    try:
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", MODEL], input=prompt,
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        print(f"  claude -p failed rc={r.returncode}: {r.stderr[:120]}", file=sys.stderr)
        return None
    m = re.search(r"\{.*\}", r.stdout, re.DOTALL)
    if not m:
        return None
    try:
        res = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    cache.execute("INSERT OR REPLACE INTO responses VALUES (?,?,?,?,datetime('now'))",
                  (key, RUBRIC_VERSION, MODEL, json.dumps(res)))
    cache.commit()
    return res


# ---------------------------------------------------------------- grading
def grade(events: list[dict]) -> dict:
    """Direction-correct + excess captured for the engine vs momentum/fade baselines."""
    act = [e for e in events if e.get("stance") in ("long", "short")]
    nt = [e for e in events if e.get("stance") == "no_trade"]

    def _excess(e, horizon):
        spy = e.get(f"spy_{horizon}")
        return None if spy is None else e[horizon] - spy

    def _score(sign_fn, horizon):
        rows = [(sign_fn(e), _excess(e, horizon)) for e in act]
        rows = [(s, x) for s, x in rows if x is not None and s != 0]
        if not rows:
            return {"n": 0}
        correct = sum(1 for s, x in rows if (x > 0) == (s > 0))
        return {"n": len(rows), "direction_correct": correct,
                "hit_rate": round(correct / len(rows), 3),
                "avg_excess_captured_pct": round(sum(s * x for s, x in rows) / len(rows), 2)}

    stance_sign = lambda e: 1 if e["stance"] == "long" else -1
    mom_sign = lambda e: 1 if e["move"] > 0 else -1
    out = {}
    for hz in ("fwd10", "fwd21"):
        out[hz] = {
            "engine": _score(stance_sign, hz),
            "baseline_momentum": _score(mom_sign, hz),
            "baseline_fade": _score(lambda e: -mom_sign(e), hz),
        }
    nt_x = [abs(_excess(e, "fwd10")) for e in nt if _excess(e, "fwd10") is not None]
    act_x = [abs(_excess(e, "fwd10")) for e in act if _excess(e, "fwd10") is not None]
    out["no_trade_honesty"] = {
        "n_no_trade": len(nt), "n_actionable": len(act),
        "avg_abs_excess_no_trade": round(sum(nt_x) / len(nt_x), 2) if nt_x else None,
        "avg_abs_excess_actionable": round(sum(act_x) / len(act_x), 2) if act_x else None,
    }
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max", type=int, default=24)
    p.add_argument("--find-only", action="store_true")
    p.add_argument("--note", default="", help="one-line description of what changed this run (ledger)")
    args = p.parse_args()

    events = find_events(args.max)
    if args.find_only:
        print(json.dumps({"window": [WINDOW_START, WINDOW_END], "events": events}, indent=1))
        return 0

    cache = _cache()
    for ev in events:
        spy = _spy_fwd(ev["date"])
        ev["spy_fwd10"], ev["spy_fwd21"] = spy if spy else (None, None)
        prompt = PROMPT.format(ticker=ev["ticker"], date=ev["date"], move=ev["move"],
                               headlines=_headlines(ev["ticker"], ev["date"]),
                               earnings_ctx=_earnings_context(ev["ticker"], ev["date"]),
                               analyst_actions=_analyst_actions(ev["ticker"], ev["date"]),
                               price_ctx=_price_ctx(ev["ticker"], ev["bar_i"]))
        res = decompose_event(cache, ev, prompt) or {}
        ev["stance"] = res.get("stance", "error")
        ev["conviction"] = res.get("conviction")
        ev["second_order"] = res.get("second_order")
        ev["priced_in"] = res.get("priced_in")
        print(f"  {ev['ticker']:6s} {ev['date']} {ev['move']:+6.1f}% -> {ev['stance']:8s} "
              f"(fwd10 {ev['fwd10']:+.1f}% vs SPY {ev['spy_fwd10'] if ev['spy_fwd10'] is not None else '?'})",
              file=sys.stderr)

    report = {
        "window": [WINDOW_START, WINDOW_END], "model": MODEL, "rubric": RUBRIC_VERSION,
        "events_tested": len(events),
        "grading": grade(events),
        "caveats": [
            "window starts post-training-cutoff (2026-03) so outcomes are not memorized",
            "headline corpus is thinner than the primary sources the live engine will grow into",
            "tracked-universe selection is mildly biased toward names the desk noticed",
            "gross of costs; validates the engine's reasoning, not a tradeable strategy by itself",
        ],
        "events": events,
    }
    print(json.dumps(report, indent=1))

    # Append-only experiment ledger: every graded run leaves a machine-readable record of
    # what was tried and how it scored — the loop's memory (no version labels needed).
    from datetime import datetime, timezone
    ledger = os.path.expanduser("~/.openclaw/state/decomp-experiments.jsonl")
    g = report["grading"]
    with open(ledger, "a") as fh:
        fh.write(json.dumps({
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "prompt_sha": hashlib.sha256(PROMPT.encode()).hexdigest()[:12],
            "model": MODEL, "n_events": report["events_tested"],
            "note": args.note,
            "fwd10": {k: g["fwd10"][k] for k in ("engine", "baseline_momentum")},
            "fwd21": {k: g["fwd21"][k] for k in ("engine", "baseline_momentum")},
            "no_trade": g["no_trade_honesty"]["n_no_trade"],
        }) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
