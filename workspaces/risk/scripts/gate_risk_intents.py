#!/usr/bin/env python3
"""Risk · gate_risk_intents.py — deterministic intent->order risk gate.

This is the final, mandatory checkpoint before an intent can reach the broker.
The Trader/Quant chain produces a *suggested* size (fractional Kelly); the Risk
agent OWNS the veto and the cap. It consumes trade_intents in state='risk_review'
(handed off by gate_evaluator.py once the critic gate stack passes) and decides:

  approved  — size is within every limit; promote to 'approved'.
  resized   — size exceeded a limit; DOWNSIZE to the binding cap, then approve.
  blocked   — a hard guardrail tripped (halt / no headroom / too many names);
              promote to 'blocked' with a reason.

Limits (deterministic, single source of truth below):
  - per-name notional cap        MAX_NAME_PCT  of equity
  - gross deployed exposure cap   MAX_GROSS_PCT of equity (across open positions
                                  + already-approved/submitted intents)
  - max concurrent names          MAX_POSITIONS
  - daily drawdown halt           day P&L <= -DAILY_DD_HALT_PCT of equity
  - regime halt                   regime=risk_off blocks all new risk
  - correlation-cluster cap       MAX_CLUSTER_PCT of equity across a cluster of
                                  names correlated >=0.70 (best-effort via
                                  risk_model; skipped on data gap, never loosens)

Every decision writes a `risk_reviews` row (verdict + approved_size + the full
limits snapshot + any breaches) and an audit (actor='risk'). Fail-closed: if
account/equity cannot be read, intents are left in 'risk_review' (never auto
-approved) and the script exits non-zero.

Usage:
    python3 gate_risk_intents.py --all-pending
    python3 gate_risk_intents.py --intent-id ti-... [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
from connectors.alpaca import ConnectorError, get_account  # noqa: E402
from connectors import eventregistry  # noqa: E402
import risk_model  # noqa: E402  (correlation-cluster cap; degrades gracefully)

# --- risk limits (single source of truth) ----------------------------------
MAX_NAME_PCT = 0.10        # <=10% of equity in any one name
MAX_GROSS_PCT = 0.60       # <=60% of equity deployed gross
MAX_POSITIONS = 24         # max concurrent names (positions + pending intents); 12->24 per rule_proposal rp-e907106afbfb49a4aff1 (risk-gate audit: ~94% idle cash, all blocks were name-count)
DAILY_DD_HALT_PCT = 0.03   # halt new risk if day P&L <= -3% of equity
# Correlation cap: a correlated cluster (a new name + holdings it co-moves with at
# corr>=0.70) can't exceed this % of equity combined — the "8 names, 1 bet" guard.
MAX_CLUSTER_PCT = risk_model.MAX_CLUSTER_PCT  # 0.25
EXPERIMENT_DEFAULT = "world_model_v1"
OPPOSITION_LOOKBACK_DAYS = 1
OPPOSITION_CACHE_H = 0.05
OPPOSITION_ARTICLE_LIMIT = 12
COMPANY_STOPWORDS = {
    "long", "short", "bullish", "bearish", "q1", "q2", "q3", "q4", "fy",
    "fiscal", "reported", "reports", "results", "earnings", "call", "inc",
    "corp", "corporation", "company", "co", "plc", "ltd", "sa", "ag",
}
ARTICLE_TOKEN_STOPWORDS = COMPANY_STOPWORDS | {
    "stock", "shares", "analyst", "rating", "target", "price", "revenue",
    "growth", "concerns", "maintains", "adjusts", "after", "with", "from",
    "looks", "beaten", "broken", "setup", "cleaner", "noisy", "through",
}
ADVERSE_TITLE_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"\bdowngrad\w*\b",
        r"\bunderweight\b",
        r"\bmaintains?\s+underweight\b",
        r"\bmaintains?\s+sell\b",
        r"\b(cuts?|lowers?|lowered)\b.{0,30}\b(price target|target|estimates?)\b",
        r"\b(revenue|growth|margin|guidance)\s+concerns?\b",
        r"\bslower\s+growth\b",
        r"\bweak\s+guidance\b",
        r"\bguidance\s+cut\b",
        r"\bearnings\s+miss\b",
        r"\brevenue\s+miss\b",
        r"\bprobe\b",
        r"\binvestigation\b",
        r"\brecall\b",
        r"\bfraud\b",
        r"\blawsuit\b",
        r"\bdelay\b",
        r"\bwarning\b",
    )
)

EXIT_OK = 0
EXIT_FAIL_LOUD = 2

PENDING_INTENT_STATES = ("approved", "submitted", "partial")
OPEN_POSITION_STATES = ("opening", "open", "scaling", "trimming", "closing")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"trading-intel DB missing at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _norm_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _title_has_target(title: str, ticker: str, queries: list[str]) -> bool:
    title_norm = _norm_text(title)
    if not title_norm:
        return False
    if ticker and re.search(rf"\b{re.escape(ticker.lower())}\b", title_norm):
        return True
    return any(q and q in title_norm for q in queries if len(q) >= 3)


def _extract_company_queries(ticker: str, thesis_summary: str | None, evidence_rows: list[sqlite3.Row]) -> list[str]:
    queries = [ticker.lower()]
    patterns = [
        thesis_summary or "",
        *[((row["source"] or "") + " " + (row["indicator"] or "")) for row in evidence_rows],
    ]
    for text in patterns:
        for match in re.finditer(r"\b([A-Z][A-Za-z&.'-]+(?:\s+[A-Z][A-Za-z&.'-]+){0,2})\b", text):
            phrase = match.group(1).strip()
            norm = _norm_text(phrase)
            if not norm or norm == ticker.lower():
                continue
            words = [w for w in norm.split() if w and w not in COMPANY_STOPWORDS]
            if not words:
                continue
            candidate = " ".join(words[:3])
            if candidate not in queries:
                queries.append(candidate)
            break
        if len(queries) >= 3:
            break
    return queries[:3]


def _article_tokens(title: str, ticker: str, queries: list[str]) -> set[str]:
    ignore = {ticker.lower()}
    for q in queries:
        ignore.update(q.split())
    return {
        tok for tok in _norm_text(title).split()
        if len(tok) >= 5 and tok not in ARTICLE_TOKEN_STOPWORDS and tok not in ignore
    }


def _is_materially_adverse_article(article: dict, ticker: str, queries: list[str], same_day: str) -> bool:
    if article.get("date") != same_day:
        return False
    title = article.get("title") or ""
    if not _title_has_target(title, ticker, queries):
        return False
    title_norm = _norm_text(title)
    return any(p.search(title_norm) for p in ADVERSE_TITLE_PATTERNS)


def _load_hypothesis_context(conn, hypothesis_id: str, intent_id: str) -> tuple[sqlite3.Row | None, list[sqlite3.Row], list[sqlite3.Row]]:
    hypo = conn.execute(
        "SELECT id, thesis_summary, rationale_concise FROM hypotheses WHERE id=?",
        (hypothesis_id,),
    ).fetchone()
    evidence = conn.execute(
        "SELECT id, indicator, value, source, source_url, retrieved_at FROM hypothesis_evidence "
        "WHERE hypothesis_id=? ORDER BY retrieved_at DESC",
        (hypothesis_id,),
    ).fetchall()
    reviews = conn.execute(
        "SELECT target_type, target_id, reviewed_at, all_challenges_addressed, challenges_json "
        "FROM critic_reviews WHERE target_id IN (?, ?) ORDER BY reviewed_at DESC",
        (hypothesis_id, intent_id),
    ).fetchall()
    return hypo, evidence, reviews


def _article_reflected_in_evidence(article: dict, evidence_rows: list[sqlite3.Row], ticker: str, queries: list[str]) -> bool:
    url = (article.get("url") or "").strip()
    source_norm = _norm_text(article.get("source") or "")
    title_tokens = _article_tokens(article.get("title") or "", ticker, queries)
    for row in evidence_rows:
        blob = " ".join(str(row[k] or "") for k in ("indicator", "value", "source", "source_url"))
        blob_norm = _norm_text(blob)
        if not blob_norm:
            continue
        if url and url == (row["source_url"] or "").strip():
            return True
        if any(p.search(blob_norm) for p in ADVERSE_TITLE_PATTERNS):
            token_hits = len([tok for tok in title_tokens if tok in blob_norm])
            if (source_norm and source_norm in blob_norm) or token_hits >= 2:
                return True
    return False


def _article_addressed_in_review(article: dict, review_rows: list[sqlite3.Row], ticker: str, queries: list[str]) -> bool:
    source_norm = _norm_text(article.get("source") or "")
    title_tokens = _article_tokens(article.get("title") or "", ticker, queries)
    article_day = article.get("date")
    for row in review_rows:
        if not row["all_challenges_addressed"]:
            continue
        reviewed_at = row["reviewed_at"] or ""
        if article_day and not reviewed_at.startswith(article_day):
            if reviewed_at[:10] < article_day:
                continue
        blob_norm = _norm_text(row["challenges_json"] or "")
        if not blob_norm or not any(p.search(blob_norm) for p in ADVERSE_TITLE_PATTERNS):
            continue
        token_hits = len([tok for tok in title_tokens if tok in blob_norm])
        if (source_norm and source_norm in blob_norm) or token_hits >= 2:
            return True
    return False


def _same_day_opposition_block(conn, intent) -> dict | None:
    action = (intent["action"] or "open").lower()
    if action in ("exit", "trim"):
        return None

    hypothesis_id = intent["hypothesis_id"]
    hypo, evidence_rows, review_rows = _load_hypothesis_context(conn, hypothesis_id, intent["id"])
    ticker = (intent["ticker"] or "").upper()
    queries = _extract_company_queries(ticker, hypo["thesis_summary"] if hypo else None, evidence_rows)
    same_day = datetime.now(timezone.utc).date().isoformat()

    articles: list[dict] = []
    seen_urls: set[str] = set()
    try:
        for query in queries:
            for article in eventregistry.recent_news(
                query,
                days=OPPOSITION_LOOKBACK_DAYS,
                count=OPPOSITION_ARTICLE_LIMIT,
                cache_h=OPPOSITION_CACHE_H,
            ):
                url = (article.get("url") or "").strip()
                dedupe_key = url or f"{article.get('date')}|{article.get('title')}"
                if dedupe_key in seen_urls:
                    continue
                seen_urls.add(dedupe_key)
                articles.append(article)
    except Exception as exc:
        return {
            "breach": "same_day_opposition_unavailable",
            "reason": f"opposition sweep unavailable for {ticker}: {str(exc)[:140]}",
            "details": {"ticker": ticker, "queries": queries, "error": str(exc)[:240]},
        }

    unresolved = []
    for article in articles:
        if not _is_materially_adverse_article(article, ticker, queries, same_day):
            continue
        if _article_reflected_in_evidence(article, evidence_rows, ticker, queries):
            continue
        if _article_addressed_in_review(article, review_rows, ticker, queries):
            continue
        unresolved.append(article)

    if not unresolved:
        return None

    headlines = [
        (article.get("title") or "")[:120]
        for article in unresolved[:2]
        if article.get("title")
    ]
    return {
        "breach": "same_day_opposition",
        "reason": (
            f"same-day contradictory flow unresolved for {ticker}: "
            + "; ".join(headlines)
        )[:500],
        "details": {
            "ticker": ticker,
            "queries": queries,
            "same_day": same_day,
            "unresolved_count": len(unresolved),
            "unresolved_articles": [
                {
                    "date": a.get("date"),
                    "title": a.get("title"),
                    "source": a.get("source"),
                    "url": a.get("url"),
                }
                for a in unresolved[:4]
            ],
        },
    }


def _regime_current(conn) -> str:
    row = conn.execute(
        "SELECT current FROM regime ORDER BY determined_at DESC LIMIT 1"
    ).fetchone()
    return row["current"] if row else "unknown"


def _equity_and_daypl() -> tuple[float, float | None]:
    acc = get_account()
    equity = float(acc.get("equity") or acc.get("portfolio_value") or 0.0)
    last_eq = acc.get("last_equity")
    day_pl = None
    if last_eq is not None:
        try:
            day_pl = equity - float(last_eq)
        except (TypeError, ValueError):
            day_pl = None
    return equity, day_pl


def _open_position_value(conn, exclude_ticker: str | None = None) -> float:
    rows = conn.execute(
        "SELECT ticker, current_value, qty, cost_basis FROM positions "
        f"WHERE state IN ({','.join('?' * len(OPEN_POSITION_STATES))})",
        OPEN_POSITION_STATES,
    ).fetchall()
    total = 0.0
    for r in rows:
        if exclude_ticker and (r["ticker"] or "").upper() == exclude_ticker.upper():
            continue
        val = r["current_value"]
        if val is None:
            val = float(r["qty"] or 0) * float(r["cost_basis"] or 0)
        # GROSS exposure: a short position (negative qty/value) consumes risk
        # budget exactly like a long — abs(), never netted (migration 0013).
        total += abs(float(val or 0))
    return total


def _pending_intent_value(conn, exclude_id: str | None = None) -> float:
    rows = conn.execute(
        "SELECT id, ticker, size, entry_price_target FROM trade_intents "
        f"WHERE state IN ({','.join('?' * len(PENDING_INTENT_STATES))})",
        PENDING_INTENT_STATES,
    ).fetchall()
    total = 0.0
    for r in rows:
        if exclude_id and r["id"] == exclude_id:
            continue
        total += abs(float(r["size"] or 0) * float(r["entry_price_target"] or 0))
    return total


def _name_exposure(conn, ticker: str) -> float:
    sym = (ticker or "").upper()
    pos = conn.execute(
        "SELECT current_value, qty, cost_basis FROM positions "
        f"WHERE UPPER(ticker)=? AND state IN ({','.join('?' * len(OPEN_POSITION_STATES))})",
        (sym, *OPEN_POSITION_STATES),
    ).fetchall()
    total = 0.0
    for r in pos:
        val = r["current_value"]
        if val is None:
            val = float(r["qty"] or 0) * float(r["cost_basis"] or 0)
        total += abs(float(val or 0))   # per-name cap on ABS exposure (shorts too)
    intents = conn.execute(
        "SELECT size, entry_price_target FROM trade_intents "
        f"WHERE UPPER(ticker)=? AND state IN ({','.join('?' * len(PENDING_INTENT_STATES))})",
        (sym, *PENDING_INTENT_STATES),
    ).fetchall()
    for r in intents:
        total += abs(float(r["size"] or 0) * float(r["entry_price_target"] or 0))
    return total


def _concurrent_name_count(conn) -> int:
    names = set()
    for r in conn.execute(
        f"SELECT DISTINCT UPPER(ticker) AS t FROM positions "
        f"WHERE state IN ({','.join('?' * len(OPEN_POSITION_STATES))})",
        OPEN_POSITION_STATES,
    ):
        names.add(r["t"])
    for r in conn.execute(
        f"SELECT DISTINCT UPPER(ticker) AS t FROM trade_intents "
        f"WHERE state IN ({','.join('?' * len(PENDING_INTENT_STATES))})",
        PENDING_INTENT_STATES,
    ):
        names.add(r["t"])
    # Names with a pending EXIT/TRIM are leaving the book — don't count them against the max-concurrent
    # cap, so a ranked SWAP can free a slot for its higher-conviction replacement in the same pass.
    exiting = {r["t"] for r in conn.execute(
        "SELECT DISTINCT UPPER(ticker) AS t FROM trade_intents WHERE action IN ('exit','trim') "
        "AND state IN ('proposed','critic_review','risk_review','approved','submitted','partial')")}
    return len(names - exiting)


def gate(conn, intent, equity, day_pl, regime) -> dict:
    iid = intent["id"]
    sym = (intent["ticker"] or "").upper()
    price = float(intent["entry_price_target"] or 0)
    req_qty = int(intent["size"] or 0)
    req_notional = req_qty * price
    action = (intent["action"] or "open").lower()

    # EXITS / TRIMS reduce risk: approve the full requested qty and skip every buy-side exposure cap AND
    # halt (a risk-reducing sell is always allowed, even risk_off / drawdown). This is what lets a ranked
    # SWAP close a weak holding to free a concurrent-name slot for a higher-conviction replacement.
    if action in ("exit", "trim") and req_qty >= 1:
        return {"verdict": "approved", "approved_qty": req_qty,
                "limits": {"action": action, "requested_qty": req_qty, "equity": round(equity, 2)},
                "breaches": [], "reason": f"{action} (risk-reducing sell) approved at full size"}

    opposition = _same_day_opposition_block(conn, intent)
    if opposition is not None:
        return {
            "verdict": "blocked",
            "approved_qty": 0,
            "limits": {"action": action, "requested_qty": req_qty, "equity": round(equity, 2), **opposition["details"]},
            "breaches": [opposition["breach"]],
            "reason": opposition["reason"],
        }

    name_cap = MAX_NAME_PCT * equity
    gross_cap = MAX_GROSS_PCT * equity
    current_gross = _open_position_value(conn) + _pending_intent_value(conn, exclude_id=iid)
    gross_headroom = max(0.0, gross_cap - current_gross)
    name_existing = _name_exposure(conn, sym)
    name_headroom = max(0.0, name_cap - name_existing)
    concurrent = _concurrent_name_count(conn)
    dd_limit = -abs(DAILY_DD_HALT_PCT * equity)

    limits = {
        "equity": round(equity, 2),
        "max_name_pct": MAX_NAME_PCT, "name_cap": round(name_cap, 2),
        "name_existing": round(name_existing, 2), "name_headroom": round(name_headroom, 2),
        "max_gross_pct": MAX_GROSS_PCT, "gross_cap": round(gross_cap, 2),
        "current_gross": round(current_gross, 2), "gross_headroom": round(gross_headroom, 2),
        "max_positions": MAX_POSITIONS, "concurrent_names": concurrent,
        "daily_dd_halt_pct": DAILY_DD_HALT_PCT, "day_pl": (None if day_pl is None else round(day_pl, 2)),
        "requested_qty": req_qty, "requested_notional": round(req_notional, 2),
        "regime": regime,
    }
    breaches: list[str] = []

    # --- hard halts -------------------------------------------------------
    if regime == "risk_off":
        breaches.append("regime_risk_off_halt")
        return {"verdict": "blocked", "approved_qty": 0, "limits": limits,
                "breaches": breaches, "reason": "regime=risk_off halts new risk"}
    if day_pl is not None and day_pl <= dd_limit:
        breaches.append("daily_drawdown_halt")
        return {"verdict": "blocked", "approved_qty": 0, "limits": limits,
                "breaches": breaches,
                "reason": f"day_pl={round(day_pl,2)} <= halt={round(dd_limit,2)}"}
    if price <= 0 or req_qty < 1:
        breaches.append("invalid_intent")
        return {"verdict": "blocked", "approved_qty": 0, "limits": limits,
                "breaches": breaches, "reason": f"price={price} qty={req_qty}"}

    # New-name capacity (only if this name is not already held/pending).
    if name_existing <= 0 and concurrent >= MAX_POSITIONS:
        breaches.append("max_positions")
        return {"verdict": "blocked", "approved_qty": 0, "limits": limits,
                "breaches": breaches,
                "reason": f"concurrent_names={concurrent} >= cap={MAX_POSITIONS}"}

    # --- sizing caps (resize down to the binding limit) -------------------
    approved_notional = req_notional
    if approved_notional > name_headroom:
        approved_notional = name_headroom
        breaches.append("name_concentration_cap")
    if approved_notional > gross_headroom:
        approved_notional = gross_headroom
        breaches.append("gross_exposure_cap")

    # --- correlation-cluster cap (best-effort; data gap => skip, never loosen) --
    try:
        cluster = risk_model.correlated_cluster(conn, sym, equity)
    except Exception:
        cluster = None
    if cluster and len(cluster.get("members", [])) > 1:
        cluster_cap = float(cluster["cap"])
        cluster_existing = float(cluster["value"])
        cluster_headroom = max(0.0, cluster_cap - cluster_existing)
        limits["correlation_cluster"] = {
            "members": cluster["members"], "cap_pct": cluster["cap_pct"],
            "cap": round(cluster_cap, 2), "existing": round(cluster_existing, 2),
            "headroom": round(cluster_headroom, 2),
        }
        if approved_notional > cluster_headroom:
            approved_notional = cluster_headroom
            breaches.append("correlation_cluster_cap")

    approved_qty = int(math.floor(approved_notional / price)) if price > 0 else 0
    if approved_qty < 1:
        return {"verdict": "blocked", "approved_qty": 0, "limits": limits,
                "breaches": breaches or ["no_headroom"],
                "reason": f"no sizing headroom (name={round(name_headroom,2)}, "
                          f"gross={round(gross_headroom,2)})"}

    if approved_qty < req_qty:
        return {"verdict": "resized", "approved_qty": approved_qty, "limits": limits,
                "breaches": breaches,
                "reason": f"resized {req_qty}->{approved_qty} to fit "
                          f"{'+'.join(breaches)}"}
    return {"verdict": "approved", "approved_qty": approved_qty, "limits": limits,
            "breaches": breaches, "reason": "within all limits"}


def apply(conn, intent, decision, exp) -> None:
    iid = intent["id"]
    verdict = decision["verdict"]
    approved_qty = decision["approved_qty"]
    new_state = "blocked" if verdict == "blocked" else "approved"

    rid = "rr-" + uuid.uuid4().hex[:20]
    conn.execute(
        "INSERT INTO risk_reviews (id, target_type, target_id, reviewed_at, "
        "reviewed_by, verdict, approved_size, limits_json, breaches_json, "
        "rationale_concise, experiment_id) "
        "VALUES (?, 'trade_intent', ?, ?, 'risk', ?, ?, ?, ?, ?, ?)",
        (rid, iid, _now_iso(), verdict, float(approved_qty),
         json.dumps(decision["limits"]), json.dumps(decision["breaches"]),
         decision["reason"][:500], exp),
    )

    if verdict == "resized":
        conn.execute("UPDATE trade_intents SET size=?, max_fillable_size=?, "
                     "state='approved' WHERE id=?",
                     (float(approved_qty), float(approved_qty), iid))
    elif verdict == "approved":
        conn.execute("UPDATE trade_intents SET max_fillable_size=?, state='approved' "
                     "WHERE id=?", (float(approved_qty), iid))
    else:  # blocked
        conn.execute("UPDATE trade_intents SET state='blocked', blocked_reason=? "
                     "WHERE id=?", (("risk:" + decision["reason"])[:240], iid))

    aid = "AUDIT-" + _now_iso().replace(":", "").replace("-", "") + "-" + iid[:20]
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise, experiment_id) "
        "VALUES (?, ?, 'risk', 'trade_intent', ?, 'risk_gate', 'risk_review', ?, ?, ?)",
        (aid, _now_iso(), iid, new_state,
         f"{verdict}: {decision['reason']}"[:500], exp),
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--intent-id", default=None)
    p.add_argument("--all-pending", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--experiment-id", default=EXPERIMENT_DEFAULT)
    args = p.parse_args(argv)

    try:
        conn = _connect()
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return EXIT_FAIL_LOUD

    if args.intent_id:
        ids = [args.intent_id]
    elif args.all_pending:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM trade_intents WHERE state='risk_review' "
            "ORDER BY created_at ASC")]
    else:
        p.error("must specify --intent-id or --all-pending")
        return EXIT_FAIL_LOUD

    if not ids:
        print(json.dumps({"processed": 0, "reason": "no intents in risk_review"}))
        return EXIT_OK

    # Fail-closed: cannot size without equity → never auto-approve.
    try:
        equity, day_pl = _equity_and_daypl()
    except ConnectorError as exc:
        print(json.dumps({"error": f"alpaca_account: {exc}", "fail_closed": True,
                          "left_in_risk_review": ids}), file=sys.stderr)
        return EXIT_FAIL_LOUD
    if equity <= 0:
        print(json.dumps({"error": "equity<=0", "fail_closed": True}), file=sys.stderr)
        return EXIT_FAIL_LOUD

    regime = _regime_current(conn)
    results = []
    for iid in ids:
        intent = conn.execute(
            "SELECT id, ticker, size, entry_price_target, state, action FROM trade_intents "
            "WHERE id=?", (iid,)).fetchone()
        if not intent:
            results.append({"intent_id": iid, "error": "not_found"})
            continue
        if intent["state"] != "risk_review":
            results.append({"intent_id": iid, "skipped": True,
                            "reason": f"state={intent['state']!r} not risk_review"})
            continue
        decision = gate(conn, intent, equity, day_pl, regime)
        if not args.dry_run:
            apply(conn, intent, decision, args.experiment_id)
        results.append({"intent_id": iid, "ticker": (intent["ticker"] or "").upper(),
                        "verdict": decision["verdict"],
                        "approved_qty": decision["approved_qty"],
                        "requested_qty": int(intent["size"] or 0),
                        "breaches": decision["breaches"], "reason": decision["reason"]})

    if not args.dry_run:
        conn.commit()
    print(json.dumps({"processed": len(results), "dry_run": bool(args.dry_run),
                      "equity": round(equity, 2),
                      "day_pl": (None if day_pl is None else round(day_pl, 2)),
                      "regime": regime, "results": results}, indent=2, default=str))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
