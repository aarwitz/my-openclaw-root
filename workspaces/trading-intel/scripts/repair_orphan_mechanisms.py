#!/usr/bin/env python3
"""Restore missing mechanism parents without reviving them for trading.

Historical resets deleted mechanism rows while episodes and observations still
referenced them. This tool reconstructs only referenced parents from the
canonical seed libraries, marks every restored row ``deprecated``, and leaves
all child evidence untouched.

Dry-run is the default. Pass ``--apply`` to write.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
sys.path.insert(0, str(ROOT / "workspaces/developer/scripts"))
sys.path.insert(0, str(ROOT / "workspaces/trading-intel/scripts"))

from developer_db import audit, connect, emit, now_iso  # noqa: E402
from seed_episodes import NEW_MECHANISMS  # noqa: E402

SEED_PATH = ROOT / "workspaces/trading-intel/sql/seeds/mechanisms.json"
EXPERIMENT_ID = "preproduction_fk_repair_20260730"


def _definitions() -> dict[str, dict]:
    definitions = {
        row["id"]: {
            **row,
            "transmission_chain": row.get("transmission_chain", []),
            "prior_alpha": 1.0,
            "prior_beta": 1.0,
            "half_life_days": 180.0,
        }
        for row in NEW_MECHANISMS
    }
    for row in json.loads(SEED_PATH.read_text())["mechanisms"]:
        definitions[row["id"]] = {
            **row,
            "transmission_chain": row.get("transmission_chain_json", []),
        }
    return definitions


def _missing_ids(conn) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT mo.mechanism_id
            FROM mechanism_observations mo
            LEFT JOIN mechanisms m ON m.id=mo.mechanism_id
            WHERE m.id IS NULL
            UNION
            SELECT DISTINCT e.mechanism_id
            FROM episodes e
            LEFT JOIN mechanisms m ON m.id=e.mechanism_id
            WHERE e.mechanism_id IS NOT NULL AND m.id IS NULL
            ORDER BY 1
            """
        )
    ]


def repair(conn, *, apply: bool) -> dict:
    definitions = _definitions()
    missing = _missing_ids(conn)
    unknown = [mid for mid in missing if mid not in definitions]
    if unknown:
        raise RuntimeError(
            "missing mechanism definition(s); refusing to fabricate parents: "
            + ", ".join(unknown)
        )
    if not apply:
        return {"missing": missing, "restored": [], "dry_run": True}

    restored: list[str] = []
    try:
        conn.execute("BEGIN")
        for mid in missing:
            row = definitions[mid]
            notes = {
                "recovered_parent": True,
                "trading_eligible": False,
                "reason": "restore foreign-key parent while preserving historical evidence",
                "original_notes": row.get("notes"),
            }
            conn.execute(
                """
                INSERT INTO mechanisms (
                  id, created_at, created_by, name, antecedent_class,
                  transmission_chain_json, consequent_class, direction, horizon,
                  regime_context, prior_alpha, prior_beta, observed_hits,
                  observed_misses, posterior_mean, posterior_ci_low,
                  posterior_ci_high, half_life_days, last_observed_at, status,
                  notes, experiment_id
                ) VALUES (
                  ?, ?, 'developer', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0,
                  NULL, NULL, NULL, ?, NULL, 'deprecated', ?, ?
                )
                """,
                (
                    mid,
                    now_iso(),
                    row["name"],
                    row["antecedent_class"],
                    json.dumps(row.get("transmission_chain", [])),
                    row["consequent_class"],
                    row["direction"],
                    row.get("horizon"),
                    row.get("regime_context"),
                    float(row.get("prior_alpha", 1.0)),
                    float(row.get("prior_beta", 1.0)),
                    float(row.get("half_life_days", 180.0)),
                    json.dumps(notes, sort_keys=True),
                    EXPERIMENT_ID,
                ),
            )
            audit(
                conn,
                actor="developer",
                entity_type="mechanism",
                entity_id=mid,
                action="restore_foreign_key_parent",
                rationale=(
                    "restored as deprecated reference-only parent; preserved "
                    "episode/observation history; not trading eligible"
                ),
                experiment_id=EXPERIMENT_ID,
            )
            restored.append(mid)
        violations = list(conn.execute("PRAGMA foreign_key_check"))
        if violations:
            raise RuntimeError(
                f"foreign_key_check still has {len(violations)} violation(s)"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"missing": missing, "restored": restored, "dry_run": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    conn = connect()
    try:
        result = repair(conn, apply=args.apply)
    finally:
        conn.close()
    emit({"ok": True, **result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
