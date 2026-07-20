import sqlite3
import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
