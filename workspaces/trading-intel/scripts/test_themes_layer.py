#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
import sys
sys.path.insert(0, str(ROOT / "workspaces/trading-intel/scripts"))
sys.path.insert(0, str(ROOT / "workspaces/archivist/scripts"))

import industry_rs
import integrity_check
import market_debrief
import score_themes
import theme_context
import theme_model as tm


def scratch() -> tuple[sqlite3.Connection, str]:
    fd, path = tempfile.mkstemp(suffix=".sqlite", dir="/tmp")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "workspaces/trading-intel/sql/schema.sql").read_text())
    return conn, path


class ThemeStoreTests(unittest.TestCase):
    def tearDown(self) -> None:
        for name in getattr(self, "paths", []):
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    def test_file_update_is_audited_and_observation_is_idempotent(self) -> None:
        conn, path = scratch()
        self.paths = [path]
        observation = {
            "source_type": "operator", "source_id": "operator:test:1",
            "outcome": "support", "as_of": "2026-07-24",
            "evidence": {"fact": "frozen before outcome"}, "notes": "test",
        }
        first = tm.file_theme(
            conn, theme_id="theme-test", statement="A testable cross-name claim.",
            beneficiaries=["AAA"], victims=["BBB"], status="watch",
            source="operator", created_by="human", observation=observation,
        )
        second = tm.file_theme(
            conn, theme_id="theme-test", statement="A refined testable claim.",
            beneficiaries=["AAA"], victims=["BBB"], status="watch",
            source="operator", created_by="human", observation=observation,
        )
        self.assertTrue(first["observation_emitted"])
        self.assertFalse(second["observation_emitted"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM theme_observations").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM audits WHERE entity_type='theme'").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT statement FROM themes").fetchone()[0], "A refined testable claim.")
        conn.close()

    def test_market_debrief_theme_tags_are_strict(self) -> None:
        self.assertEqual(
            tm.parse_theme_tags("theme-a:support,theme-b:contradict"),
            [("theme-a", "support"), ("theme-b", "contradict")],
        )
        with self.assertRaises(ValueError):
            tm.parse_theme_tags("theme-a:invented")
        self.assertIn("--themes", Path(market_debrief.__file__).read_text())

    def test_themes_fresh_fails_for_stale_active_theme(self) -> None:
        conn, path = scratch()
        self.paths = [path]
        stale = (datetime.now(timezone.utc) - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO themes(id,statement,status,source,created_at,updated_at,last_evidence_at) "
            "VALUES ('theme-stale','stale claim','active','operator',?,?,?)",
            (stale, stale, stale),
        )
        self.assertEqual(integrity_check.themes_fresh(conn)["status"], "RED")
        conn.execute("UPDATE themes SET last_evidence_at=datetime('now')")
        self.assertEqual(integrity_check.themes_fresh(conn)["status"], "OK")
        conn.close()

    def test_context_contains_baskets_score_and_no_authority(self) -> None:
        conn, path = scratch()
        self.paths = [path]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO themes(id,statement,beneficiaries_json,victims_json,status,source,"
            "created_at,updated_at,score,score_as_of,last_evidence_at) "
            "VALUES ('theme-context','claim','[\"AAA\"]','[\"BBB\"]','watch','scanner',?,?,?,?,?)",
            (now, now, 3.25, now[:10], now),
        )
        conn.commit()
        conn.close()
        payload = theme_context.build_context(path)
        self.assertEqual(payload["authority"], "research_context_only_no_trade_authority")
        self.assertEqual(payload["themes"][0]["beneficiaries"], ["AAA"])
        self.assertEqual(payload["themes"][0]["score_pct"], 3.25)

    def test_schema_has_hypothesis_and_intent_theme_lineage(self) -> None:
        conn, path = scratch()
        self.paths = [path]
        self.assertIn("theme_id", [row[1] for row in conn.execute("PRAGMA table_info(hypotheses)")])
        self.assertIn("theme_id", [row[1] for row in conn.execute("PRAGMA table_info(trade_intents)")])
        source = (ROOT / "workspaces/trader/scripts/author_intents.py").read_text()
        self.assertIn("direction, theme_id", source)
        snapshot_source = (ROOT / "workspaces/developer/scripts/snapshot_builder.py").read_text()
        self.assertIn('"themes": _load_themes(conn)', snapshot_source)
        conn.close()

    def test_v1_to_market_graded_migration_is_replayable(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE market_events(id TEXT PRIMARY KEY);
            CREATE TABLE hypotheses(id TEXT PRIMARY KEY,state TEXT);
            CREATE TABLE trade_intents(id TEXT PRIMARY KEY,state TEXT);
        """)
        conn.executescript((ROOT / "workspaces/trading-intel/sql/migrations/0029_themes_layer.sql").read_text())
        conn.executescript((ROOT / "workspaces/trading-intel/sql/migrations/0030_themes_market_graded.sql").read_text())
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(conn.execute("SELECT value FROM meta WHERE key='_schema_version'").fetchone()[0], "30")
        self.assertIn("theme_id", [row[1] for row in conn.execute("PRAGMA table_info(hypotheses)")])
        conn.close()


class IndustryScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.path = tempfile.mkstemp(suffix=".sqlite", dir="/tmp")
        os.close(fd)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("CREATE TABLE features(ticker TEXT,as_of TEXT,name TEXT,value REAL)")

    def tearDown(self) -> None:
        self.conn.close()
        os.unlink(self.path)

    def test_bottom_to_top_quartile_inflection_is_detected_without_network(self) -> None:
        groups = {f"g{i}": [f"T{i}A", f"T{i}B", f"T{i}C"] for i in range(8)}
        dates = [f"2026-06-{day:02d}" for day in range(1, 22)] + [f"2026-07-{day:02d}" for day in range(1, 22)]
        for index, date in enumerate(dates):
            for group_no in range(8):
                # g0 is weakest in the old window, strongest in the new one.
                daily = (-1.0 if index < 21 else 1.0) if group_no == 0 else (group_no - 3.5) * 0.05
                for ticker in groups[f"g{group_no}"]:
                    self.conn.execute("INSERT INTO features VALUES (?,?, 'ret_1d',?)", (ticker, date, daily))
        self.conn.commit()
        report = industry_rs.scan(self.path, dates[-1], groups)
        self.assertIn("g0", [row["group"] for row in report["inflections"]])

    def test_status_transitions_require_market_evidence(self) -> None:
        self.assertEqual(score_themes.desired_status("watch", 3.0, 80.0, 4), "watch")
        self.assertEqual(score_themes.desired_status("watch", 3.0, 80.0, 5), "active")
        self.assertEqual(score_themes.desired_status("active", -0.1, 40.0, 5), "fading")
        self.assertEqual(score_themes.desired_status("fading", -6.0, 20.0, 21), "dead")


if __name__ == "__main__":
    unittest.main()
