import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parent))
import link_mechanisms


class LinkMechanismsTests(unittest.TestCase):
    def _make_relink_db(self) -> str:
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.executescript(
            """
            CREATE TABLE mechanisms (
              id TEXT PRIMARY KEY,
              name TEXT,
              antecedent_class TEXT,
              consequent_class TEXT,
              direction TEXT,
              horizon TEXT,
              status TEXT
            );
            CREATE TABLE hypotheses (
              id TEXT PRIMARY KEY,
              thesis_summary TEXT,
              time_horizon TEXT
            );
            CREATE TABLE hypothesis_evidence (
              id TEXT PRIMARY KEY,
              hypothesis_id TEXT,
              indicator TEXT
            );
            CREATE TABLE predictions (
              id TEXT PRIMARY KEY,
              hypothesis_id TEXT,
              mechanism_ids_json TEXT,
              realized_outcome TEXT,
              brier_component REAL,
              resolved_at TEXT,
              regime_at_prediction TEXT
            );
            CREATE TABLE mechanism_observations (
              id TEXT PRIMARY KEY,
              mechanism_id TEXT,
              observed_at TEXT,
              source_type TEXT,
              source_id TEXT,
              outcome TEXT,
              weight REAL,
              regime_at_obs TEXT,
              notes TEXT,
              experiment_id TEXT
            );
            CREATE TABLE audits (
              id TEXT PRIMARY KEY,
              timestamp TEXT,
              actor TEXT,
              entity_type TEXT,
              entity_id TEXT,
              action TEXT,
              before_state TEXT,
              after_state TEXT,
              rationale_concise TEXT,
              experiment_id TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO mechanisms VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "old_mech",
                    "Old Mechanism",
                    "old",
                    "old",
                    "long",
                    "position_1_4w",
                    "active",
                ),
                (
                    "gen_revenue_growth_yoy_hi_long__quarter_63d",
                    "Revenue Growth High",
                    "revenue_growth",
                    "fundamental_strength",
                    "long",
                    "position_1_4w",
                    "active",
                ),
            ],
        )
        conn.execute(
            "INSERT INTO hypotheses VALUES (?, ?, ?)",
            (
                "h1",
                "Mechanisms: Revenue Growth High. Revenue growth should persist.",
                "position_1_4w",
            ),
        )
        conn.execute(
            "INSERT INTO predictions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "p1",
                "h1",
                '["old_mech"]',
                "correct",
                0.21,
                link_mechanisms._now_iso(),
                "neutral",
            ),
        )
        conn.execute(
            "INSERT INTO mechanism_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "mobs-old",
                "old_mech",
                link_mechanisms._now_iso(),
                "prediction",
                "p1",
                "hit",
                1.0,
                "neutral",
                "old observation",
                "world_model_v1",
            ),
        )
        conn.commit()
        conn.close()
        return tmp.name

    def test_multi_mechanisms_require_exact_name_linkage(self) -> None:
        mechs = [
            {
                "id": "multi_revenue_growth_yoy_hi_mom_12_1_hi__month_21d",
                "name": "Revenue Growth + Momentum",
                "antecedent_class": "revenue_growth",
                "consequent_class": "momentum",
                "horizon": "position_1_4w",
            },
            {
                "id": "gen_revenue_growth_yoy_hi_long__quarter_63d",
                "name": "Revenue Growth High",
                "antecedent_class": "revenue_growth",
                "consequent_class": "fundamental_strength",
                "horizon": "position_1_4w",
            },
        ]

        fuzzy = link_mechanisms.link(
            "revenue growth and momentum are improving",
            mechs,
            "position_1_4w",
        )

        self.assertNotIn("multi_revenue_growth_yoy_hi_mom_12_1_hi__month_21d", [m["id"] for m in fuzzy])
        self.assertIn("gen_revenue_growth_yoy_hi_long__quarter_63d", [m["id"] for m in fuzzy])

        exact = link_mechanisms.link(
            "Mechanisms: revenue growth + momentum",
            mechs,
            "position_1_4w",
        )

        self.assertEqual(exact[0]["id"], "multi_revenue_growth_yoy_hi_mom_12_1_hi__month_21d")
        self.assertEqual(exact[0]["src"], "name")

    def test_relink_resolved_dry_run_reports_replay_cohort_without_writes(self) -> None:
        db_path = self._make_relink_db()
        self.addCleanup(os.unlink, db_path)

        result = link_mechanisms.relink_resolved(dry_run=True, execute=False, db_path=db_path)

        self.assertEqual(result["resolved_predictions"], 1)
        self.assertEqual(result["changed_links"], 1)
        self.assertEqual(result["changed_prediction_sample"][0]["old_ids"], ["old_mech"])
        self.assertEqual(
            result["changed_prediction_sample"][0]["new_ids"],
            ["gen_revenue_growth_yoy_hi_long__quarter_63d"],
        )

        conn = sqlite3.connect(db_path)
        stored = conn.execute("SELECT mechanism_ids_json FROM predictions WHERE id='p1'").fetchone()[0]
        obs_count = conn.execute("SELECT COUNT(*) FROM mechanism_observations").fetchone()[0]
        audit_count = conn.execute("SELECT COUNT(*) FROM audits").fetchone()[0]
        conn.close()

        self.assertEqual(stored, '["old_mech"]')
        self.assertEqual(obs_count, 1)
        self.assertEqual(audit_count, 0)

    def test_relink_resolved_execute_rewrites_links_observations_and_audits(self) -> None:
        db_path = self._make_relink_db()
        self.addCleanup(os.unlink, db_path)

        with patch.object(
            link_mechanisms,
            "_backup_ledger",
            return_value={"path": "/tmp/pre-relink.sqlite", "integrity_check": "ok"},
        ):
            result = link_mechanisms.relink_resolved(dry_run=False, execute=True, db_path=db_path)

        self.assertEqual(result["changed_predictions"], 1)
        self.assertEqual(result["apply"]["obs_deleted"], 1)
        self.assertEqual(result["apply"]["obs_inserted"], 1)
        self.assertEqual(result["apply"]["prediction_audit_count"], 1)
        self.assertEqual(result["apply"]["backup_audit_id"][:6], "AUDIT-")

        conn = sqlite3.connect(db_path)
        links = conn.execute("SELECT mechanism_ids_json FROM predictions WHERE id='p1'").fetchone()[0]
        obs = conn.execute(
            "SELECT mechanism_id, outcome, experiment_id FROM mechanism_observations WHERE source_id='p1'"
        ).fetchall()
        actions = [r[0] for r in conn.execute("SELECT action FROM audits ORDER BY action").fetchall()]
        conn.close()

        self.assertEqual(
            [item["id"] for item in json.loads(links)],
            ["gen_revenue_growth_yoy_hi_long__quarter_63d"],
        )
        self.assertEqual(obs, [("gen_revenue_growth_yoy_hi_long__quarter_63d", "hit", link_mechanisms.RELINK_PROPOSAL_ID)])
        self.assertEqual(
            actions,
            ["ledger_backup", "relink_resolved", "relink_resolved_prediction"],
        )


if __name__ == "__main__":
    unittest.main()
