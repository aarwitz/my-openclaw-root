#!/usr/bin/env python3
"""Regression tests for the cross-component failures found in the 2026-07-29 audit."""

from __future__ import annotations

import os
import io
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path("/home/aaron/.openclaw")
os.environ.setdefault("OPENCLAW_RUN_WITH_TRACE", "1")
sys.path.insert(0, str(ROOT / "workspaces/trading-intel/scripts"))
sys.path.insert(0, str(ROOT / "workspaces/quant/scripts"))
sys.path.insert(0, str(ROOT / "workspaces/trader/scripts"))
sys.path.insert(0, str(ROOT / "workspaces/executor/scripts"))
sys.path.insert(0, str(ROOT / "workspaces/archivist/scripts"))
sys.path.insert(0, str(ROOT / "workspaces/developer/scripts"))

import gate_evaluator  # noqa: E402
import promote_mechanisms  # noqa: E402
import signal_scan  # noqa: E402
import author_intents  # noqa: E402
import broker  # noqa: E402
import reconcile  # noqa: E402
import sim_broker  # noqa: E402
import write_postmortems  # noqa: E402
import worldmodel  # noqa: E402
import symbol_lifecycle  # noqa: E402
import mechanism_backtest  # noqa: E402
import mechanism_correlation  # noqa: E402
import integrate_calibrated  # noqa: E402
import causal_graph  # noqa: E402
import feature_store  # noqa: E402
import valuation  # noqa: E402
from connectors import edgar  # noqa: E402
from connectors import _http as connector_http  # noqa: E402
import integrity_check  # noqa: E402
import hypothesis_hygiene  # noqa: E402
import sync_symbol_aliases  # noqa: E402
import market_debrief  # noqa: E402
import macro_calendar  # noqa: E402
import capital_efficiency_audit  # noqa: E402
import compute_attribution  # noqa: E402
from connectors import marketdata  # noqa: E402
from connectors import massive  # noqa: E402

_launcher_spec = importlib.util.spec_from_file_location(
    "dwight_launch_from_issue", ROOT / "scripts/dwight-launch-from-issue.py"
)
assert _launcher_spec and _launcher_spec.loader
dwight_launch_from_issue = importlib.util.module_from_spec(_launcher_spec)
_launcher_spec.loader.exec_module(dwight_launch_from_issue)

_pq_poller_spec = importlib.util.spec_from_file_location(
    "poll_priority_queue", ROOT / "workspaces/dwight/scripts/poll_priority_queue.py"
)
assert _pq_poller_spec and _pq_poller_spec.loader
poll_priority_queue = importlib.util.module_from_spec(_pq_poller_spec)
_pq_poller_spec.loader.exec_module(poll_priority_queue)

_pq_groom_spec = importlib.util.spec_from_file_location(
    "pq_groom", ROOT / "workspaces/dwight/scripts/pq_groom.py"
)
assert _pq_groom_spec and _pq_groom_spec.loader
pq_groom = importlib.util.module_from_spec(_pq_groom_spec)
_pq_groom_spec.loader.exec_module(pq_groom)

_health_spec = importlib.util.spec_from_file_location(
    "system_health_sweep", ROOT / "scripts/system-health-sweep.py"
)
assert _health_spec and _health_spec.loader
system_health_sweep = importlib.util.module_from_spec(_health_spec)
_health_spec.loader.exec_module(system_health_sweep)


def _iso(minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scratch() -> tuple[sqlite3.Connection, str]:
    fd, path = tempfile.mkstemp(suffix=".sqlite", dir="/tmp")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "workspaces/trading-intel/sql/schema.sql").read_text())
    return conn, path


class SignalSafetyTests(unittest.TestCase):
    def test_priority_queue_poller_loads_unattended_session_token(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".json", dir="/tmp")
        os.close(fd)
        try:
            Path(path).write_text(json.dumps({"session_token": "service-token"}))
            with mock.patch.dict(
                os.environ,
                {"TASK_MANAGER_BEARER_TOKEN": "", "TM_BEARER_TOKEN": ""},
                clear=False,
            ):
                self.assertEqual(
                    poll_priority_queue.tm_bearer_token(Path(path)),
                    "service-token",
                )
        finally:
            os.unlink(path)

    def test_priority_queue_grooming_keeps_newest_family_observation(self) -> None:
        rows = {
            "old": {
                "id": "old", "status": "open", "task_id": None,
                "claimed_by": None, "submitted_at": "2026-07-01T00:00:00Z",
                "title": "Fix Telegram target mismatch",
            },
            "new": {
                "id": "new", "status": "open", "task_id": None,
                "claimed_by": None, "submitted_at": "2026-07-02T00:00:00Z",
                "title": "Cron Telegram group route mismatch",
            },
            "idea": {
                "id": "idea", "status": "open", "task_id": None,
                "claimed_by": None, "submitted_at": "2026-07-01T00:00:00Z",
                "title": "Research a durable new signal",
            },
        }
        changes = pq_groom.plan(rows)
        self.assertEqual([row["id"] for row in changes], ["old"])
        self.assertEqual(changes[0]["status"], "superseded")
        self.assertEqual(changes[0]["superseded_by"], "new")
        self.assertTrue(pq_groom.eligible(rows["new"]))
        self.assertTrue(pq_groom.eligible(rows["idea"]))

    def test_connector_errors_redact_credential_query_values(self) -> None:
        redacted = connector_http._redacted_url(
            "https://example.test/data?symbol=ABC&apikey=topsecret&token=alsosecret"
        )
        self.assertIn("symbol=ABC", redacted)
        self.assertNotIn("topsecret", redacted)
        self.assertNotIn("alsosecret", redacted)
        self.assertEqual(redacted.count("REDACTED"), 2)

    def test_edgar_uses_the_canonical_connector_error_class(self) -> None:
        self.assertIs(edgar.ConnectorError, connector_http.ConnectorError)
        with mock.patch.object(
            edgar, "_facts", side_effect=connector_http.ConnectorError("offline")
        ):
            with mock.patch.object(valuation, "price_history", return_value=[100.0] * 260):
                result = valuation.value("TEST")
        self.assertFalse(result["applicable"])
        self.assertEqual(result["reason"], "offline")

    def test_share_count_rejects_stale_and_thousands_scale_facts(self) -> None:
        shares, source = edgar._select_share_count(
            {"end": "2026-04-30", "val": 44_933},
            {"end": "2026-06-15", "val": 44_727_068},
            "2026-04-30",
        )
        self.assertEqual(shares, 44_727_068)
        self.assertEqual(source, "cover_instant_unit_repair")
        shares, source = edgar._select_share_count(
            None,
            {"end": "2010-01-27", "val": 469_280_842},
            "2025-09-30",
        )
        self.assertIsNone(shares)
        self.assertEqual(source, "missing_or_stale")

    def test_market_cap_share_crosscheck_repairs_large_denominator_error(self) -> None:
        shares, source, repaired = valuation._reconcile_share_count(
            469_280_842, 1_750_000_000, "cover_instant"
        )
        self.assertEqual(shares, 1_750_000_000)
        self.assertEqual(source, "universe_market_cap_override")
        self.assertTrue(repaired)
        shares, source, repaired = valuation._reconcile_share_count(
            44_727_068, 45_000_000, "cover_instant_unit_repair"
        )
        self.assertEqual(shares, 44_727_068)
        self.assertFalse(repaired)

    def test_valuation_universe_is_one_atomic_asof_snapshot(self) -> None:
        conn, path = _scratch()
        conn.executescript(
            (ROOT / "workspaces/trading-intel/sql/migrations/0011_valuations.sql").read_text()
        )
        conn.close()
        try:
            def fake_value(ticker: str) -> dict:
                return {
                    "ticker": ticker,
                    "applicable": True,
                    "price": 10.0,
                    "fair_value": 12.0,
                    "margin_of_safety": 0.2,
                    "zone": "fair",
                    "confidence": 0.5,
                }

            with mock.patch.object(valuation, "DB_PATH", Path(path)), \
                    mock.patch.object(valuation, "value", side_effect=fake_value), \
                    mock.patch.object(
                        valuation.edgar,
                        "now_iso",
                        side_effect=[
                            "2026-07-31T13:00:00Z",
                            "2026-07-31T13:00:01Z",
                            "2026-07-31T13:00:02Z",
                            "2026-07-31T13:00:03Z",
                            "2026-07-31T13:00:04Z",
                        ],
                    ):
                report = valuation.value_universe(["AAA", "BBB"])

            check = sqlite3.connect(path)
            snapshots = check.execute(
                "SELECT as_of,COUNT(*) FROM valuations GROUP BY as_of"
            ).fetchall()
            check.close()
            self.assertEqual(report["as_of"], "2026-07-31T13:00:00Z")
            self.assertEqual(snapshots, [("2026-07-31T13:00:00Z", 2)])
        finally:
            os.unlink(path)

    def test_database_enforces_one_live_hypothesis_per_ticker(self) -> None:
        conn, path = _scratch()
        try:
            conn.execute(
                "INSERT INTO hypotheses(id,created_at,created_by,tickers,thesis_summary,state) "
                "VALUES('h1',?,'researcher','[\"ABC\"]','first','ready')",
                (_iso(),),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO hypotheses(id,created_at,created_by,tickers,thesis_summary,state) "
                    "VALUES('h2',?,'researcher','[\"ABC\"]','duplicate','raw')",
                    (_iso(),),
                )
            conn.execute(
                "INSERT INTO hypotheses(id,created_at,created_by,tickers,thesis_summary,state) "
                "VALUES('h3',?,'researcher','[\"ABC\"]','history','dormant')",
                (_iso(),),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE hypotheses SET state='scored' WHERE id='h3'")
        finally:
            conn.close()
            os.unlink(path)

    def test_hypothesis_hygiene_reopens_positions_and_dormants_noise(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE hypotheses(
              id TEXT PRIMARY KEY,tickers TEXT,state TEXT,created_at TEXT,
              resolved_at TEXT,resolved_state TEXT,archivist_grade TEXT
            );
            CREATE TABLE positions(id TEXT,hypothesis_id TEXT,ticker TEXT,state TEXT);
            CREATE TABLE trade_intents(hypothesis_id TEXT,state TEXT);
            CREATE TABLE audits(
              id TEXT PRIMARY KEY,timestamp TEXT,actor TEXT,entity_type TEXT,
              entity_id TEXT,action TEXT,before_state TEXT,after_state TEXT,
              rationale_concise TEXT
            );
            """
        )
        old = "2026-01-01T00:00:00Z"
        conn.executemany(
            "INSERT INTO hypotheses VALUES(?,?,?,?,?,?,?)",
            [
                ("held", '["ABC"]', "resolved", old, old, "correct_right_reasons", "old grade"),
                ("duplicate", '["ABC"]', "ready", old, None, None, None),
                ("orphan", '["XYZ"]', "active", old, None, None, None),
                ("clean", '["NEW"]', "ready", old, None, None, None),
            ],
        )
        conn.execute("INSERT INTO positions VALUES('p','held','ABC','open')")
        report = hypothesis_hygiene.inspect_and_repair(
            conn, repair=True, grace_hours=0
        )
        self.assertEqual(report["reopened_position_theses"], ["held"])
        states = dict(conn.execute("SELECT id,state FROM hypotheses"))
        self.assertEqual(states["held"], "active")
        self.assertEqual(states["duplicate"], "dormant")
        self.assertEqual(states["orphan"], "dormant")
        self.assertEqual(states["clean"], "ready")
        self.assertIsNone(
            conn.execute("SELECT resolved_at FROM hypotheses WHERE id='held'").fetchone()[0]
        )
        conn.close()

    def test_open_risk_requires_robust_mechanism_provenance(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE mechanisms(id TEXT,status TEXT,notes TEXT)")
        conn.execute("INSERT INTO mechanisms VALUES('legacy','active',NULL)")
        self.assertEqual(author_intents._robust_active_edge_count(conn), 0)
        conn.execute(
            "INSERT INTO mechanisms VALUES('robust','active',?)",
            (json.dumps({"calibrated": True, "bonferroni": True}),),
        )
        self.assertEqual(author_intents._robust_active_edge_count(conn), 1)
        conn.close()

    def test_unknown_cross_sectional_operators_fail_closed(self) -> None:
        feats = {"news": (1.0, "2026-07-29")}
        self.assertFalse(signal_scan.cond_holds([["news", "hi", 0.2]], feats))
        self.assertFalse(signal_scan.cond_holds([["news", "lo", 0.2]], feats))
        self.assertTrue(signal_scan.cond_holds([["news", ">", 0.2]], feats))

    def test_probability_bootstrap_uses_hit_rate_not_alpha(self) -> None:
        self.assertIsNone(promote_mechanisms.posterior(None, 1000))
        self.assertAlmostEqual(promote_mechanisms.posterior(0.50, 1000), 0.50, places=3)
        self.assertGreater(promote_mechanisms.posterior(0.60, 1000), 0.58)

    def test_correlation_report_accepts_missing_probability_posterior(self) -> None:
        cells = [{
            "id": "xs_filing_delta_hi",
            "horizon": "month_21d",
            "direction": "long",
            "conds": [["filing_delta", "hi", 0.2]],
            "net_alpha": 1.489,
            "post": None,
        }]
        clusters = {
            ("month_21d", "long"): [{"cid": 0, "members": [0], "size": 1}]
        }
        out = io.StringIO()
        with redirect_stdout(out):
            mechanism_correlation._report(
                cells, clusters, {0: (0, 1, 1.0)},
                {"xs_filing_delta_hi": {1, 2}}, 1.0, 1, 2,
            )
        self.assertIn("post=     -", out.getvalue())
        self.assertIn("net_alpha= 1.489%", out.getvalue())

    def test_development_cross_sectional_artifact_is_not_live_eligible(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE discovered_mechanisms(evaluation_label TEXT);
            INSERT INTO discovered_mechanisms VALUES('development_reused_holdout');
            CREATE TABLE calibrated_mechanisms(
              id TEXT, source TEXT, bonf_sig INT, posterior_mean REAL,
              net_alpha_pct REAL
            );
            INSERT INTO calibrated_mechanisms
            VALUES('xs_filing_delta_hi','cross',1,NULL,1.489);
            """
        )
        report = integrate_calibrated.integration_eligibility(conn)
        self.assertFalse(report["eligible"])
        self.assertEqual(report["calibrated_count"], 1)
        self.assertEqual(report["eligible_survivors"], [])
        self.assertIn("development/reused holdout provenance", report["blockers"])
        conn.close()

    def test_backtest_inference_uses_date_clusters_and_hac(self) -> None:
        mean, p_value = mechanism_backtest._hac_mean_p(
            [0.01, 0.02, -0.01, 0.00, 0.01], 2
        )
        self.assertAlmostEqual(mean, 0.006)
        self.assertGreaterEqual(p_value, 0.0)
        self.assertLessEqual(p_value, 1.0)
        source = (ROOT / "workspaces/trading-intel/scripts/mechanism_backtest.py").read_text()
        self.assertIn("test_date_clusters", source)
        self.assertIn("cluster_n >= 30", source)
        self.assertIn("ticker_n >= 20", source)

    def test_latest_features_returns_latest_value_per_name(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE features(ticker TEXT,name TEXT,as_of TEXT,value REAL)")
        conn.execute("CREATE INDEX idx_features ON features(ticker,name,as_of)")
        conn.executemany(
            "INSERT INTO features VALUES(?,?,?,?)",
            [("T", "x", "2026-01-01", 1.0), ("T", "x", "2026-01-02", 2.0),
             ("T", "y", "2026-01-01", 3.0)],
        )
        self.assertEqual(signal_scan.latest_features(conn, "T")["x"], (2.0, "2026-01-02"))

    def test_one_prediction_has_one_total_unit_of_learning_credit(self) -> None:
        allocated = worldmodel.allocate_prediction_credit([
            {"id": "a", "align": 1},
            {"id": "b", "align": -1},
            {"id": "a", "align": 1},
        ])
        self.assertEqual([item["id"] for item, _ in allocated], ["a", "b"])
        self.assertAlmostEqual(sum(weight for _, weight in allocated), 1.0)

    def test_live_symbols_are_canonical_and_stale_features_fail_closed(self) -> None:
        self.assertEqual(symbol_lifecycle.canonical_symbol("LAAC", "2026-07-30"), "LAR")
        self.assertEqual(symbol_lifecycle.canonical_symbol("LAAC", "2024-12-31"), "LAAC")
        today = datetime(2026, 7, 30).date()
        self.assertFalse(symbol_lifecycle.is_live_feature_fresh(
            "2026-06-18", today=today
        ))
        self.assertTrue(symbol_lifecycle.is_live_feature_fresh(
            "2026-07-29", today=today
        ))

    def test_symbol_alias_sync_is_idempotent_after_redirect(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".sqlite", dir="/tmp")
        os.close(fd)
        try:
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE universe(symbol TEXT PRIMARY KEY,market_cap REAL,"
                "sector TEXT,status TEXT,ipo_date TEXT,delisted_date TEXT)"
            )
            conn.execute(
                "INSERT INTO universe VALUES('LAAC',100,'Materials','active',NULL,NULL)"
            )
            conn.commit()
            conn.close()
            alias = {"old": "LAAC", "new": "LAR", "effective_date": "2025-01-24"}
            with mock.patch.object(sync_symbol_aliases.symbol_lifecycle, "aliases", return_value=(alias,)):
                first = sync_symbol_aliases.sync(Path(path))
                second = sync_symbol_aliases.sync(Path(path))
            self.assertEqual(first["changed"], 1)
            self.assertEqual(second["changed"], 0)
            conn = sqlite3.connect(path)
            self.assertEqual(
                dict(conn.execute("SELECT symbol,status FROM universe")),
                {"LAAC": "renamed", "LAR": "active"},
            )
            conn.close()
        finally:
            os.unlink(path)

    def test_daily_bar_completeness_uses_session_close_not_is_open(self) -> None:
        et = ZoneInfo("America/New_York")
        self.assertFalse(marketdata.daily_bar_complete(
            "2026-07-30", datetime(2026, 7, 30, 8, 0, tzinfo=et)
        ))
        self.assertFalse(marketdata.daily_bar_complete(
            "2026-07-30", datetime(2026, 7, 30, 15, 59, tzinfo=et)
        ))
        self.assertTrue(marketdata.daily_bar_complete(
            "2026-07-30", datetime(2026, 7, 30, 16, 0, tzinfo=et)
        ))
        # Day after Thanksgiving is a 13:00 ET close.
        self.assertFalse(marketdata.daily_bar_complete(
            "2026-11-27", datetime(2026, 11, 27, 12, 59, tzinfo=et)
        ))
        self.assertTrue(marketdata.daily_bar_complete(
            "2026-11-27", datetime(2026, 11, 27, 13, 0, tzinfo=et)
        ))
        self.assertFalse(marketdata.daily_bar_complete(
            "2026-07-25", datetime(2026, 7, 26, 12, 0, tzinfo=et)
        ))

    def test_feature_generation_drops_unfinished_daily_bar(self) -> None:
        bars = [{"t": "2026-07-29", "c": 100}, {"t": "2026-07-30", "c": 101}]
        with mock.patch.object(marketdata, "daily_bar_complete", return_value=False):
            self.assertEqual(feature_store._drop_incomplete_today(bars), bars[:-1])
        with mock.patch.object(marketdata, "daily_bar_complete", return_value=True):
            self.assertEqual(feature_store._drop_incomplete_today(bars), bars)

    def test_live_feature_refresh_bypasses_stale_price_cache(self) -> None:
        bars = [{"t": "2026-07-30", "c": 101, "h": 102, "v": 10}]
        with mock.patch.object(massive, "available", return_value=True), mock.patch.object(
            massive, "daily_bars", return_value=bars
        ) as daily:
            self.assertEqual(feature_store._prices("ABC", 4000, fresh_prices=True), bars)
        daily.assert_called_once_with("ABC", cache_h=0.0)
        source = (ROOT / "workspaces/trading-intel/scripts/feature_store.py").read_text()
        self.assertIn("_ledger_subject_symbols()", source)
        self.assertIn("ledger_subjects +", source)

    def test_partial_bar_integrity_check_only_counts_price_features(self) -> None:
        source = (
            ROOT / "workspaces/trading-intel/scripts/integrity_check.py"
        ).read_text()
        self.assertIn("WHERE source='price' AND as_of>=?", source)

    def test_bulk_snapshot_parses_many_symbols_in_one_bounded_call(self) -> None:
        payload = {
            "tickers": [
                {"ticker": "AAA", "lastTrade": {"p": 10.5}, "updated": 1},
                {"ticker": "BBB", "day": {"c": 20.25}, "updated": 2},
            ]
        }
        with mock.patch.object(massive, "_get", return_value=payload) as get:
            quotes = massive.latest_trades(["BBB", "AAA", "AAA"])
        self.assertEqual(quotes["AAA"]["price"], 10.5)
        self.assertEqual(quotes["BBB"]["price"], 20.25)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.kwargs["retries"], 0)
        self.assertEqual(get.call_args.kwargs["timeout"], 8.0)

    def test_money_path_uses_bulk_or_stored_marks_not_serial_snapshots(self) -> None:
        stop_source = (
            ROOT / "workspaces/trader/scripts/enforce_stops.py"
        ).read_text()
        broker_source = (
            ROOT / "workspaces/executor/scripts/broker.py"
        ).read_text()
        mark_source = (
            ROOT / "workspaces/developer/scripts/mark_positions.py"
        ).read_text()
        sim_source = (
            ROOT / "workspaces/executor/scripts/sim_broker.py"
        ).read_text()
        author_source = (
            ROOT / "workspaces/trader/scripts/author_intents.py"
        ).read_text()
        self.assertIn("quotes = latest_trades(", stop_source)
        self.assertNotIn("lt = latest_trade(tick)", stop_source)
        self.assertIn("quotes = latest_trades(", mark_source)
        self.assertNotIn("latest_trade(r[", mark_source)
        account_body = broker_source.split("def get_account()", 1)[1].split(
            "def list_positions()", 1
        )[0]
        self.assertNotIn("_mark_price", account_body)
        self.assertLess(
            sim_source.index('held[sym].get("current_price")'),
            sim_source.index("massive.cached_daily_close(sym)", sim_source.index("def mark_book")),
        )
        self.assertIn(
            "no robust active mechanisms; open-risk authoring quarantined",
            author_source,
        )

    def test_simulator_mark_atomically_updates_canonical_position_view(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE positions(id TEXT,ticker TEXT,qty REAL,cost_basis REAL,"
            "book TEXT,state TEXT,current_price REAL,current_value REAL,"
            "unrealized_pnl_pct REAL,pnl_ideal REAL,pnl_slippage_adjusted REAL)"
        )
        conn.executemany(
            "INSERT INTO positions(id,ticker,qty,cost_basis,book,state) VALUES(?,?,?,?,?,?)",
            [
                ("long", "AAA", 2, 10, "desk", "open"),
                ("short", "BBB", -3, 20, "desk", "open"),
                ("model", "AAA", 1, 10, "model", "open"),
            ],
        )
        self.assertEqual(
            sim_broker._sync_canonical_marks(conn, "desk", {"AAA": 12, "BBB": 18}),
            2,
        )
        long = conn.execute(
            "SELECT current_price,current_value,unrealized_pnl_pct,pnl_ideal "
            "FROM positions WHERE id='long'"
        ).fetchone()
        short = conn.execute(
            "SELECT current_price,current_value,unrealized_pnl_pct,pnl_ideal "
            "FROM positions WHERE id='short'"
        ).fetchone()
        self.assertEqual(long, (12.0, 24.0, 20.0, 4.0))
        self.assertEqual(short, (18.0, -54.0, 10.0, 6.0))
        self.assertIsNone(
            conn.execute("SELECT current_price FROM positions WHERE id='model'").fetchone()[0]
        )
        conn.close()

    def test_historical_shadow_book_cannot_be_marked_or_narrated(self) -> None:
        with self.assertRaisesRegex(ValueError, "not operational"):
            sim_broker.mark_book(sqlite3.connect(":memory:"), "shadow")
        jobs = json.loads((ROOT / "cron/jobs.json").read_text()).get("jobs", [])
        premarket = next(
            job for job in jobs if job.get("name") == "pre-market-reconcile-0845-et"
        )
        message = str((premarket.get("payload") or {}).get("message", ""))
        self.assertIn("open desk rows in sim_positions", message)
        self.assertIn("Historical `shadow` rows are inert audit artifacts", message)
        self.assertIn("never inspect, reconcile, mark, page, or narrate them", message)

    def test_market_debrief_backfill_uses_event_date_bar(self) -> None:
        bars = [
            {"t": "2026-07-23", "c": 100.0},
            {"t": "2026-07-24", "c": 101.0},
            {"t": "2026-07-27", "c": 90.0},
        ]
        self.assertEqual(market_debrief._move_as_of(bars, "2026-07-24"), 1.0)
        self.assertIsNone(market_debrief._move_as_of(bars, "2026-07-25"))

    def test_macro_calendar_uses_official_dates_not_weekday_approximations(self) -> None:
        self.assertEqual(
            macro_calendar._official_release_day("NFP", 2026, 7).isoformat(),
            "2026-07-02",
        )
        self.assertEqual(
            macro_calendar._official_release_day("CPI_YOY", 2026, 9).isoformat(),
            "2026-09-11",
        )
        self.assertIsNone(macro_calendar._official_release_day("CPI_YOY", 2027, 1))
        source = (ROOT / "workspaces/trading-intel/scripts/macro_calendar.py").read_text()
        self.assertNotIn("_approx_cpi_day", source)
        self.assertNotIn("_first_friday", source)

    def test_protective_health_check_respects_closed_market_deferral(self) -> None:
        source = (
            ROOT / "workspaces/developer/scripts/audit_pipeline_health.py"
        ).read_text()
        stranded_block = source.split("stranded = conn.execute", 1)[1].split(
            "bypass = conn.execute", 1
        )[0]
        self.assertIn('market_clock().get("is_open")', stranded_block)

    def test_pipeline_health_blocks_production_edge_claims_without_corpus(self) -> None:
        source = (
            ROOT / "workspaces/developer/scripts/audit_pipeline_health.py"
        ).read_text()
        self.assertIn('validation.get("post_cutoff", 0)', source)
        self.assertIn('validation.get("negative_control", 0)', source)
        self.assertIn("reasoning_gate=fail", source)
        self.assertIn("internal-paper simulation may continue", source)

    def test_cash_without_validated_edge_is_not_idea_supply_drag(self) -> None:
        self.assertEqual(
            capital_efficiency_audit._classify_idle_cash(50_000.0, 0),
            (0.0, 50_000.0),
        )
        self.assertEqual(
            capital_efficiency_audit._classify_idle_cash(50_000.0, 2),
            (50_000.0, 0.0),
        )

    def test_prospective_edge_matches_live_id_to_artifact_horizon(self) -> None:
        live = sqlite3.connect(":memory:")
        live.execute("CREATE TABLE mechanisms(id TEXT, status TEXT)")
        live.execute(
            "INSERT INTO mechanisms VALUES('quality__month_21d','active')"
        )
        fd, feature_path = tempfile.mkstemp(suffix=".sqlite", dir="/tmp")
        os.close(fd)
        try:
            feature = sqlite3.connect(feature_path)
            feature.execute(
                "CREATE TABLE calibrated_mechanisms("
                "id TEXT,horizon TEXT,net_alpha_pct REAL,bonf_sig INT)"
            )
            feature.execute(
                "INSERT INTO calibrated_mechanisms VALUES("
                "'quality','month_21d',2.5,1)"
            )
            feature.commit()
            feature.close()
            with mock.patch.object(
                capital_efficiency_audit, "FEATURE_DB", Path(feature_path)
            ):
                rate, source, count = (
                    capital_efficiency_audit._prospective_edge_rate(live)
                )
            self.assertEqual(count, 1)
            self.assertEqual(source, "robust_calibrated_mechanism_median")
            self.assertAlmostEqual(rate, 0.025)
        finally:
            live.close()
            os.unlink(feature_path)

    def test_attribution_accepts_nanosecond_timestamps(self) -> None:
        parsed = compute_attribution._parse("2026-07-06T13:38:36.848190044Z")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.microsecond, 848190)

    def test_learning_health_uses_exact_resolver_maturity(self) -> None:
        source = (ROOT / "scripts/system-health-sweep.py").read_text()
        body = source.split("def check_learning_loop()", 1)[1].split(
            "def check_offsite_backup()", 1
        )[0]
        self.assertIn("resolve_prediction_backlog.py", body)
        self.assertIn('"--dry-run"', body)
        self.assertNotIn("horizon_cal", body)
        pipeline_source = (
            ROOT / "workspaces/developer/scripts/audit_pipeline_health.py"
        ).read_text()
        self.assertIn("resolve_prediction_backlog(conn, dry_run=True)", pipeline_source)
        self.assertNotIn("maturity_days =", pipeline_source)

    def test_health_sweep_closes_learning_loop_before_verification(self) -> None:
        sweep = (ROOT / "scripts/sweep-and-page.sh").read_text()
        closer = (ROOT / "scripts/close-matured-predictions.sh").read_text()

        self.assertLess(
            sweep.index("close-matured-predictions.sh"),
            sweep.index("system-health-sweep.py"),
        )
        self.assertIn('"learning_loop_closure"', sweep)
        self.assertIn("PREFLIGHT_RC -ne 0", sweep)
        self.assertIn("grade_outcomes.py", closer)
        self.assertIn("calibrate.py", closer)
        self.assertIn("--no-propose", closer)
        self.assertIn("state/trading-money-path.lock", closer)

    def test_backup_health_uses_mtime_not_mixed_filename_order(self) -> None:
        source = (ROOT / "scripts/system-health-sweep.py").read_text()
        body = source.split("def check_ledger_backup()", 1)[1].split(
            "def check_learning_loop()", 1
        )[0]
        self.assertIn("max(files, key=os.path.getmtime)", body)
        self.assertNotIn("os.path.getmtime(files[-1])", body)

    def test_all_money_path_writers_share_one_lock(self) -> None:
        for relative in (
            "scripts/close-matured-predictions.sh",
            "scripts/trader-pass-deterministic.sh",
            "scripts/guard-pass.sh",
            "scripts/learning-chain.sh",
            "scripts/learning-signals.sh",
        ):
            source = (ROOT / relative).read_text()
            self.assertIn(
                'state/trading-money-path.lock',
                source,
                msg=f"{relative} can race another ledger/feature writer",
            )

    def test_merge_and_nightly_paths_run_the_offline_preflight(self) -> None:
        policy = json.loads((ROOT / "scripts/merge-policy.json").read_text())
        checks = policy["repo_checks"][str(ROOT)]
        self.assertTrue(any("scripts/autotrade-preflight.py" in c for c in checks))
        learning = (ROOT / "scripts/learning-chain.sh").read_text()
        self.assertIn('step "autotrade-preflight"', learning)
        preflight = (ROOT / "scripts/autotrade-preflight.py").read_text()
        for required in (
            "config-json-contract",
            "doc-contract",
            "internal-paper-only",
            "trading-unit-suite",
            "quant-unit-suite",
            "money-path",
        ):
            self.assertIn(required, preflight)

    def test_telegram_authoring_crons_cannot_emit_completion_receipts(self) -> None:
        jobs = json.loads((ROOT / "cron/jobs.json").read_text()).get("jobs", [])
        telegram_jobs = [
            job for job in jobs
            if job.get("enabled")
            and "telegram" in str((job.get("payload") or {}).get("message", "")).lower()
        ]
        self.assertTrue(telegram_jobs)
        for job in telegram_jobs:
            self.assertEqual((job.get("delivery") or {}).get("mode"), "none")
            message = str((job.get("payload") or {}).get("message", ""))
            self.assertIn("FINAL OUTPUT CONTRACT (no duplicate receipts):", message)
            self.assertIn("return exactly SILENT_SUCCESS", message)
            if "[OVERSEER-DRIVE-V3]" in message:
                self.assertIn("group -1003846579956", message)
                self.assertNotIn("-1003237263898", message)

    def test_routine_overseer_crons_cannot_force_research_churn(self) -> None:
        jobs = json.loads((ROOT / "cron/jobs.json").read_text()).get("jobs", [])
        by_name = {str(job.get("name")): job for job in jobs}
        self.assertNotIn("mechanism-proposer-daily", by_name)
        drive_jobs = [
            job for job in jobs
            if job.get("enabled") and "[OVERSEER-DRIVE-V3]" in str((job.get("payload") or {}).get("message", ""))
        ]
        self.assertTrue(drive_jobs)
        for job in drive_jobs:
            name = str(job.get("name"))
            message = str((job.get("payload") or {}).get("message", ""))
            self.assertNotIn("NOT allowed to conclude", message)
            self.assertNotIn("last_researcher_pass_age_min > 360", message)
            if name == "catalyst-research-0830-et":
                self.assertIn("the only routine research spawn", message)
                self.assertIn("UPDATE that thesis", message)
                self.assertIn("at most 5 net-new", message)
                self.assertIn("critic_review_queue.py list --max 10", message)
                self.assertIn("spawn `critic` exactly once", message)
                self.assertIn("critic_review_queue.py finalize --apply", message)
            else:
                self.assertIn("Do not spawn researcher", message)

    def test_data_scout_cannot_dirty_source_control_or_create_proposals(self) -> None:
        jobs = json.loads((ROOT / "cron/jobs.json").read_text()).get("jobs", [])
        scout = next(job for job in jobs if job.get("name") == "data-scout-monthly")
        message = str((scout.get("payload") or {}).get("message", ""))
        self.assertIn("no repository or queue writes", message)
        self.assertIn("Do not edit DATA_SOURCES.md", message)
        self.assertIn("no persistent proposal", message)
        self.assertNotIn("Append a dated 'Scout log'", message)

    def test_live_integrator_has_no_wipe_flag_and_requires_robust_survivors(self) -> None:
        source = (
            ROOT / "workspaces/trading-intel/scripts/integrate_calibrated.py"
        ).read_text()
        self.assertNotIn('add_argument("--reset"', source)
        self.assertNotIn('DELETE FROM mechanisms"', source)
        self.assertIn("bonf_sig=1", source)

    def test_weekly_rediscovery_is_analytics_only_and_creates_no_backup(self) -> None:
        source = (ROOT / "scripts/learning-rediscovery.sh").read_text()
        self.assertNotIn("PRE-REDISCOVERY", source)
        self.assertNotIn('run "backup db"', source)
        self.assertNotIn('run "integrate"', source)
        self.assertIn('integrate_calibrated.py" --check-only', source)
        self.assertIn("live mechanism ledger unchanged", source)

    def test_canonical_architecture_matches_live_contract_boundaries(self) -> None:
        architecture = (ROOT / "SYSTEM_ARCHITECTURE.md").read_text()
        risk_source = (
            ROOT / "workspaces/risk/scripts/gate_risk_intents.py"
        ).read_text()
        self.assertIn("migrations:** through 0025", architecture)
        self.assertIn("Concurrent names:** ≤ 48", architecture)
        self.assertIn("MAX_POSITIONS = 48", risk_source)
        self.assertIn("These are **not one unified graph**", architecture)
        self.assertIn(
            "do not directly contain thesis, prediction,", architecture
        )
        overseer = (ROOT / "workspaces/overseer/AGENTS.md").read_text()
        self.assertIn("“Very concise” means", overseer)
        self.assertIn("at most 100 words", overseer)
        config = json.loads((ROOT / "openclaw.json").read_text())
        telegram = config["channels"]["telegram"]
        topic_prompt = telegram["groups"]["-1003846579956"]["topics"]["641"]["systemPrompt"]
        self.assertIn("answer in <=100 words and stop", topic_prompt)
        self.assertNotIn(
            "researcher -> quant -> critic -> trader -> risk -> executor",
            topic_prompt,
        )

    def test_health_json_parser_tolerates_stderr_after_stdout(self) -> None:
        output = 'connector note\n{"matured": 0, "scanned": 7}\nSPY fallback notice\n'
        self.assertEqual(
            system_health_sweep._first_json_object(output),
            {"matured": 0, "scanned": 7},
        )

    def test_integrity_freshness_counts_sessions_not_weekend_hours(self) -> None:
        friday_mark = "2026-07-31T20:00:00Z"
        sunday = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
        monday_preclose = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
        monday_postclose = datetime(2026, 8, 3, 21, 0, tzinfo=timezone.utc)
        self.assertEqual(
            integrity_check.completed_sessions_since(friday_mark, sunday), 0
        )
        self.assertEqual(
            integrity_check.completed_sessions_since(friday_mark, monday_preclose), 0
        )
        self.assertEqual(
            integrity_check.completed_sessions_since(friday_mark, monday_postclose), 1
        )

    def test_canonical_connection_and_health_enforce_foreign_keys(self) -> None:
        db_helper = (
            ROOT / "workspaces/developer/scripts/developer_db.py"
        ).read_text()
        integrity = (
            ROOT / "workspaces/trading-intel/scripts/integrity_check.py"
        ).read_text()
        self.assertIn("PRAGMA foreign_keys=ON", db_helper)
        self.assertIn("PRAGMA foreign_key_check", integrity)

    def test_research_coverage_rejects_post_event_and_stale_theses(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE market_events(event_date TEXT, observed_moves_json TEXT);
            CREATE TABLE hypotheses(
              id TEXT PRIMARY KEY, tickers TEXT, created_at TEXT,
              thesis_summary TEXT, state TEXT
            );
            CREATE TABLE predictions(
              hypothesis_id TEXT, predicted_at TEXT, return_p50 REAL
            );
            """
        )
        event = datetime.now(timezone.utc).date() - timedelta(days=1)
        event_s = event.isoformat()
        moves = json.dumps({"PRE": 5.0, "POST": -6.0, "STALE": 7.0})
        conn.execute("INSERT INTO market_events VALUES(?,?)", (event_s, moves))
        conn.executemany(
            "INSERT INTO hypotheses VALUES(?,?,?,?,?)",
            [
                ("pre", '["PRE"]', (event - timedelta(days=2)).isoformat(),
                 "Positive setup", "scored"),
                ("post", '["POST"]', event_s, "Bearish after the fall", "scored"),
                ("stale", '["STALE"]', (event - timedelta(days=20)).isoformat(),
                 "Old bullish note", "scored"),
            ],
        )
        conn.execute(
            "INSERT INTO predictions VALUES(?,?,?)",
            ("pre", (event - timedelta(days=2)).isoformat(), 3.0),
        )
        report = integrity_check.research_coverage(conn)
        self.assertEqual(len(report), 1)
        self.assertIn("direction-correct on 1/3", report[0]["detail"])
        self.assertIn("timely pre-event coverage 1/3", report[0]["detail"])


class EvidenceGraphTests(unittest.TestCase):
    def test_corroboration_counts_unique_evidence_not_rebuilds(self) -> None:
        conn = sqlite3.connect(":memory:")
        causal_graph.ensure_schema(conn)
        causal_graph.edge(
            conn, "ticker:a", "ticker:b", "co_moves",
            evidence="corr-window:2026-01", status="association_validated",
            confidence=0.7,
        )
        causal_graph.edge(
            conn, "ticker:a", "ticker:b", "co_moves",
            evidence="corr-window:2026-01", status="association_validated",
            confidence=0.7,
        )
        self.assertEqual(
            conn.execute("SELECT corroboration FROM causal_edges").fetchone()[0],
            1,
        )
        causal_graph.edge(
            conn, "ticker:a", "ticker:b", "co_moves",
            evidence="corr-window:2026-02", status="association_validated",
            confidence=0.8,
        )
        self.assertEqual(
            conn.execute("SELECT corroboration FROM causal_edges").fetchone()[0],
            2,
        )

    def test_why_engine_never_labels_association_as_cause(self) -> None:
        source = (ROOT / "workspaces/trading-intel/scripts/why_engine.py").read_text()
        self.assertNotIn("VALIDATED causes", source)
        self.assertIn("It does not establish causality", source)

    def test_graph_cli_help_does_not_build_and_default_build_is_clean(self) -> None:
        source = (
            ROOT / "workspaces/trading-intel/scripts/causal_graph.py"
        ).read_text()
        self.assertIn('add_subparsers(dest="command", required=True)', source)
        self.assertIn("build(rebuild=not args.incremental)", source)


class IntentSafetyTests(unittest.TestCase):
    def test_missing_or_weak_prediction_cannot_fallback_to_baseline(self) -> None:
        self.assertTrue(author_intents._kelly_sizing(None, 100_000)["skip"])
        weak = {
            "id": "p", "p_correct": 0.50, "return_p10": -5.0,
            "return_p50": 0.2, "return_p90": 5.0,
        }
        self.assertTrue(author_intents._kelly_sizing(weak, 100_000)["skip"])

    def test_positive_prediction_can_size_with_kelly(self) -> None:
        pred = {
            "id": "p", "p_correct": 0.60, "return_p10": -4.0,
            "return_p50": 1.0, "return_p90": 8.0,
        }
        out = author_intents._kelly_sizing(pred, 100_000)
        self.assertFalse(out["skip"])
        self.assertEqual(out["sizing_basis"], "kelly")
        self.assertGreater(out["notional_raw"], 0)

    def test_nonmarketable_sim_limit_is_not_fabricated_as_fill(self) -> None:
        old_conn, old_sim, old_trade = broker._conn, broker._sim, broker._massive.latest_trade

        class FakeSim:
            @staticmethod
            def fill_price(symbol, side, ref):
                return 101.0

        try:
            broker._conn = lambda: object()
            broker._sim = lambda: FakeSim
            broker._massive.latest_trade = lambda symbol: {"price": 100.0}
            with self.assertRaises(broker.ConnectorError):
                broker.place_order("TEST", 1, "buy", order_type="limit", limit_price=100.50)
        finally:
            broker._conn, broker._sim, broker._massive.latest_trade = old_conn, old_sim, old_trade


class ReconciliationExitContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, self.path = _scratch()

    def tearDown(self) -> None:
        self.conn.close()
        os.unlink(self.path)

    def _position(self, ticker: str = "AAA") -> None:
        self.conn.execute(
            "INSERT INTO hypotheses(id,created_at,created_by,tickers,thesis_summary,state) "
            "VALUES('h-rec',?,'researcher',?,'reconcile fixture','active')",
            (_iso(), json.dumps([ticker])),
        )
        self.conn.execute(
            "INSERT INTO positions(id,hypothesis_id,ticker,vehicle,qty,cost_basis,state,opened_at) "
            "VALUES('p-rec','h-rec',?,'direct_equity',2,100,'open',?)",
            (ticker, _iso()),
        )
        self.conn.commit()

    def test_dry_run_returns_nonzero_on_divergence(self) -> None:
        self._position()
        with (
            mock.patch.object(reconcile, "connect", return_value=self.conn),
            mock.patch.object(reconcile, "list_positions", return_value=[]),
            mock.patch.object(reconcile, "list_orders", return_value=[]),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(reconcile.main(["--dry-run"]), 1)

    def test_repair_must_verify_clean_before_returning_success(self) -> None:
        self._position()
        with (
            mock.patch.object(reconcile, "connect", return_value=self.conn),
            mock.patch.object(reconcile, "list_positions", return_value=[]),
            mock.patch.object(reconcile, "list_orders", return_value=[]),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(reconcile.main(["--repair"]), 0)
        row = self.conn.execute(
            "SELECT resolved FROM reconciliation_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row["resolved"], 1)
        self.assertEqual(
            self.conn.execute("SELECT state FROM positions WHERE id='p-rec'").fetchone()[0],
            "closed",
        )

    def test_unrepairable_divergence_remains_failed(self) -> None:
        phantom = {
            "id": "sim-phantom",
            "client_order_id": "sim-phantom-client",
            "symbol": "AAA",
            "status": "new",
        }

        def orders(*, status="open", limit=100):
            return [phantom] if status == "open" else []

        with (
            mock.patch.object(reconcile, "connect", return_value=self.conn),
            mock.patch.object(reconcile, "list_positions", return_value=[]),
            mock.patch.object(reconcile, "list_orders", side_effect=orders),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(reconcile.main(["--repair"]), 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT resolved FROM reconciliation_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()[0],
            0,
        )


class GateLineageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn, self.path = _scratch()
        created = _iso(0)
        self.conn.execute(
            "INSERT INTO regime(id,current,determined_at,determined_by,signals_json,implications_json) "
            "VALUES('r','neutral',?,'quant','{}','{}')", (created,)
        )
        self.conn.execute(
            "INSERT INTO hypotheses(id,created_at,created_by,tickers,thesis_summary,state,rationale_concise) "
            "VALUES('h',?,'researcher','[\"TEST\"]','Long TEST on improving fundamentals','ready',?)",
            (created, "A sufficiently detailed, falsifiable thesis rationale for TEST."),
        )
        self.conn.execute(
            "INSERT INTO hypothesis_evidence(id,hypothesis_id,indicator,value,source,source_url,retrieved_at,signal_type) "
            "VALUES('e','h','fundamental','positive','primary','https://example.test',?,'fundamental')", (created,)
        )
        self.conn.execute(
            "INSERT INTO expression_candidates(id,hypothesis_id,vehicle,ticker,conviction_weight,created_at) "
            "VALUES('ec','h','direct_equity','TEST',0.02,?)", (created,)
        )
        self.conn.execute(
            "INSERT INTO trade_intents(id,hypothesis_id,expression_candidate_id,created_by,created_at,"
            "action,tranche_type,ticker,vehicle,size,entry_price_target,stop_rule,time_horizon,"
            "modeled_slippage_bps,state,direction) "
            "VALUES('ti','h','ec','trader',?,'open','starter','TEST','direct_equity',1,100,"
            "'-8% from entry','position_1_4w',8,'proposed','long')", (created,)
        )
        self.conn.execute(
            "INSERT INTO critic_reviews(id,target_type,target_id,reviewed_at,reviewed_by,"
            "challenges_json,all_challenges_addressed) "
            "VALUES('cr','hypothesis','h',?,'critic',?,1)",
            (created, '[{"challenge":"what breaks it?","response":"defined","resolved":true}]'),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        os.unlink(self.path)

    @staticmethod
    def _prediction_gate(result: dict) -> dict:
        return next(g for g in result["gates"] if g["name"] == "prediction_before_intent")

    def test_missing_or_later_prediction_fails(self) -> None:
        self.assertFalse(self._prediction_gate(gate_evaluator.evaluate(self.conn, "ti"))["pass"])
        self.conn.execute(
            "INSERT INTO predictions(id,hypothesis_id,predicted_at,predicted_by,horizon,p_correct,"
            "return_p10,return_p50,return_p90,mechanism_ids_json) "
            "VALUES('late','h',?,'quant','position_1_4w',0.60,-4,1,8,'[]')", (_iso(1),)
        )
        self.conn.commit()
        self.assertFalse(self._prediction_gate(gate_evaluator.evaluate(self.conn, "ti"))["pass"])

    def test_positive_prior_prediction_passes(self) -> None:
        self.conn.execute(
            "INSERT INTO predictions(id,hypothesis_id,predicted_at,predicted_by,horizon,p_correct,"
            "return_p10,return_p50,return_p90,mechanism_ids_json) "
            "VALUES('prior','h',?,'quant','position_1_4w',0.60,-4,1,8,'[]')", (_iso(-1),)
        )
        self.conn.commit()
        self.assertTrue(self._prediction_gate(gate_evaluator.evaluate(self.conn, "ti"))["pass"])


class ShellContinuityTests(unittest.TestCase):
    def test_pipeline_propagates_failures_and_guard_runs_both_gates(self) -> None:
        pipeline = (ROOT / "scripts/trader-pass-deterministic.sh").read_text()
        guard = (ROOT / "scripts/guard-pass.sh").read_text()
        self.assertIn('exit "$PIPELINE_RC"', pipeline)
        self.assertIn("gate_evaluator.py", guard)
        self.assertIn("gate_risk_intents.py", guard)
        self.assertLess(guard.index("gate_evaluator.py"), guard.index("execute_intent.py"))
        self.assertLess(guard.index("gate_risk_intents.py"), guard.index("execute_intent.py"))
        self.assertIn("predict.py --states ready", pipeline)
        predictor = (ROOT / "workspaces/quant/scripts/predict.py").read_text()
        self.assertIn('add_argument("--states", default="ready"', predictor)

    def test_runtime_snapshots_never_mutate_the_website_checkout(self) -> None:
        pipeline = (ROOT / "scripts/trader-pass-deterministic.sh").read_text()
        publisher = (ROOT / "scripts/push-trader-data.sh").read_text()
        provenance = (ROOT / "scripts/provenance-check.py").read_text()
        overlay = Path(
            "/home/aaron/repos/lidi-solutions/scripts/snapshot-trader-intel.mjs"
        ).read_text()
        tracked_path = "public/solutions/trader_intel/app/data.json"

        self.assertIn("state/trader-intel-snapshot", pipeline)
        self.assertIn("state/trader-intel-snapshot", publisher)
        self.assertNotIn(tracked_path, pipeline)
        self.assertNotIn(tracked_path, publisher)
        self.assertIn("TRADER_INTEL_OUT_DIR", pipeline)
        self.assertIn("TRADER_INTEL_OUT_DIR", publisher)
        self.assertIn("TRADER_INTEL_SKIP_DIST", overlay)
        self.assertNotIn('"generated": [', provenance)

    def test_postmortem_audit_ids_are_unique_per_hypothesis(self) -> None:
        first = write_postmortems._postmortem_id("H-ONE")
        second = write_postmortems._postmortem_id("H-TWO")
        self.assertNotEqual(first, second)
        self.assertNotEqual(
            write_postmortems._audit_id(first),
            write_postmortems._audit_id(second),
        )

    def test_health_probe_does_not_self_latch_on_prior_trader_pass(self) -> None:
        health = (ROOT / "workspaces/developer/scripts/audit_pipeline_health.py").read_text()
        critical_runs = health.split("CRITICAL_RUNS = {", 1)[1].split("}", 1)[0]
        self.assertNotIn('"trader-pass-deterministic.sh":', critical_runs)

    def test_grading_is_prediction_level_not_hypothesis_level(self) -> None:
        grader = (ROOT / "workspaces/archivist/scripts/grade_outcomes.py").read_text()
        calibrator = (ROOT / "workspaces/archivist/scripts/calibrate.py").read_text()
        self.assertIn("resolve_prediction_backlog(", grader)
        self.assertIn("resolved = []", calibrator)

    def test_baseline_critic_cannot_self_promote_and_gate_requires_substance(self) -> None:
        baseline = (ROOT / "workspaces/critic/scripts/critic_baseline.py").read_text()
        gate = (ROOT / "workspaces/trading-intel/scripts/gate_evaluator.py").read_text()
        main_body = baseline.split("def main(", 1)[1]
        self.assertNotIn("promote(conn, ev, r)", main_body)
        self.assertIn('review["reviewed_by"] == "critic"', gate)
        self.assertIn("n >= 2 and developed >= 2", gate)

    def test_dirty_live_repo_launches_in_clean_isolated_worktree(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            repo = Path(temp_dir) / "sample"
            repo.mkdir()
            subprocess.run(
                ["git", "init", "-b", "main", str(repo)],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.test"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Guardrail Test"],
                check=True,
            )
            tracked = repo / "tracked.txt"
            tracked.write_text("committed\n")
            subprocess.run(
                ["git", "-C", str(repo), "add", "tracked.txt"], check=True
            )
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-m", "base"],
                check=True, capture_output=True,
            )
            tracked.write_text("operator edit\n")

            launch_repo, original, worktree = (
                dwight_launch_from_issue.prepare_launch_repo(
                    str(repo), "TM-999", "999", {"title": "Dirty repo isolation"}
                )
            )
            try:
                self.assertEqual(original, "main")
                self.assertIsNotNone(worktree)
                self.assertNotEqual(launch_repo, str(repo))
                self.assertEqual(tracked.read_text(), "operator edit\n")
                self.assertEqual(
                    subprocess.run(
                        ["git", "-C", launch_repo, "status", "--porcelain"],
                        capture_output=True, text=True, check=True,
                    ).stdout,
                    "",
                )
                self.assertEqual(
                    dwight_launch_from_issue.git_current_branch(launch_repo),
                    "issue-999-dirty-repo-isolation",
                )
            finally:
                subprocess.run(
                    ["git", "-C", str(repo), "worktree", "remove", launch_repo],
                    check=True, capture_output=True,
                )

    def test_agent_crons_never_announce_completion_receipts(self) -> None:
        jobs = json.loads((ROOT / "cron/jobs.json").read_text())["jobs"]
        agent_jobs = [
            job for job in jobs
            if job.get("enabled") and (job.get("payload") or {}).get("kind") == "agentTurn"
        ]
        self.assertTrue(agent_jobs)
        for job in agent_jobs:
            self.assertEqual(
                (job.get("delivery") or {}).get("mode"),
                "none",
                msg=job.get("name"),
            )
            self.assertEqual(
                (job.get("failureAlert") or {}).get("mode"),
                "announce",
                msg=job.get("name"),
            )


if __name__ == "__main__":
    unittest.main()
