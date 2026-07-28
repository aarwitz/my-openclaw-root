import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brier_contributors


class BrierContributorTests(unittest.TestCase):
    def test_rank_contributors_surfaces_overall_and_linked_worst(self) -> None:
        rows = [
            {
                "prediction_id": "p1",
                "regime_at_prediction": "neutral",
                "horizon": "position_1_4w",
                "brier_component": 0.25,
                "mechanism_ids": [],
            },
            {
                "prediction_id": "p2",
                "regime_at_prediction": "neutral",
                "horizon": "position_1_4w",
                "brier_component": 0.25,
                "mechanism_ids": [],
            },
            {
                "prediction_id": "p3",
                "regime_at_prediction": "neutral",
                "horizon": "position_1_4w",
                "brier_component": 0.31,
                "mechanism_ids": ["mech_a"],
            },
            {
                "prediction_id": "p4",
                "regime_at_prediction": "neutral",
                "horizon": "position_1_4w",
                "brier_component": 0.18,
                "mechanism_ids": ["mech_a"],
            },
            {
                "prediction_id": "p5",
                "regime_at_prediction": "neutral",
                "horizon": "position_1_4w",
                "brier_component": 0.28,
                "mechanism_ids": ["mech_b"],
            },
        ]

        ranked = brier_contributors.rank_contributors(rows)
        self.assertEqual(ranked[0]["mechanism"], "(none)")
        self.assertEqual(ranked[0]["total_brier"], 0.5)

        worst_linked = next(item for item in ranked if item["mechanism"] != "(none)")
        self.assertEqual(worst_linked["mechanism"], "mech_a")
        self.assertEqual(worst_linked["count"], 2)
        self.assertEqual(worst_linked["total_brier"], 0.49)

    def test_relink_replay_reports_changed_links_and_mechanism_counts(self) -> None:
        rows = [
            {
                "prediction_id": "p1",
                "hypothesis_id": "h1",
                "hypothesis_created_at": "2026-07-01T00:00:00Z",
                "thesis_summary": "growth thesis",
                "time_horizon": "position_1_4w",
                "hypothesis_state": "resolved",
                "tickers": '["ABC"]',
                "regime_at_prediction": "neutral",
                "mechanism_ids": ["multi_growth_mom__month_21d"],
                "realized_outcome": "incorrect",
            },
        ]

        def fake_prediction(conn, hyp, mech_ids, mechs, regime, **kwargs):
            return {"p_correct": 0.5 if not mech_ids else 0.8}

        with (
            patch.object(brier_contributors.link_mechanisms, "hypothesis_text", return_value="growth thesis"),
            patch.object(brier_contributors.link_mechanisms, "link", return_value=[]),
            patch.object(brier_contributors.predict, "build_prediction", side_effect=fake_prediction),
        ):
            report = brier_contributors.replay_mean_brier(
                sqlite3.connect(":memory:"),
                rows,
                {"multi_growth_mom__month_21d": {"id": "multi_growth_mom__month_21d"}},
                prefer_horizon=True,
                family_mode="root",
                relink=True,
            )

        self.assertEqual(report["changed_links"], 1)
        self.assertEqual(report["mean_brier"], 0.25)
        self.assertEqual(report["mechanism_counts"], {"(none)": 1})

    def test_diagnose_next_blocker_resolved_history_relinking(self) -> None:
        report = {
            "actual_mean_brier": 0.253884,
            "replay": {
                "current_linker_replay": {
                    "mean_brier": 0.242734,
                    "changed_links": 88,
                },
            },
        }

        blocker = brier_contributors.diagnose_next_blocker(report)

        self.assertEqual(blocker["kind"], "resolved_history_relinking")
        self.assertEqual(blocker["actual_mean_brier"], 0.253884)
        self.assertEqual(blocker["current_linker_replay_mean_brier"], 0.242734)
        self.assertEqual(blocker["changed_links"], 88)

    def test_diagnose_next_blocker_current_linker_behavior(self) -> None:
        report = {
            "actual_mean_brier": 0.253884,
            "replay": {
                "current_linker_replay": {
                    "mean_brier": 0.254,
                    "changed_links": 12,
                },
            },
        }

        blocker = brier_contributors.diagnose_next_blocker(report)

        self.assertEqual(blocker["kind"], "current_linker_behavior")


if __name__ == "__main__":
    unittest.main()
