#!/usr/bin/env python3
"""Shared deterministic operations for the market-graded themes layer.

Themes are research memory.  They may organize evidence, suggest hypotheses,
and provide lineage, but this module deliberately exposes no trading action.
"""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
DB_PATH = ROOT / "state/trading-intel.sqlite"
FEATURE_DB = ROOT / "state/features.sqlite"
VALID_STATUS = {"watch", "active", "fading", "dead"}
VALID_SOURCE = {"operator", "debrief", "scanner"}
VALID_OUTCOME = {"support", "contradict", "mixed", "context"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def parse_list(raw: str | list[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        values = raw
    else:
        text = raw.strip()
        if not text:
            return []
        values = json.loads(text) if text.startswith("[") else text.split(",")
    return sorted({str(value).strip().upper() for value in values if str(value).strip()})


def parse_theme_tags(raw: str | None) -> list[tuple[str, str]]:
    """Parse ``theme-id[:outcome]`` tokens for market_debrief."""
    tags: list[tuple[str, str]] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        theme_id, _, outcome = token.partition(":")
        outcome = outcome.strip().lower() or "context"
        if outcome not in VALID_OUTCOME:
            raise ValueError(f"invalid theme outcome {outcome!r}")
        tags.append((theme_id.strip(), outcome))
    return tags


def _audit(conn: sqlite3.Connection, theme_id: str, action: str,
           before: dict | None, after: dict | None, rationale: str,
           actor: str = "archivist") -> None:
    stamp = now_iso()
    aid = "AUDIT-" + uuid.uuid4().hex[:24]
    conn.execute(
        "INSERT INTO audits (id,timestamp,actor,entity_type,entity_id,action,"
        "before_state,after_state,rationale_concise,experiment_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (aid, stamp, actor, "theme", theme_id, action,
         None if before is None else json.dumps(before, sort_keys=True),
         None if after is None else json.dumps(after, sort_keys=True),
         rationale[:500], "themes_v2"),
    )


def append_observation(
    conn: sqlite3.Connection,
    *,
    theme_id: str,
    source_type: str,
    source_id: str | None,
    outcome: str,
    observed_at: str | None = None,
    ticker: str | None = None,
    move_pct: float | None = None,
    beneficiary_return_pct: float | None = None,
    victim_return_pct: float | None = None,
    spread_pct: float | None = None,
    breadth_pct: float | None = None,
    as_of: str | None = None,
    evidence: dict | None = None,
    notes: str | None = None,
) -> bool:
    if outcome not in VALID_OUTCOME:
        raise ValueError(f"invalid outcome {outcome!r}")
    if not conn.execute("SELECT 1 FROM themes WHERE id=?", (theme_id,)).fetchone():
        raise ValueError(f"unknown theme {theme_id!r}")
    oid = "tobs-" + uuid.uuid4().hex[:20]
    observed = observed_at or now_iso()
    try:
        conn.execute(
            "INSERT INTO theme_observations (id,theme_id,observed_at,source_type,"
            "source_id,ticker,move_pct,outcome,beneficiary_return_pct,"
            "victim_return_pct,spread_pct,breadth_pct,as_of,evidence_ref_json,"
            "notes,experiment_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, theme_id, observed, source_type, source_id,
             ticker.upper() if ticker else None, move_pct, outcome,
             beneficiary_return_pct, victim_return_pct, spread_pct,
             breadth_pct, as_of or observed[:10],
             json.dumps(evidence or {}, sort_keys=True), notes, "themes_v2"),
        )
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed" in str(exc):
            return False
        raise
    touch = (as_of or observed[:10]) + "T23:59:59Z"
    conn.execute(
        "UPDATE themes SET updated_at=?,last_evidence_at=CASE "
        "WHEN last_evidence_at IS NULL OR last_evidence_at<? THEN ? "
        "ELSE last_evidence_at END WHERE id=?",
        (now_iso(), touch, touch, theme_id),
    )
    return True


def file_theme(
    conn: sqlite3.Connection,
    *,
    theme_id: str,
    statement: str,
    beneficiaries: list[str],
    victims: list[str],
    status: str,
    source: str,
    created_by: str = "archivist",
    created_at: str | None = None,
    observation: dict | None = None,
) -> dict:
    if status not in VALID_STATUS or source not in VALID_SOURCE:
        raise ValueError("invalid theme status/source")
    if not statement.strip() or len(statement) > 1000:
        raise ValueError("statement must be 1..1000 characters")
    before_row = conn.execute("SELECT * FROM themes WHERE id=?", (theme_id,)).fetchone()
    before = dict(before_row) if before_row else None
    stamp = now_iso()
    refs = []
    if before:
        try:
            refs = json.loads(before["evidence_refs_json"] or "[]")
        except (ValueError, TypeError):
            refs = []
    evidence = (observation or {}).get("evidence")
    if evidence and evidence not in refs:
        refs.append(evidence)
    conn.execute(
        "INSERT INTO themes (id,statement,beneficiaries_json,victims_json,status,"
        "source,created_at,updated_at,evidence_refs_json,created_by,experiment_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "statement=excluded.statement,beneficiaries_json=excluded.beneficiaries_json,"
        "victims_json=excluded.victims_json,status=excluded.status,source=excluded.source,"
        "updated_at=excluded.updated_at,evidence_refs_json=excluded.evidence_refs_json",
        (theme_id, statement.strip(), json.dumps(parse_list(beneficiaries)),
         json.dumps(parse_list(victims)), status, source, created_at or stamp, stamp,
         json.dumps(refs, sort_keys=True), created_by, "themes_v2"),
    )
    emitted = False
    if observation:
        emitted = append_observation(conn, theme_id=theme_id, **{
            key: value for key, value in observation.items() if key != "evidence"
        }, evidence=evidence)
    after = {
        "status": status, "source": source,
        "beneficiaries": parse_list(beneficiaries), "victims": parse_list(victims),
    }
    _audit(conn, theme_id, "theme_file" if before is None else "theme_update",
           before, after, f"{statement[:300]} | observation+={int(emitted)}", created_by)
    return {"theme_id": theme_id, "created": before is None,
            "observation_emitted": emitted, **after}


def cumulative_return(conn: sqlite3.Connection, ticker: str, start: str,
                      end: str) -> tuple[float | None, int]:
    rows = conn.execute(
        "SELECT value FROM features WHERE ticker=? AND name='ret_1d' "
        "AND as_of>? AND as_of<=? ORDER BY as_of",
        (ticker.upper(), start[:10], end[:10]),
    ).fetchall()
    values = [float(row[0]) / 100.0 for row in rows if row[0] is not None]
    if not values:
        return None, 0
    return (math.prod(1.0 + value for value in values) - 1.0) * 100.0, len(values)


def basket_return(conn: sqlite3.Connection, tickers: list[str], start: str,
                  end: str) -> tuple[float | None, int, float | None]:
    results = [cumulative_return(conn, ticker, start, end) for ticker in tickers]
    values = [ret for ret, sessions in results if ret is not None and sessions > 0]
    sessions = min((sessions for ret, sessions in results if ret is not None), default=0)
    breadth = (sum(value > 0 for value in values) / len(values) * 100.0) if values else None
    return (sum(values) / len(values) if values else None), sessions, breadth
