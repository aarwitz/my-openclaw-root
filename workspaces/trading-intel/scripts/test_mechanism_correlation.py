#!/usr/bin/env python3
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

import mechanism_correlation


class MechanismCorrelationReportTests(unittest.TestCase):
    def test_single_cross_sectional_survivor_allows_nullable_statistics(self) -> None:
        cells = [{
            "id": "xs_filing_delta_hi", "horizon": "month_21d", "direction": "long",
            "conds": [["filing_delta", ">", 0.0]], "net_alpha": 1.489, "post": None,
        }]
        clusters = {("month_21d", "long"): [{"cid": 0, "members": [0], "size": 1}]}
        with redirect_stdout(io.StringIO()) as output:
            mechanism_correlation._report(
                cells, clusters, {0: (0, 1, 1.0)}, {"xs_filing_delta_hi": {1, 2}},
                1.0, 1, 1,
            )
        text = output.getvalue()
        self.assertIn("net_alpha= 1.489%", text)
        self.assertIn("post=     -", text)


if __name__ == "__main__":
    unittest.main()
