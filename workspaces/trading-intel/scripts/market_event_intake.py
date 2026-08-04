#!/usr/bin/env python3
"""Market-wide, replayable event intake for advisory research.

The prior catalyst lane searched recent articles only for a static ticker
watchlist plus a handful of model names.  That cannot discover market-level
fund flows, liquidations, portfolio transfers, or a causal narrative whose
affected tickers are not yet known.  This lane polls broad market news,
classifies events deterministically, and preserves publication/retrieval time.

It is deliberately *not* a trading authority.  Its output may seed research
and forward tests; it cannot create an intent, size risk, or activate a
mechanism.

Commands:
  market_event_intake.py collect [--fixture normalized-articles.json]
  market_event_intake.py status
  market_event_intake.py brief [--hours 72]
  market_event_intake.py audit [--fail-on-enforced-miss]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from connectors import eventregistry, massive  # noqa: E402

DEFAULT_DB = Path(os.path.expanduser("~/.openclaw/state/event-intel.sqlite"))
DEFAULT_TAXONOMY = ROOT / "config" / "market_event_taxonomy.json"
DEFAULT_CASES = ROOT / "config" / "event_coverage_cases.json"

DDL = """
CREATE TABLE IF NOT EXISTS market_event_articles (
  id                  TEXT PRIMARY KEY,
  source              TEXT NOT NULL,
  source_event_id     TEXT,
  title               TEXT NOT NULL,
  description         TEXT,
  url                 TEXT,
  published_at        TEXT NOT NULL,
  first_retrieved_at  TEXT NOT NULL,
  last_retrieved_at   TEXT NOT NULL,
  query_ids_json      TEXT NOT NULL,
  event_classes_json  TEXT NOT NULL,
  flow_phase          TEXT NOT NULL,
  tickers_json        TEXT NOT NULL,
  entities_json       TEXT NOT NULL,
  raw_sha256          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_event_published
  ON market_event_articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_event_first_retrieved
  ON market_event_articles(first_retrieved_at DESC);
CREATE TABLE IF NOT EXISTS market_event_intake_runs (
  id                  TEXT PRIMARY KEY,
  started_at          TEXT NOT NULL,
  completed_at        TEXT NOT NULL,
  status              TEXT NOT NULL,
  provider_count      INTEGER NOT NULL,
  query_count         INTEGER NOT NULL,
  fetched_count       INTEGER NOT NULL,
  new_count           INTEGER NOT NULL,
  error_count         INTEGER NOT NULL,
  errors_json         TEXT NOT NULL,
  max_published_at    TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: Any, fallback: datetime | None = None) -> str:
    """Normalize provider timestamps to an explicit UTC ISO string."""
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            dt = fallback or utcnow()
        else:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                raw += "T00:00:00Z"
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                dt = fallback or utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def normalized_text(*parts: Any) -> str:
    text = " ".join(str(x or "") for x in parts).lower()
    return re.sub(r"\s+", " ", text).strip()


def phrase_present(text: str, phrase: str) -> bool:
    """Match phrases on token boundaries; never let 'record' match 'recording'."""
    tokens = [re.escape(x) for x in re.findall(r"[a-z0-9]+", phrase.lower())]
    if not tokens:
        return False
    return re.search(r"(?<![a-z0-9])" + r"[\s\-]+".join(tokens) + r"(?![a-z0-9])", text) is not None


def classify_event(title: str, description: str, taxonomy: dict[str, Any]) -> tuple[list[str], str]:
    text = normalized_text(title, description)
    if any(phrase_present(text, p) for p in taxonomy.get("exclude_if_any", [])):
        return [], "none"
    title_text = normalized_text(title)
    classes = []
    for name, phrases in (taxonomy.get("classes") or {}).items():
        # Generic descriptions and company boilerplate routinely mention old
        # buybacks/contracts. Corporate direction therefore requires a phrase
        # in the headline itself; flow classes can use the full lead text.
        haystack = title_text if name.startswith("corporate_") else text
        if any(phrase_present(haystack, p) for p in phrases):
            classes.append(name)
    # "Steep losses" is only fund distress when the article is actually about
    # a fund/portfolio/prime-broker context, not an issuer or plaintiff ad.
    if "fund_distress" in classes and not any(
        phrase_present(text, p) for p in ("fund", "portfolio", "prime broker", "asset manager")
    ):
        classes.remove("fund_distress")
    classes.sort()
    phase = "none"
    # Completion is checked first because articles often mention both prior
    # distress and the completed transfer.  The market implication differs.
    for candidate in ("transfer_complete", "active_unwind", "distress"):
        phrases = (taxonomy.get("flow_phases") or {}).get(candidate, [])
        if any(phrase_present(text, p) for p in phrases):
            phase = candidate
            break
    return classes, phase


def article_id(article: dict[str, Any]) -> str:
    stable = article.get("source_event_id") or article.get("url")
    if not stable:
        stable = "|".join((article.get("source", ""), article.get("published_at", ""), article.get("title", "")))
    return "mea-" + hashlib.sha256(str(stable).encode()).hexdigest()[:24]


def _json_list(values: Iterable[Any]) -> str:
    return json.dumps(sorted({str(x).strip() for x in values if str(x).strip()}), separators=(",", ":"))


def normalize_massive(raw: dict[str, Any], retrieved_at: str) -> dict[str, Any]:
    publisher = raw.get("publisher") or {}
    return {
        "source": f"massive:{publisher.get('name') or 'unknown'}",
        "source_event_id": raw.get("id"),
        "title": raw.get("title") or "",
        "description": raw.get("description") or "",
        "url": raw.get("article_url") or raw.get("amp_url"),
        "published_at": iso_utc(raw.get("published_utc") or raw.get("published_at")),
        "retrieved_at": retrieved_at,
        "query_ids": ["massive_market_stream"],
        "tickers": raw.get("tickers") or [],
        "entities": raw.get("keywords") or [],
    }


def normalize_eventregistry(raw: dict[str, Any], query_id: str, retrieved_at: str) -> dict[str, Any]:
    concepts = raw.get("concepts") or []
    entities = [c[0] for c in concepts if isinstance(c, (list, tuple)) and c and c[0]]
    return {
        "source": f"eventregistry:{raw.get('source') or 'unknown'}",
        "source_event_id": raw.get("uri"),
        "title": raw.get("title") or "",
        "description": raw.get("body") or "",
        "url": raw.get("url"),
        "published_at": iso_utc(raw.get("published_at") or raw.get("date")),
        "retrieved_at": retrieved_at,
        "query_ids": [query_id],
        "tickers": raw.get("tickers") or [],
        "entities": entities,
    }


def connect(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.executescript(DDL)
    return conn


def upsert_article(conn: sqlite3.Connection, article: dict[str, Any], taxonomy: dict[str, Any]) -> bool:
    title = str(article.get("title") or "").strip()
    if not title:
        return False
    description = str(article.get("description") or "").strip()
    classes, phase = classify_event(title, description, taxonomy)
    aid = article_id(article)
    prior = conn.execute(
        "SELECT query_ids_json,event_classes_json,tickers_json,entities_json,first_retrieved_at FROM market_event_articles WHERE id=?",
        (aid,),
    ).fetchone()
    query_ids = set(article.get("query_ids") or [])
    tickers = set(article.get("tickers") or [])
    entities = set(article.get("entities") or [])
    first = article["retrieved_at"]
    if prior:
        query_ids.update(json.loads(prior[0]))
        classes = sorted(set(classes) | set(json.loads(prior[1])))
        tickers.update(json.loads(prior[2]))
        entities.update(json.loads(prior[3]))
        first = min(first, prior[4])
    raw_hash = hashlib.sha256(json.dumps(article, sort_keys=True, default=str).encode()).hexdigest()
    conn.execute(
        """INSERT INTO market_event_articles(
             id,source,source_event_id,title,description,url,published_at,
             first_retrieved_at,last_retrieved_at,query_ids_json,event_classes_json,
             flow_phase,tickers_json,entities_json,raw_sha256)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             last_retrieved_at=excluded.last_retrieved_at,
             query_ids_json=excluded.query_ids_json,
             event_classes_json=excluded.event_classes_json,
             flow_phase=excluded.flow_phase,
             tickers_json=excluded.tickers_json,
             entities_json=excluded.entities_json,
             raw_sha256=excluded.raw_sha256""",
        (aid, article["source"], article.get("source_event_id"), title, description,
         article.get("url"), article["published_at"], first, article["retrieved_at"],
         _json_list(query_ids), _json_list(classes), phase, _json_list(tickers),
         _json_list(entities), raw_hash),
    )
    return prior is None


def reclassify_all(conn: sqlite3.Connection, taxonomy: dict[str, Any]) -> int:
    """Apply the current source-controlled taxonomy to all retained articles."""
    changed = 0
    for aid, title, description, old_classes, old_phase in conn.execute(
        "SELECT id,title,description,event_classes_json,flow_phase FROM market_event_articles"
    ).fetchall():
        classes, phase = classify_event(title, description or "", taxonomy)
        encoded = _json_list(classes)
        if encoded != old_classes or phase != old_phase:
            conn.execute(
                "UPDATE market_event_articles SET event_classes_json=?,flow_phase=? WHERE id=?",
                (encoded, phase, aid),
            )
            changed += 1
    return changed


def collect(db: Path, taxonomy_path: Path, fixture: Path | None = None,
            lookback_hours: int = 168) -> dict[str, Any]:
    taxonomy = load_json(taxonomy_path)
    started = utcnow()
    retrieved = iso_utc(started)
    articles: list[dict[str, Any]] = []
    errors: list[str] = []
    providers = 0
    queries = 0

    if fixture:
        raw_fixture = json.loads(fixture.read_text())
        rows = raw_fixture.get("articles", raw_fixture) if isinstance(raw_fixture, dict) else raw_fixture
        if not isinstance(rows, list):
            raise ValueError("fixture must be an article list or {articles:[...]}")
        for row in rows:
            item = dict(row)
            item.setdefault("source", "fixture")
            item.setdefault("retrieved_at", retrieved)
            item["published_at"] = iso_utc(item.get("published_at"), started)
            item.setdefault("query_ids", ["fixture"])
            item.setdefault("tickers", [])
            item.setdefault("entities", [])
            articles.append(item)
        providers = 1
    else:
        start_date = (started - timedelta(hours=lookback_hours)).date().isoformat()
        try:
            raw = massive.market_news(gte=start_date, max_pages=3, cache_h=0.08)
            articles.extend(normalize_massive(x, retrieved) for x in raw)
            providers += 1
        except Exception as exc:
            errors.append(f"massive:{type(exc).__name__}:{str(exc)[:180]}")

        eventregistry_successes = 0
        for q in taxonomy.get("queries") or []:
            queries += 1
            try:
                raw = eventregistry.recent_news(q["query"], days=max(2, (lookback_hours + 23) // 24),
                                                count=50, cache_h=0.25)
                articles.extend(normalize_eventregistry(x, q["id"], retrieved) for x in raw)
                eventregistry_successes += 1
            except Exception as exc:
                errors.append(f"eventregistry:{q.get('id')}:{type(exc).__name__}:{str(exc)[:160]}")
        if eventregistry_successes:
            providers += 1

    conn = connect(db)
    before = conn.total_changes
    new_count = 0
    for article in articles:
        new_count += int(upsert_article(conn, article, taxonomy))
    reclassified = reclassify_all(conn, taxonomy)
    changed = conn.total_changes - before
    completed = utcnow()
    if not articles:
        status = "failed_empty"
    elif errors:
        status = "degraded"
    else:
        status = "ok"
    run_id = "mei-run-" + uuid.uuid4().hex[:20]
    max_pub = max((x.get("published_at", "") for x in articles), default=None)
    conn.execute(
        "INSERT INTO market_event_intake_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, iso_utc(started), iso_utc(completed), status, providers, queries,
         len(articles), new_count, len(errors), json.dumps(errors), max_pub),
    )
    conn.commit()
    conn.close()
    return {
        "run_id": run_id, "status": status, "providers": providers, "queries": queries,
        "fetched": len(articles), "new": new_count, "changed": changed,
        "reclassified": reclassified,
        "errors": errors, "max_published_at": max_pub,
    }


def rows_for_brief(db: Path, hours: int = 72, limit: int = 40) -> list[dict[str, Any]]:
    conn = connect(db)
    cutoff = iso_utc(utcnow() - timedelta(hours=hours))
    rows = conn.execute(
        """SELECT id,source,title,url,published_at,first_retrieved_at,event_classes_json,
                  flow_phase,tickers_json,entities_json
           FROM market_event_articles
           WHERE published_at>=? AND event_classes_json!='[]'
           ORDER BY published_at DESC LIMIT ?""", (cutoff, limit),
    ).fetchall()
    conn.close()
    keys = ("id", "source", "title", "url", "published_at", "first_retrieved_at",
            "classes", "flow_phase", "tickers", "entities")
    out = []
    for row in rows:
        item = dict(zip(keys, row))
        for key in ("classes", "tickers", "entities"):
            item[key] = json.loads(item[key])
        pub = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
        seen = datetime.fromisoformat(item["first_retrieved_at"].replace("Z", "+00:00"))
        item["retrieval_latency_minutes"] = round(max(0.0, (seen - pub).total_seconds() / 60), 1)
        out.append(item)
    return out


def status(db: Path) -> dict[str, Any]:
    conn = connect(db)
    run = conn.execute(
        "SELECT id,completed_at,status,fetched_count,new_count,error_count,max_published_at FROM market_event_intake_runs ORDER BY completed_at DESC LIMIT 1"
    ).fetchone()
    totals = conn.execute(
        "SELECT COUNT(*),MAX(published_at),MAX(first_retrieved_at) FROM market_event_articles"
    ).fetchone()
    conn.close()
    return {
        "latest_run": (dict(zip(("id", "completed_at", "status", "fetched", "new", "errors", "max_published_at"), run)) if run else None),
        "articles": totals[0], "max_published_at": totals[1], "last_retrieved_at": totals[2],
    }


def audit_cases(db: Path, cases_path: Path) -> dict[str, Any]:
    spec = load_json(cases_path)
    cutover = iso_utc(spec.get("cutover_at"))
    conn = connect(db)
    rows = conn.execute(
        "SELECT title,description,published_at,first_retrieved_at,event_classes_json FROM market_event_articles"
    ).fetchall()
    conn.close()
    results = []
    enforced_misses = 0
    for case in spec.get("cases") or []:
        match = None
        for title, description, published, retrieved, classes_json in rows:
            text = normalized_text(title, description)
            if case.get("terms_all") and not all(phrase_present(text, x) for x in case["terms_all"]):
                continue
            if case.get("terms_any") and not any(phrase_present(text, x) for x in case["terms_any"]):
                continue
            classes = set(json.loads(classes_json))
            if case.get("required_classes_any") and not classes.intersection(case["required_classes_any"]):
                continue
            match = (published, retrieved, sorted(classes))
            break
        known_at = iso_utc(case["known_at"])
        enforced = known_at >= cutover and not case.get("pre_cutover_expected_miss", False)
        if match:
            seen = datetime.fromisoformat(match[1].replace("Z", "+00:00"))
            known = datetime.fromisoformat(known_at.replace("Z", "+00:00"))
            latency = round(max(0.0, (seen - known).total_seconds() / 60), 1)
            passed = latency <= float(case.get("deadline_minutes", 60))
        else:
            latency, passed = None, False
        if enforced and not passed:
            enforced_misses += 1
        results.append({
            "id": case["id"], "enforced": enforced, "matched": bool(match), "passed": passed,
            "latency_minutes": latency, "deadline_minutes": case.get("deadline_minutes", 60),
            "classes": match[2] if match else [],
        })
    return {"cutover_at": cutover, "cases": results, "enforced_misses": enforced_misses}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    sub = ap.add_subparsers(dest="command", required=True)
    cp = sub.add_parser("collect")
    cp.add_argument("--fixture", type=Path)
    cp.add_argument("--lookback-hours", type=int, default=168)
    bp = sub.add_parser("brief")
    bp.add_argument("--hours", type=int, default=72)
    bp.add_argument("--limit", type=int, default=40)
    ap_audit = sub.add_parser("audit")
    ap_audit.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    ap_audit.add_argument("--fail-on-enforced-miss", action="store_true")
    sub.add_parser("status")
    args = ap.parse_args()

    if args.command == "collect":
        result = collect(args.db, args.taxonomy, args.fixture, args.lookback_hours)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "ok" else (2 if result["status"] == "failed_empty" else 1)
    if args.command == "brief":
        print(json.dumps({"events": rows_for_brief(args.db, args.hours, args.limit)}, indent=2))
        return 0
    if args.command == "audit":
        result = audit_cases(args.db, args.cases)
        print(json.dumps(result, indent=2))
        return 1 if args.fail_on_enforced_miss and result["enforced_misses"] else 0
    print(json.dumps(status(args.db), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
