import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import predict


class PredictHorizonPreferenceTests(unittest.TestCase):
    def test_family_selector_prefers_exact_horizon_before_observation_count(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE hypothesis_evidence (hypothesis_id TEXT, signal_type TEXT, source_url TEXT, retrieved_at TEXT)"
        )

        mechs = {
            "growth_month": {
                "id": "growth_month",
                "name": "growth month",
                "antecedent_class": "revenue growth sales growth",
                "consequent_class": "growth continuation",
                "direction": "long",
                "horizon": "position_1_4w",
                "posterior_mean": 0.58,
                "ci_low": 0.50,
                "ci_high": 0.62,
                "status": "active",
                "n_obs": 8.0,
            },
            "growth_quarter": {
                "id": "growth_quarter",
                "name": "growth quarter",
                "antecedent_class": "revenue growth sales growth",
                "consequent_class": "growth continuation",
                "direction": "long",
                "horizon": "trend_1_3m",
                "posterior_mean": 0.61,
                "ci_low": 0.52,
                "ci_high": 0.65,
                "status": "active",
                "n_obs": 30.0,
            },
        }
        hyp = ("hyp-1", "2026-07-20T00:00:00Z", "Long TEST: revenue growth continues", "position_1_4w", "scored", '["TEST"]')

        baseline = predict.build_prediction(
            conn, hyp, ["growth_month", "growth_quarter"], mechs, "neutral", prefer_horizon=False
        )
        fixed = predict.build_prediction(
            conn, hyp, ["growth_month", "growth_quarter"], mechs, "neutral", prefer_horizon=True
        )

        self.assertEqual(baseline["mechanism_ids"], ["growth_quarter"])
        self.assertEqual(fixed["mechanism_ids"], ["growth_month"])


if __name__ == "__main__":
    unittest.main()
