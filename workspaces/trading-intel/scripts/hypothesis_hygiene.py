#!/usr/bin/env python3
"""Keep the live hypothesis board truthful and one-name/one-thesis.

The research agents historically inserted a new row for every news angle while
``author_intents`` marked a thesis active as soon as it authored an intent. A
blocked or rejected intent therefore left an ``active`` thesis forever, and a
later research pass created another thesis for the same ticker. By 2026-07-31
the board held 283 live rows for 159 tickers, including ten for WHR, while only
38 positions were open.

This repair is deliberately non-destructive: superseded rows become ``dormant``;
their evidence, predictions, audits, and trade lineage remain intact.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
LIVE_STATES = ("raw", "scored", "challenged", "ready", "active")
POSITION_STATES = ("opening", "open", "scaling", "trimming", "closing")
INTENT_STATES = ("proposed", "critic_review", "risk_review", "approved", "submitted", "partial")
STATE_RANK = {"active": 5, "ready": 4, "challenged": 3, "scored": 2, "raw": 1}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_tickers(raw: str | None) -> tuple[str, ...]:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(values, list):
        return ()
    return tuple(dict.fromkeys(str(v).strip().upper() for v in values if str(v).strip()))


def _parse_time(raw: str | None) -> datetime:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _audit(conn: sqlite3.Connection, hypothesis_id: str, action: str,
           before: str, after: str, rationale: str) -> None:
    conn.execute(
        "INSERT INTO audits(id,timestamp,actor,entity_type,entity_id,action,"
        "before_state,after_state,rationale_concise) "
        "VALUES(?,?,'developer','hypothesis',?,?,?,?,?)",
        (
            "AUDIT-" + uuid.uuid4().hex,
            _now(),
            hypothesis_id,
            action,
            before,
            after,
            rationale[:500],
        ),
    )


def inspect_and_repair(conn: sqlite3.Connection, *, repair: bool,
                       grace_hours: float = 6.0) -> dict:
    conn.row_factory = sqlite3.Row
    placeholders_live = ",".join("?" * len(LIVE_STATES))
    placeholders_pos = ",".join("?" * len(POSITION_STATES))
    placeholders_intent = ",".join("?" * len(INTENT_STATES))
    rows = conn.execute(
        f"SELECT id,tickers,state,created_at,resolved_at,resolved_state,archivist_grade "
        f"FROM hypotheses "
        f"WHERE state IN ({placeholders_live})",
        LIVE_STATES,
    ).fetchall()
    position_hypotheses = {
        str(r[0])
        for r in conn.execute(
            f"SELECT DISTINCT hypothesis_id FROM positions "
            f"WHERE state IN ({placeholders_pos}) AND hypothesis_id IS NOT NULL",
            POSITION_STATES,
        )
    }
    intent_hypotheses = {
        str(r[0])
        for r in conn.execute(
            f"SELECT DISTINCT hypothesis_id FROM trade_intents "
            f"WHERE state IN ({placeholders_intent}) AND hypothesis_id IS NOT NULL",
            INTENT_STATES,
        )
    }

    cutoff = datetime.now(timezone.utc) - timedelta(hours=grace_hours)
    dormant: dict[str, dict] = {}
    reopen: dict[str, dict] = {}

    # A position cannot be open against a resolved/dormant thesis. That state
    # disables accurate lifecycle counts and invites Researcher to create a new
    # thesis for capital that is already deployed. Preserve the former grade in
    # the repair audit, then reopen the position's actual lineage.
    position_rows = conn.execute(
        f"SELECT DISTINCT h.id,h.tickers,h.state,h.created_at,h.resolved_at,"
        f"h.resolved_state,h.archivist_grade "
        f"FROM positions p JOIN hypotheses h ON h.id=p.hypothesis_id "
        f"WHERE p.state IN ({placeholders_pos})",
        POSITION_STATES,
    ).fetchall()
    position_tickers: dict[str, set[str]] = {
        str(r["id"]): set(_parse_tickers(r["tickers"])) for r in position_rows
    }
    for row in position_rows:
        hid = str(row["id"])
        if row["state"] == "active":
            continue
        reopen[hid] = {
            "before": str(row["state"]),
            "reason": (
                "open position requires active thesis lineage; cleared premature "
                f"resolution metadata resolved_at={row['resolved_at']} "
                f"resolved_state={row['resolved_state']} grade={row['archivist_grade']}"
            ),
        }

    # The position's lineage owns its ticker. Make any unpositioned competing
    # live thesis dormant before reopening it (and before the DB trigger lands).
    owned_tickers = set().union(*position_tickers.values()) if position_tickers else set()
    for row in rows:
        hid = str(row["id"])
        if hid in position_hypotheses:
            continue
        overlap = owned_tickers.intersection(_parse_tickers(row["tickers"]))
        if overlap:
            owners = sorted(
                owner for owner, tickers in position_tickers.items() if tickers & overlap
            )
            dormant[hid] = {
                "before": str(row["state"]),
                "action": "deduplicate_against_open_position",
                "reason": (
                    f"open-position thesis owns {','.join(sorted(overlap))}; "
                    f"canonical={','.join(owners)}"
                ),
            }

    # ``active`` means an intent or position is actually live. Once both are
    # terminal, keep the thesis as dormant history rather than an eternal live
    # board item. The grace period avoids racing a just-authored intent.
    for row in rows:
        hid = str(row["id"])
        if (
            row["state"] == "active"
            and hid not in position_hypotheses
            and hid not in intent_hypotheses
            and _parse_time(row["created_at"]) <= cutoff
        ):
            dormant[hid] = {
                "before": "active",
                "action": "dormant_no_live_exposure",
                "reason": "active thesis has no open position or non-terminal intent",
            }

    remaining = [r for r in rows if str(r["id"]) not in dormant]
    remaining_ids = {str(r["id"]) for r in remaining}
    remaining.extend(r for r in position_rows if str(r["id"]) not in remaining_ids)
    by_ticker: dict[str, list[sqlite3.Row]] = defaultdict(list)
    malformed: list[str] = []
    for row in remaining:
        tickers = _parse_tickers(row["tickers"])
        if not tickers:
            malformed.append(str(row["id"]))
            continue
        for ticker in tickers:
            by_ticker[ticker].append(row)

    duplicate_tickers: dict[str, list[str]] = {}
    for ticker, candidates in by_ticker.items():
        unique = {str(r["id"]): r for r in candidates}
        if len(unique) <= 1:
            continue
        ordered = sorted(
            unique.values(),
            key=lambda r: (
                str(r["id"]) in position_hypotheses,
                str(r["id"]) in intent_hypotheses,
                STATE_RANK.get(str(r["state"]), 0),
                _parse_time(r["created_at"]),
                str(r["id"]),
            ),
            reverse=True,
        )
        keeper = str(ordered[0]["id"])
        duplicate_tickers[ticker] = [str(r["id"]) for r in ordered]
        for row in ordered[1:]:
            hid = str(row["id"])
            # Never detach a thesis from a currently open position. If a future
            # multi-ticker position creates a conflict, leave it visible so the
            # integrity check pages instead of silently choosing wrong lineage.
            if hid in position_hypotheses:
                continue
            dormant.setdefault(
                hid,
                {
                    "before": str(row["state"]),
                    "action": "deduplicate_live_thesis",
                    "reason": f"superseded live thesis for {ticker}; canonical={keeper}",
                },
            )

    if repair and (dormant or reopen):
        # SAVEPOINT composes safely with callers that already opened a broader
        # money-path transaction; the operational wrapper supplies the writer
        # lock before this function is called.
        conn.execute("SAVEPOINT hypothesis_hygiene")
        try:
            for hid, change in sorted(dormant.items()):
                conn.execute(
                    "UPDATE hypotheses SET state='dormant' WHERE id=? AND state=?",
                    (hid, change["before"]),
                )
                _audit(
                    conn,
                    hid,
                    change["action"],
                    change["before"],
                    "dormant",
                    change["reason"],
                )
            for hid, change in sorted(reopen.items()):
                conn.execute(
                    "UPDATE hypotheses SET state='active',resolved_at=NULL,"
                    "resolved_state=NULL,archivist_grade=NULL WHERE id=?",
                    (hid,),
                )
                _audit(
                    conn,
                    hid,
                    "reopen_for_live_position",
                    change["before"],
                    "active",
                    change["reason"],
                )
            conn.execute("RELEASE SAVEPOINT hypothesis_hygiene")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT hypothesis_hygiene")
            conn.execute("RELEASE SAVEPOINT hypothesis_hygiene")
            raise

    return {
        "repair": repair,
        "live_rows_before": len(rows),
        "live_tickers_before": len(by_ticker),
        "duplicate_tickers_before": len(duplicate_tickers),
        "dormant_count": len(dormant),
        "dormant_ids": sorted(dormant),
        "reopened_position_theses": sorted(reopen),
        "malformed_live_ticker_rows": malformed,
        "largest_duplicate_groups": [
            {"ticker": ticker, "count": len(ids), "ids": ids}
            for ticker, ids in sorted(
                duplicate_tickers.items(), key=lambda item: (-len(item[1]), item[0])
            )[:20]
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--grace-hours", type=float, default=6.0)
    args = parser.parse_args(argv)
    conn = sqlite3.connect(args.db, timeout=60)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        report = inspect_and_repair(
            conn, repair=args.repair, grace_hours=max(0.0, args.grace_hours)
        )
    finally:
        conn.close()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
