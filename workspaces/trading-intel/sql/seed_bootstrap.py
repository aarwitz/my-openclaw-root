#!/usr/bin/env python3
"""Bootstrap canonical seeds into the trading-intel SQLite store.

Currently seeds:
- active regime_rules from sql/seeds/regime_rules.json
- starter world-model mechanisms from sql/seeds/mechanisms.json

Idempotent: existing rows with the same id are left untouched.

Usage:
    python3 sql/seed_bootstrap.py [path/to/trading-intel.sqlite]

Defaults to ~/.openclaw/state/trading-intel.sqlite.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_DB = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
REGIME_SEED = HERE / "seeds" / "regime_rules.json"
MECHANISMS_SEED = HERE / "seeds" / "mechanisms.json"

# Reuse the shared world-model math for the Beta posterior at seed time.
sys.path.insert(0, str(HERE.parent / "scripts"))
import worldmodel as wm  # noqa: E402


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_regime_rules(conn: sqlite3.Connection) -> str:
    row = json.loads(REGIME_SEED.read_text())
    cur = conn.execute("SELECT 1 FROM regime_rules WHERE id = ?", (row["id"],))
    if cur.fetchone():
        return f"skip (exists): {row['id']}"
    conn.execute(
        """
        INSERT INTO regime_rules
            (id, rule_version, effective_at, thresholds_json, notes, experiment_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            row["id"],
            row["rule_version"],
            row["effective_at"],
            json.dumps(row["thresholds_json"], separators=(",", ":")),
            row.get("notes"),
            row.get("experiment_id"),
        ),
    )
    return f"inserted: {row['id']}"


def seed_mechanisms(conn: sqlite3.Connection) -> list[str]:
    data = json.loads(MECHANISMS_SEED.read_text())
    now = _now_utc_iso()
    results: list[str] = []
    for row in data["mechanisms"]:
        if conn.execute("SELECT 1 FROM mechanisms WHERE id = ?", (row["id"],)).fetchone():
            results.append(f"skip (exists): {row['id']}")
            continue
        a = float(row.get("prior_alpha", 1.0))
        b = float(row.get("prior_beta", 1.0))
        # With zero observations the posterior equals the prior.
        mean = wm.beta_mean(a, b)
        ci_low, ci_high = wm.beta_ci(a, b)
        conn.execute(
            """
            INSERT INTO mechanisms
                (id, created_at, created_by, name, antecedent_class,
                 transmission_chain_json, consequent_class, direction, horizon,
                 regime_context, prior_alpha, prior_beta, observed_hits,
                 observed_misses, posterior_mean, posterior_ci_low,
                 posterior_ci_high, half_life_days, last_observed_at, status,
                 notes, experiment_id)
            VALUES (?, ?, 'system', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?,
                    NULL, ?, ?, ?)
            """,
            (
                row["id"],
                now,
                row["name"],
                row["antecedent_class"],
                json.dumps(row["transmission_chain_json"], separators=(",", ":")),
                row["consequent_class"],
                row["direction"],
                row.get("horizon"),
                row.get("regime_context"),
                a,
                b,
                mean,
                ci_low,
                ci_high,
                float(row.get("half_life_days", 180)),
                row.get("status", "candidate"),
                row.get("notes"),
                row.get("experiment_id", "world_model_seed_v1"),
            ),
        )
        results.append(f"inserted: {row['id']} (mean={mean:.3f})")
    return results


def main(argv: list[str]) -> int:
    db_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_DB
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(db_path)
    try:
        print(seed_regime_rules(conn))
        for line in seed_mechanisms(conn):
            print(line)
        conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
