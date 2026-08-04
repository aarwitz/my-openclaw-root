#!/usr/bin/env python3
"""Market-grade theme baskets and audit conservative status transitions."""
from __future__ import annotations

import argparse
import glob
import json
import sqlite3
from datetime import datetime, timezone

import theme_model as tm


def _spy_return(start: str, end: str) -> tuple[float | None, int]:
    paths = sorted(glob.glob(str(tm.ROOT / "state/market-data-cache/massive_spy_1d_*.json")))
    if not paths:
        return None, 0
    best: list[dict] = []
    for path in reversed(paths):
        try:
            bars = json.loads(open(path).read()).get("bars", [])
        except (OSError, ValueError, AttributeError):
            continue
        eligible = [row for row in bars if start[:10] <= str(row.get("t", ""))[:10] <= end[:10]]
        if len(eligible) > len(best):
            best = eligible
        if best and str(best[-1].get("t", ""))[:10] == end[:10]:
            break
    before = [row for row in bars if str(row.get("t", ""))[:10] <= start[:10]] if paths else []
    if not best or not before:
        return None, 0
    first = float(before[-1]["c"])
    last = float(best[-1]["c"])
    return ((last / first - 1.0) * 100.0 if first else None), len(best)


def desired_status(current: str, spread: float | None, breadth: float | None,
                   sessions: int) -> str:
    if spread is None or sessions < 5:
        return current
    if current == "watch" and spread >= 2.0 and (breadth or 0.0) >= 60.0:
        return "active"
    if current == "active" and spread < 0.0:
        return "fading"
    if current == "fading" and sessions >= 21 and spread <= -5.0:
        return "dead"
    if current == "fading" and spread >= 2.0 and (breadth or 0.0) >= 60.0:
        return "active"
    return current


def score_all(db_path=tm.DB_PATH, feature_path=tm.FEATURE_DB,
              as_of: str | None = None, apply: bool = True) -> dict:
    conn = tm.connect(db_path)
    features = sqlite3.connect(f"file:{feature_path}?mode=ro", uri=True)
    features.row_factory = sqlite3.Row
    try:
        as_of = as_of or features.execute(
            "SELECT MAX(as_of) FROM features WHERE name='ret_1d'"
        ).fetchone()[0]
        results = []
        transitions = []
        for row in conn.execute("SELECT * FROM themes ORDER BY id"):
            beneficiaries = tm.parse_list(row["beneficiaries_json"])
            victims = tm.parse_list(row["victims_json"])
            start = row["created_at"][:10]
            ben_ret, ben_sessions, breadth = tm.basket_return(
                features, beneficiaries, start, as_of
            )
            if victims:
                vic_ret, vic_sessions, _ = tm.basket_return(features, victims, start, as_of)
            else:
                vic_ret, vic_sessions = _spy_return(start, as_of)
            sessions = min(ben_sessions, vic_sessions) if ben_sessions and vic_sessions else 0
            spread = (ben_ret - vic_ret) if ben_ret is not None and vic_ret is not None else None
            new_status = desired_status(row["status"], spread, breadth, sessions)
            result = {
                "theme_id": row["id"], "as_of": as_of, "status_before": row["status"],
                "status_after": new_status, "beneficiary_return_pct": ben_ret,
                "victim_return_pct": vic_ret, "spread_pct": spread,
                "breadth_pct": breadth, "sessions": sessions,
                "available": spread is not None and sessions > 0,
            }
            results.append(result)
            if apply:
                conn.execute(
                    "UPDATE themes SET score=?,score_as_of=?,updated_at=?,status=? WHERE id=?",
                    (spread, as_of, tm.now_iso(), new_status, row["id"]),
                )
                if spread is not None and sessions > 0:
                    tm.append_observation(
                        conn, theme_id=row["id"], source_type="scanner",
                        source_id=f"theme-score:{row['id']}:{as_of}",
                        outcome="support" if spread >= 0 else "contradict",
                        beneficiary_return_pct=ben_ret, victim_return_pct=vic_ret,
                        spread_pct=spread, breadth_pct=breadth, as_of=as_of,
                        evidence={"method": "equal_weight_beneficiaries_minus_victims_since_creation",
                                  "sessions": sessions},
                        notes="deterministic market grade; no LLM judgment",
                    )
                if new_status != row["status"]:
                    tm._audit(
                        conn, row["id"], "theme_status_transition",
                        {"status": row["status"], "score": row["score"]},
                        {"status": new_status, "score": spread},
                        f"market-graded transition after {sessions} sessions as of {as_of}",
                        "system",
                    )
                    transitions.append({"theme_id": row["id"], "from": row["status"], "to": new_status})
        if apply:
            conn.commit()
        else:
            conn.rollback()
        return {"as_of": as_of, "themes": results, "transitions": transitions,
                "applied": bool(apply)}
    except Exception:
        conn.rollback()
        raise
    finally:
        features.close()
        conn.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--as-of")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    print(json.dumps(score_all(as_of=args.as_of, apply=not args.dry_run), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
