#!/usr/bin/env python3
"""resolve_challenged.py — close the challenged→resolve loop with deep LLM decomposition.

The desk's #1 measured research failure: 'challenged' theses ROT (76 open). The system flags a
thesis as contradicted-by-reality and then never resolves it — holding stale consensus longs
(NVDA/NFLX/TSM straight into the AI-crowding unwind, 0/3 direction on the month's big stories).
The critic is *supposed* to move theses out of 'challenged'; it doesn't.

This forces the resolution with a STRONG model doing the SECOND-ORDER, CONTRARIAN decomposition
the desk keeps missing — the reasoning that separates 'Kimi bad for semis' (consensus, already
priced) from 'Moonshot still bought GB300s ⇒ NVDA demand intact ⇒ selloff overdone' (the edge).

Per thesis it decides: HOLD (challenge is noise/priced — thesis stands), CLOSE (thesis is broken),
or FLIP (the contrarian read is right — author the opposite thesis, which then flows through the
normal quant→critic→risk gates; it cannot reach the broker un-gated). The LLM authors judgement/
prose only — it never invents numbers, and every transition is audited. Measured on the integrity
scoreboard (challenged-rot count + research direction).

  python3 resolve_challenged.py --dry-run          # print resolutions, write nothing (default off)
  python3 resolve_challenged.py --max 10            # resolve up to 10 (writes)
  RESOLVE_MODEL=claude-opus-4-8 python3 resolve_challenged.py --max 5   # max-quality
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/executor/scripts"))))
from connectors.marketdata import daily_bars  # noqa: E402  (Massive-backed; Alpaca-free)

DB_PATH = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
MODEL = os.environ.get("RESOLVE_MODEL", "claude-sonnet-5")  # strong reasoning; opus for max
_BEAR = ("short", "bear", "downside", "de-rat", "avoid", "overvalued")

PROMPT = """You are a sharp, contrarian buy-side analyst resolving a CHALLENGED thesis. The desk
authored a thesis; then market reality contradicted it and it was flagged 'challenged' — and then
NOBODY re-thought it. Your job is exactly the second-order decomposition the desk keeps missing.

The failure mode you must NOT repeat: parroting the consensus. The desk read "Kimi K3 is a
competition shock, bad for AI semis," stayed long into the rotation, and got run over — when the
edge was second-order: a US-gov primary source said Moonshot ACQUIRED GB300 (Nvidia) servers to
train, i.e. even the competitor still buys the incumbent's picks-and-shovels ⇒ Nvidia demand is
intact ⇒ the selloff was an overreaction. That is the level of reasoning required.

CHALLENGED THESIS
  ticker: {ticker}   original direction: {direction}   conviction: {conviction}
  thesis: {thesis}
  original rationale: {rationale}
  supporting evidence the desk had: {evidence}

WHAT CONTRADICTED IT
  recent market events (narrative + this name's move): {events}
  price since the thesis was set: {price_move}

Decompose, do NOT restate the consensus:
1. FIRST-ORDER: what is the consensus reaction that challenged this thesis?
2. SECOND-ORDER: break the real driver into components (e.g. training vs inference demand; unit
   volume vs margin; genuine frontier threat vs commodity/distillation; who still sells the
   picks-and-shovels; what is already priced).
3. CONTRARIAN CHECK: is the market's reaction — and therefore the challenge — overdone or justified?
   Name the SPECIFIC, checkable fact that would flip the conclusion.
4. DECISION.

Output STRICT JSON, nothing else:
{{"decision":"HOLD|CLOSE|FLIP",
  "direction":"long|short",
  "conviction":0.0-1.0,
  "first_order":"one sentence",
  "second_order":"the real decomposition, 2-4 sentences",
  "contrarian_check":"is the reaction overdone/justified + the specific fact that would flip it",
  "key_facts":["specific, checkable facts that actually drive the call"],
  "rationale":"one-paragraph decision a PM could act on"}}
HOLD = the challenge is noise/already-priced, original thesis stands. CLOSE = genuinely broken,
abandon. FLIP = the contrarian read is right, the OPPOSITE position is the edge (state it in
`direction`)."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _direction(thesis: str, rationale: str) -> str:
    t = (thesis + " " + (rationale or "")).lower()
    return "short" if any(w in t for w in _BEAR) else "long"


def _price_move(ticker: str, since: str | None) -> str:
    try:
        bars = daily_bars(ticker, days=90)
    except Exception:
        return "unavailable"
    if not bars:
        return "unavailable"
    last = bars[-1]["c"]
    start = None
    if since:
        d0 = since[:10]
        for b in bars:
            if b["t"] >= d0:
                start = b["c"]
                break
    start = start or bars[0]["c"]
    return f"{ticker} {((last/start)-1)*100:+.1f}% since {since[:10] if since else 'thesis'} (now ${last:.2f})"


def _events(conn, ticker: str) -> str:
    rows = conn.execute(
        "SELECT event_date, headline, observed_moves_json FROM market_events "
        "WHERE event_date >= date('now','-21 days') ORDER BY event_date DESC LIMIT 8"
    ).fetchall()
    out = []
    for r in rows:
        mv = ""
        try:
            m = json.loads(r["observed_moves_json"] or "{}")
            if ticker in m:
                mv = f" [{ticker} {m[ticker]:+.1f}%]"
        except (ValueError, TypeError):
            pass
        out.append(f"- {r['event_date']}: {(r['headline'] or '')[:180]}{mv}")
    return "\n".join(out) or "(no recent recorded events)"


def _evidence(conn, hyp_id: str) -> str:
    rows = conn.execute(
        "SELECT indicator, value, source, as_of FROM hypothesis_evidence WHERE hypothesis_id=? LIMIT 8",
        (hyp_id,),
    ).fetchall()
    return "; ".join(f"{r['indicator']}={r['value']} ({r['source']}, {r['as_of']})" for r in rows) or "(none)"


def resolve_one(conn, thesis: dict) -> dict | None:
    try:
        ticker = json.loads(thesis["tickers"] or "[]")[0]
    except (ValueError, IndexError):
        return None
    direction = _direction(thesis["thesis_summary"], thesis["rationale_concise"])
    prompt = PROMPT.format(
        ticker=ticker, direction=direction, conviction=thesis["confidence"],
        thesis=thesis["thesis_summary"], rationale=(thesis["rationale_concise"] or "")[:600],
        evidence=_evidence(conn, thesis["id"]),
        events=_events(conn, ticker),
        price_move=_price_move(ticker, thesis["scored_at"] or thesis["created_at"]),
    )
    try:
        r = subprocess.run([CLAUDE_BIN, "-p", "--model", MODEL], input=prompt,
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        print(f"  claude -p failed rc={r.returncode}: {r.stderr[:150]}", file=sys.stderr)
        return None
    m = re.search(r"\{.*\}", r.stdout, re.DOTALL)
    if not m:
        return None
    try:
        res = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    res["_ticker"] = ticker
    res["_orig_direction"] = direction
    return res


def apply_resolution(conn, thesis: dict, res: dict) -> None:
    hid, tkr = thesis["id"], res["_ticker"]
    decision = res.get("decision", "HOLD").upper()
    now = _now()

    def _evrow(hyp, ind, val):
        conn.execute(
            "INSERT INTO hypothesis_evidence (id, hypothesis_id, indicator, value, source, retrieved_at, as_of, signal_type) "
            "VALUES (?,?,?,?,'resolve_challenged',?,date('now'),'llm_resolution')",
            ("ev-" + uuid.uuid4().hex[:12], hyp, ind, str(val)[:500], now),
        )

    def _audit(entity, action, before, after, why):
        # actor must be an allowed agent (schema CHECK); this is critic-lane work
        # (critic owns challenged→resolution). True provenance is in action + evidence.source.
        conn.execute(
            "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, before_state, after_state, rationale_concise) "
            "VALUES (?,?,'critic','hypothesis',?,?,?,?,?)",
            ("AUDIT-" + now.replace(":", "").replace("-", "") + "-" + entity[:20], now, entity, action, before, after, (why or "")[:500]),
        )

    _evrow(hid, "second_order", res.get("second_order", ""))
    _evrow(hid, "contrarian_check", res.get("contrarian_check", ""))
    for f in (res.get("key_facts") or [])[:5]:
        _evrow(hid, "key_fact", f)

    if decision == "HOLD":
        conn.execute("UPDATE hypotheses SET state='active' WHERE id=?", (hid,))
        _audit(hid, "resolve_hold", "challenged", "active", res.get("rationale"))
    elif decision == "CLOSE":
        # resolve the STATE out of the challenged rot; resolved_state (the grade) stays the
        # archivist's outcome-based job — never an LLM judgment. Reason lives in audit+evidence.
        conn.execute("UPDATE hypotheses SET state='resolved', resolved_at=? WHERE id=?", (now, hid))
        _audit(hid, "resolve_close", "challenged", "resolved", res.get("rationale"))
    elif decision == "FLIP":
        conn.execute("UPDATE hypotheses SET state='resolved', resolved_at=? WHERE id=?", (now, hid))
        _audit(hid, "resolve_flip", "challenged", "resolved", res.get("rationale"))
        new_id = "hyp-flip-" + uuid.uuid4().hex[:12]
        flipped_dir = res.get("direction", "short" if res["_orig_direction"] == "long" else "long")
        conn.execute(
            "INSERT INTO hypotheses (id, created_at, created_by, tickers, thesis_summary, state, confidence, "
            "time_horizon, rationale_concise) VALUES (?,?,'critic',?,?,'scored',?, 'position_1_4w', ?)",
            (new_id, now, json.dumps([tkr]),
             f"[{flipped_dir.upper()} {tkr}] contrarian flip of a busted thesis: {res.get('rationale','')[:280]}",
             _conf_enum(res.get("conviction")), (res.get("second_order") or "")[:500]),
        )
        _evrow(new_id, "flip_reason", res.get("rationale", ""))
        for f in (res.get("key_facts") or [])[:5]:
            _evrow(new_id, "key_fact", f)
        _audit(new_id, "author_from_flip", None, "scored", f"contrarian flip of {hid}; goes through quant→critic→risk")


def _conf_enum(c) -> str:
    try:
        c = float(c)
    except (TypeError, ValueError):
        return "medium"
    return "high" if c >= 0.66 else ("low" if c < 0.4 else "medium")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max", type=int, default=8, help="max challenged theses to resolve this run")
    p.add_argument("--dry-run", action="store_true", help="print resolutions, write nothing")
    p.add_argument("--ticker", help="only resolve theses on this ticker (for testing)")
    args = p.parse_args()

    import sqlite3
    conn = sqlite3.connect(DB_PATH, timeout=30)  # busy-wait: passes write concurrently (2026-07-23 lock crash)
    conn.row_factory = sqlite3.Row

    # Deterministic expiry pre-pass: challenged theses that are HYGIENE failures, not
    # contested judgments — never promoted past 'scored', evidence stale > 7d, no open
    # position — retire without an LLM call. Measured 2026-07-23: the challenged pile mixes
    # two streams (critic_baseline hygiene fails on auto-generated scored theses + agent
    # challenges of active theses); only the latter deserves deep resolution.
    expired = 0
    if not args.dry_run:
        rows_exp = conn.execute("""
            SELECT h.id, h.tickers FROM hypotheses h WHERE h.state='challenged'
            AND NOT EXISTS (SELECT 1 FROM audits a WHERE a.entity_id=h.id
                            AND a.after_state IN ('ready','active'))
            AND COALESCE((SELECT MAX(e.retrieved_at) FROM hypothesis_evidence e
                          WHERE e.hypothesis_id=h.id), h.created_at) < datetime('now','-7 days')
        """).fetchall()
        now = _now()
        for r in rows_exp:
            try:
                tk = (json.loads(r["tickers"] or "[]") or [None])[0]
            except ValueError:
                tk = None
            if tk and conn.execute(
                "SELECT 1 FROM positions WHERE UPPER(ticker)=? AND state IN "
                "('opening','open','scaling','trimming','closing') LIMIT 1", (str(tk).upper(),)
            ).fetchone():
                continue  # touching a held name -> deserves real resolution, not expiry
            conn.execute("UPDATE hypotheses SET state='retired' WHERE id=?", (r["id"],))
            conn.execute(
                "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
                "before_state, after_state, rationale_concise) VALUES (?,?,'critic','hypothesis',?,"
                "'resolve_expire','challenged','retired',?)",
                ("AUDIT-" + now.replace(":", "").replace("-", "") + "-" + r["id"][:20], now, r["id"],
                 "deterministic expiry: never promoted, evidence stale >7d, no position — hygiene fail, not a contested thesis"),
            )
            expired += 1
        if expired:
            conn.commit()
    q = ("SELECT id, tickers, state, thesis_summary, rationale_concise, confidence, scored_at, created_at "
         "FROM hypotheses WHERE state='challenged'")
    params: list = []
    if args.ticker:
        q += " AND tickers LIKE ?"
        params.append(f'%"{args.ticker.upper()}"%')
    q += " ORDER BY COALESCE(scored_at, created_at) ASC LIMIT ?"
    params.append(args.max)
    rows = conn.execute(q, params).fetchall()

    out = []
    for row in rows:
        thesis = dict(row)
        res = resolve_one(conn, thesis)
        if not res:
            out.append({"id": thesis["id"], "error": "no_resolution"})
            continue
        rec = {"id": thesis["id"], "ticker": res["_ticker"], "decision": res.get("decision"),
               "direction": res.get("direction"), "second_order": res.get("second_order"),
               "contrarian_check": res.get("contrarian_check"), "rationale": res.get("rationale"),
               "key_facts": res.get("key_facts")}
        if not args.dry_run:
            apply_resolution(conn, thesis, res)
        out.append(rec)
    if not args.dry_run:
        conn.commit()
    print(json.dumps({"resolved": len([o for o in out if "decision" in o]),
                      "expired_hygiene": expired,
                      "dry_run": bool(args.dry_run), "model": MODEL,
                      "generated_at": _now(), "resolutions": out}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
