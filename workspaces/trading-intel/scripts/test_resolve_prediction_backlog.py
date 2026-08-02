import sqlite3
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import resolve_prediction_backlog as resolver


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE predictions (id TEXT PRIMARY KEY, hypothesis_id TEXT, predicted_at TEXT, horizon TEXT, "
        "p_correct REAL, mechanism_ids_json TEXT, regime_at_prediction TEXT, experiment_id TEXT, "
        "realized_outcome TEXT, realized_return_pct REAL, realized_excess_pct REAL, brier_component REAL, "
        "resolved_at TEXT, thesis_direction TEXT)"
    )
    conn.execute(
        "CREATE TABLE hypotheses (id TEXT PRIMARY KEY, tickers TEXT, thesis_summary TEXT)"
    )
    conn.execute(
        "CREATE TABLE audits (id TEXT PRIMARY KEY, timestamp TEXT, actor TEXT, entity_type TEXT, entity_id TEXT, "
        "action TEXT, before_state TEXT, after_state TEXT, rationale_concise TEXT, journal_ref TEXT, experiment_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE mechanism_observations (id TEXT PRIMARY KEY, mechanism_id TEXT, observed_at TEXT, "
        "source_type TEXT, source_id TEXT, outcome TEXT, weight REAL, regime_at_obs TEXT, notes TEXT, experiment_id TEXT)"
    )
    return conn


class ResolvePredictionBacklogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 20, 16, 0, 0, tzinfo=timezone.utc)

    def test_resolves_matured_prediction_and_emits_observations(self) -> None:
        conn = _conn()
        conn.execute(
            "INSERT INTO hypotheses VALUES ('h1', '[\"ABC\"]', 'Long ABC on durable catalyst')"
        )
        conn.execute(
            "INSERT INTO predictions VALUES ('p1', 'h1', '2026-06-01T12:00:00Z', 'intraday', 0.8, "
            "'[{\"id\":\"mech-1\",\"align\":1},{\"id\":\"mech-2\",\"align\":-1}]', 'neutral', 'exp-1', NULL, NULL, NULL, NULL, NULL, 'long')"
        )

        def prices(symbol: str) -> list[dict]:
            data = {
                "ABC": [{"t": "2026-06-01", "c": 100.0}, {"t": "2026-06-02", "c": 110.0}],
                "SPY": [{"t": "2026-06-01", "c": 100.0}, {"t": "2026-06-02", "c": 104.0}],
            }
            return data[symbol]

        result = resolver.resolve_prediction_backlog(conn, now=self.now, price_loader=prices)
        row = conn.execute("SELECT realized_outcome, realized_return_pct, realized_excess_pct, brier_component, resolved_at FROM predictions WHERE id='p1'").fetchone()
        self.assertEqual(result["resolved"], 1)
        self.assertEqual(row["realized_outcome"], "correct")
        self.assertEqual(row["realized_return_pct"], 10.0)
        self.assertEqual(row["realized_excess_pct"], 6.0)
        self.assertAlmostEqual(row["brier_component"], 0.04)
        self.assertEqual(row["resolved_at"], "2026-06-02T00:00:00Z")
        obs = conn.execute(
            "SELECT mechanism_id, outcome, weight FROM mechanism_observations ORDER BY mechanism_id"
        ).fetchall()
        self.assertEqual(
            [(r["mechanism_id"], r["outcome"], r["weight"]) for r in obs],
            [("mech-1", "hit", 0.5), ("mech-2", "miss", 0.5)],
        )
        audit = conn.execute("SELECT action, after_state FROM audits WHERE entity_id='p1'").fetchone()
        self.assertEqual((audit["action"], audit["after_state"]), ("resolve_prediction", "correct"))

    def test_marks_deadband_prediction_inconclusive_without_observations(self) -> None:
        conn = _conn()
        conn.execute(
            "INSERT INTO hypotheses VALUES ('h2', '[\"ABC\"]', 'Long ABC with low edge')"
        )
        conn.execute(
            "INSERT INTO predictions VALUES ('p2', 'h2', '2026-06-01T12:00:00Z', 'intraday', 0.6, "
            "'[\"mech-1\"]', 'neutral', 'exp-2', NULL, NULL, NULL, NULL, NULL, 'long')"
        )

        def prices(symbol: str) -> list[dict]:
            data = {
                "ABC": [{"t": "2026-06-01", "c": 100.0}, {"t": "2026-06-02", "c": 100.4}],
                "SPY": [{"t": "2026-06-01", "c": 100.0}, {"t": "2026-06-02", "c": 100.0}],
            }
            return data[symbol]

        result = resolver.resolve_prediction_backlog(conn, now=self.now, price_loader=prices)
        row = conn.execute("SELECT realized_outcome, brier_component FROM predictions WHERE id='p2'").fetchone()
        obs_count = conn.execute("SELECT COUNT(*) FROM mechanism_observations").fetchone()[0]
        self.assertEqual(result["inconclusive"], 1)
        self.assertEqual(row["realized_outcome"], "inconclusive")
        self.assertIsNone(row["brier_component"])
        self.assertEqual(obs_count, 0)

    def test_frozen_prediction_direction_survives_later_hypothesis_edit(self) -> None:
        conn = _conn()
        conn.execute(
            "INSERT INTO hypotheses VALUES ('h-direction', '[\"ABC\"]', 'Long ABC after later edit')"
        )
        conn.execute(
            "INSERT INTO predictions VALUES ('p-direction', 'h-direction', "
            "'2026-06-01T12:00:00Z', 'intraday', 0.7, '[]', 'neutral', 'exp', "
            "NULL, NULL, NULL, NULL, NULL, 'short')"
        )

        def prices(symbol: str) -> list[dict]:
            data = {
                "ABC": [{"t": "2026-06-01", "c": 100.0}, {"t": "2026-06-02", "c": 94.0}],
                "SPY": [{"t": "2026-06-01", "c": 100.0}, {"t": "2026-06-02", "c": 100.0}],
            }
            return data[symbol]

        resolver.resolve_prediction_backlog(conn, now=self.now, price_loader=prices)
        row = conn.execute(
            "SELECT realized_outcome,realized_excess_pct FROM predictions WHERE id='p-direction'"
        ).fetchone()
        self.assertEqual(row["realized_outcome"], "correct")
        self.assertEqual(row["realized_excess_pct"], -6.0)

    def test_missing_price_window_stays_open_and_visible(self) -> None:
        conn = _conn()
        conn.execute(
            "INSERT INTO hypotheses VALUES ('h3', '[\"ABC\"]', 'Long ABC but no prices')"
        )
        conn.execute(
            "INSERT INTO predictions VALUES ('p3', 'h3', '2026-06-01T12:00:00Z', 'intraday', 0.7, "
            "'[\"mech-1\"]', 'neutral', 'exp-3', NULL, NULL, NULL, NULL, NULL, 'long')"
        )

        def prices(symbol: str) -> list[dict]:
            if symbol == "SPY":
                return [{"t": "2026-06-01", "c": 100.0}, {"t": "2026-06-02", "c": 101.0}]
            return [{"t": "2026-06-01", "c": 100.0}]

        first = resolver.resolve_prediction_backlog(conn, now=self.now, price_loader=prices)
        second = resolver.resolve_prediction_backlog(conn, now=self.now, price_loader=prices)
        row = conn.execute("SELECT realized_outcome, resolved_at FROM predictions WHERE id='p3'").fetchone()
        audits = conn.execute("SELECT COUNT(*) FROM audits WHERE entity_id='p3'").fetchone()[0]
        self.assertEqual(first["data_blocked"], 1)
        self.assertEqual(second["expired"], 0)
        self.assertEqual(second["data_blocked"], 1)
        self.assertEqual(second["matured"], 1)
        self.assertIsNone(row["realized_outcome"])
        self.assertIsNone(row["resolved_at"])
        self.assertEqual(audits, 0)


if __name__ == "__main__":
    unittest.main()
