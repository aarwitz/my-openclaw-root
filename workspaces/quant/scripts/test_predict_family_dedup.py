import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import predict


class PredictFamilyDedupTests(unittest.TestCase):
    def test_root_family_dedup_collapses_mirrored_generated_variants(self) -> None:
        mechs = {
            "multi_revenue_growth_yoy_hi_mom_12_1_hi__month_21d": {
                "id": "multi_revenue_growth_yoy_hi_mom_12_1_hi__month_21d",
                "antecedent_class": "growth_strong",
                "consequent_class": "price_outperform",
                "direction": "long",
                "horizon": "position_1_4w",
                "posterior_mean": 0.61,
                "ci_low": 0.52,
                "ci_high": 0.70,
                "status": "active",
                "n_obs": 24.0,
            },
            "multi_revenue_growth_yoy_lo_mom_12_1_hi__month_21d": {
                "id": "multi_revenue_growth_yoy_lo_mom_12_1_hi__month_21d",
                "antecedent_class": "growth_weak",
                "consequent_class": "price_outperform",
                "direction": "long",
                "horizon": "position_1_4w",
                "posterior_mean": 0.58,
                "ci_low": 0.49,
                "ci_high": 0.67,
                "status": "active",
                "n_obs": 18.0,
            },
        }
        mech_ids = list(mechs.keys())

        legacy_terms, legacy_used, _ = predict._family_terms(
            mech_ids,
            mechs,
            thesis_dir="long",
            horizon="position_1_4w",
            prefer_horizon=True,
            family_mode="legacy_class",
        )
        root_terms, root_used, _ = predict._family_terms(
            mech_ids,
            mechs,
            thesis_dir="long",
            horizon="position_1_4w",
            prefer_horizon=True,
            family_mode="root",
        )

        self.assertEqual(len(legacy_terms), 2)
        self.assertEqual(set(legacy_used), set(mech_ids))
        self.assertEqual(len(root_terms), 1)
        self.assertEqual(root_used, ["multi_revenue_growth_yoy_hi_mom_12_1_hi__month_21d"])


if __name__ == "__main__":
    unittest.main()
