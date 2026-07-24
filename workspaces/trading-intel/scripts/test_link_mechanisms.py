import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import link_mechanisms


class LinkMechanismsTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
