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
sys.path.insert(0, str(ROOT / "workspaces/risk/scripts"))
sys.path.insert(0, str(ROOT / "workspaces/archivist/scripts"))
sys.path.insert(0, str(ROOT / "workspaces/developer/scripts"))
sys.path.insert(0, str(ROOT / "scripts/lib"))

import gate_evaluator  # noqa: E402
import promote_mechanisms  # noqa: E402
import promotion_gate  # noqa: E402
import signal_scan  # noqa: E402
import author_intents  # noqa: E402
import enforce_stops  # noqa: E402
import broker  # noqa: E402
import execute_intent  # noqa: E402
import gate_risk_intents  # noqa: E402
import repair_cash_yield_history  # noqa: E402
import reconcile  # noqa: E402
import sim_broker  # noqa: E402
import trading_policy  # noqa: E402
import write_postmortems  # noqa: E402
import worldmodel  # noqa: E402
import symbol_lifecycle  # noqa: E402
import mechanism_backtest  # noqa: E402
import historical_walkforward  # noqa: E402
import historical_snapshot  # noqa: E402
import forward_shadow  # noqa: E402
import forward_shadow_report  # noqa: E402
import mechanism_correlation  # noqa: E402
import integrate_calibrated  # noqa: E402
import causal_graph  # noqa: E402
import feature_store  # noqa: E402
import feature_contract  # noqa: E402
import valuation  # noqa: E402
from connectors import edgar  # noqa: E402
from connectors import _http as connector_http  # noqa: E402
import integrity_check  # noqa: E402
import hypothesis_hygiene  # noqa: E402
import sync_symbol_aliases  # noqa: E402
import market_debrief  # noqa: E402
import market_event_intake  # noqa: E402
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

_operator_event_spec = importlib.util.spec_from_file_location(
    "operator_event", ROOT / "scripts/operator_event.py"
)
assert _operator_event_spec and _operator_event_spec.loader
operator_event = importlib.util.module_from_spec(_operator_event_spec)
_operator_event_spec.loader.exec_module(operator_event)

_oauth_enforcer_spec = importlib.util.spec_from_file_location(
    "enforce_codex_oauth", ROOT / "scripts/enforce-codex-oauth.py"
)
assert _oauth_enforcer_spec and _oauth_enforcer_spec.loader
enforce_codex_oauth = importlib.util.module_from_spec(_oauth_enforcer_spec)
_oauth_enforcer_spec.loader.exec_module(enforce_codex_oauth)

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
    def test_market_event_taxonomy_recognizes_completed_distressed_portfolio_transfer(self) -> None:
        taxonomy = market_event_intake.load_json(
            ROOT / "workspaces/trading-intel/config/market_event_taxonomy.json"
        )
        classes, phase = market_event_intake.classify_event(
            "AI-focused hedge fund sells all of its stocks",
            "Situational Awareness sold the bulk of its public equities portfolio "
            "to Citadel after suffering steep losses.",
            taxonomy,
        )
        self.assertIn("portfolio_transfer", classes)
        self.assertIn("fund_distress", classes)
        self.assertEqual(phase, "transfer_complete")

    def test_market_event_taxonomy_does_not_treat_generic_record_or_deal_as_catalyst(self) -> None:
        taxonomy = market_event_intake.load_json(
            ROOT / "workspaces/trading-intel/config/market_event_taxonomy.json"
        )
        classes, phase = market_event_intake.classify_event(
            "Apple falls after record run; investors debate whether to buy the dip",
            "Commentators discuss a possible deal, but no contract, guidance, or filing changed.",
            taxonomy,
        )
        self.assertNotIn("corporate_positive", classes)
        self.assertEqual(phase, "none")

    def test_market_event_intake_fixture_is_idempotent_and_coverage_auditable(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as td:
            tmp = Path(td)
            db = tmp / "events.sqlite"
            fixture = tmp / "fixture.json"
            fixture.write_text(json.dumps({"articles": [{
                "source": "fixture:wire",
                "source_event_id": "evt-1",
                "title": "AI fund sells all of its stocks to Citadel",
                "description": "Situational Awareness sold the bulk after steep losses.",
                "url": "https://example.test/event-1",
                "published_at": "2026-07-30T16:18:39Z",
                "retrieved_at": "2026-07-30T16:48:39Z",
                "query_ids": ["portfolio_transfer"],
                "tickers": ["NVDA"],
                "entities": ["Situational Awareness", "Citadel"],
            }]}))
            taxonomy = ROOT / "workspaces/trading-intel/config/market_event_taxonomy.json"
            first = market_event_intake.collect(db, taxonomy, fixture)
            second = market_event_intake.collect(db, taxonomy, fixture)
            self.assertEqual(first["status"], "ok")
            self.assertEqual(first["new"], 1)
            self.assertEqual(second["new"], 0)
            events = market_event_intake.rows_for_brief(db, hours=100000, limit=10)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["retrieval_latency_minutes"], 30.0)

            cases = tmp / "cases.json"
            cases.write_text(json.dumps({
                "cutover_at": "2026-07-01T00:00:00Z",
                "cases": [{
                    "id": "case-1",
                    "known_at": "2026-07-30T16:18:39Z",
                    "deadline_minutes": 60,
                    "terms_any": ["situational awareness"],
                    "terms_all": ["citadel"],
                    "required_classes_any": ["portfolio_transfer"],
                }],
            }))
            audit = market_event_intake.audit_cases(db, cases)
            self.assertEqual(audit["enforced_misses"], 0)
            self.assertTrue(audit["cases"][0]["passed"])

    def test_market_event_lane_is_advisory_and_read_by_research(self) -> None:
        wrapper = (ROOT / "scripts/market-event-intake.sh").read_text()
        self.assertIn("market_event_intake.py", wrapper)
        for forbidden in ("author_intents.py", "execute_intent.py", "gate_risk_intents.py"):
            self.assertNotIn(forbidden, wrapper)
        researcher = (ROOT / "workspaces/researcher/AGENTS.md").read_text()
        self.assertIn("state/market-event-brief.json", researcher)
        self.assertIn("transfer_complete", researcher)
        installer = (ROOT / "scripts/install-market-event-cron.sh").read_text()
        self.assertIn("*/15 6-20 * * 1-5", installer)
        self.assertIn("# BEGIN AUTOTRADE MARKET EVENT INTAKE", installer)
        self.assertIn("mktemp", installer)
        self.assertIn("trap", installer)

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

    def test_priority_queue_grooming_never_collapses_distinct_learning_failures(self) -> None:
        rows = {
            "resolver": {
                "id": "resolver", "status": "open", "task_id": None,
                "claimed_by": None, "submitted_at": "2026-08-03T17:30:52Z",
                "title": "Unblock matured prediction grading price windows",
            },
            "lock": {
                "id": "lock", "status": "open", "task_id": None,
                "claimed_by": None, "submitted_at": "2026-08-03T20:38:12Z",
                "title": "Prevent learning-pass core lock overlap",
            },
        }
        self.assertEqual(pq_groom.family(rows["resolver"]["title"]), "learning_outcomes")
        self.assertIsNone(pq_groom.family(rows["lock"]["title"]))
        self.assertEqual(pq_groom.plan(rows), [])

    def test_operator_event_deduplicates_pages_but_records_every_output(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            paths = {
                "ledger_path": root / "events.jsonl",
                "queue_path": root / "queue.jsonl",
                "lock_path": root / "events.lock",
            }
            first = operator_event.ingest(
                "🚨 HEALTH SWEEP CRIT — learning loop unresolved",
                family="health-learning-loop",
                now=datetime(2026, 8, 3, tzinfo=timezone.utc),
                **paths,
            )
            second = operator_event.ingest(
                "🚨 HEALTH SWEEP CRIT — learning loop unresolved",
                family="health-learning-loop",
                now=datetime(2026, 8, 3, 1, tzinfo=timezone.utc),
                **paths,
            )
            self.assertEqual(first["disposition"], "queued")
            self.assertEqual(second["disposition"], "duplicate")
            self.assertEqual(len(paths["ledger_path"].read_text().splitlines()), 2)
            self.assertEqual(len(paths["queue_path"].read_text().splitlines()), 1)

    def test_operator_event_recurrence_reopens_stable_queue_family(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            paths = {
                "ledger_path": root / "events.jsonl",
                "queue_path": root / "queue.jsonl",
                "lock_path": root / "events.lock",
            }
            operator_event.ingest(
                "Cron job learning failed: first error", family="learning-cron",
                now=datetime(2026, 8, 3, tzinfo=timezone.utc), **paths,
            )
            operator_event.ingest(
                "Cron job learning failed: different error", family="learning-cron",
                now=datetime(2026, 8, 3, 2, tzinfo=timezone.utc), **paths,
            )
            rows = [json.loads(line) for line in paths["queue_path"].read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["id"], rows[1]["id"])
            self.assertIsNone(rows[1]["task_id"])
            terminal = {"id": 1, "status": "done", "description": f"pq:{rows[0]['id']}"}
            active = {"id": 2, "status": "to_do", "description": f"pq:{rows[0]['id']}"}
            self.assertIsNone(poll_priority_queue.find_existing_issue([terminal], rows[0]["id"]))
            self.assertEqual(
                poll_priority_queue.find_existing_issue([terminal, active], rows[0]["id"])["id"],
                2,
            )

    def test_operator_event_hook_captures_transport_and_info_does_not_ticket(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            event = operator_event.ingest(
                "Quiet pass — book unchanged.",
                ledger_path=root / "events.jsonl",
                queue_path=root / "queue.jsonl",
                lock_path=root / "events.lock",
            )
            self.assertEqual(event["disposition"], "observed")
            self.assertFalse((root / "queue.jsonl").exists())
        hook = (ROOT / "hooks/telegram-ops-intake/handler.ts").read_text()
        self.assertIn('event.action !== "sent"', hook)
        self.assertIn('context.channelId !== "telegram"', hook)
        self.assertIn("operator_event.py", hook)
        config = json.loads((ROOT / "openclaw.json").read_text())
        self.assertTrue(
            config["hooks"]["internal"]["entries"]["telegram-ops-intake"]["enabled"]
        )

    def test_operator_event_delivery_rail_is_fast_deterministic_and_idempotent(self) -> None:
        wrapper = (ROOT / "scripts/dwight-pq-rail.sh").read_text()
        self.assertIn("poll_priority_queue.py", wrapper)
        self.assertIn("flock -n", wrapper)
        self.assertNotIn("openclaw agent", wrapper)
        installer = (ROOT / "scripts/install-operator-event-cron.sh").read_text()
        self.assertIn("*/5 * * * *", installer)
        self.assertIn("# BEGIN AUTOTRADE OPERATOR EVENT RAIL", installer)
        self.assertIn("mktemp", installer)
        self.assertIn("trap", installer)

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

    def test_offline_calibration_cannot_originate_without_live_approval(self) -> None:
        candidate = {
            "id": "candidate", "horizon": "month_21d", "direction": "long",
            "kind": "state", "source": "locked_forward_shadow_v1",
            "conditions": [["mom", ">", 0.1]], "rationale": "test",
            "net_alpha_pct": 1.25, "beta_neutral_alpha_pct": 0.8,
            "test_p": 0.001, "bonf_sig": 1, "hit_te": 0.55,
            "te_n": 120, "cluster_n": 60, "ticker_n": 25,
            "posterior_mean": 0.54, "skew_edge": 0,
        }
        normalized = integrate_calibrated.normalize_approved_candidates([candidate])[0]
        approval = {
            "decision_id": "D999", "_manifest_sha256": "a" * 64,
            "_source_artifact_sha256": "b" * 64,
            "expires_at": "2026-09-01T00:00:00Z",
        }
        note = integrate_calibrated.approved_candidate_note(
            normalized, approval, "2026-08-01T00:00:00Z",
        )
        with tempfile.TemporaryDirectory(dir="/tmp") as td:
            live = Path(td) / "live.sqlite"
            conn = sqlite3.connect(live)
            conn.execute(
                "CREATE TABLE mechanisms(id TEXT,status TEXT,direction TEXT,"
                "horizon TEXT,posterior_mean REAL,notes TEXT)"
            )
            conn.execute(
                "INSERT INTO mechanisms VALUES(?,?,?,?,?,?)",
                ("candidate__month_21d", "active", "long", "position_1_4w", 0.57, note),
            )
            conn.commit()
            conn.close()
            authorized = signal_scan.live_authorized_calibrations(
                str(live), now=datetime(2026, 8, 2, tzinfo=timezone.utc),
            )
            self.assertEqual(len(authorized), 1)
            self.assertEqual(authorized[0]["conditions"], [["mom", ">", 0.1]])
            self.assertEqual(authorized[0]["posterior_mean"], 0.57)

            # A research-table mutation cannot affect this payload. A live
            # note mutation without the artifact digest fails the scanner.
            broken = json.loads(note)
            broken["runtime_candidate"]["conditions"] = [["mom", ">", -99]]
            conn = sqlite3.connect(live)
            conn.execute("UPDATE mechanisms SET notes=?", (json.dumps(broken),))
            conn.commit()
            conn.close()
            with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
                signal_scan.live_authorized_calibrations(
                    str(live), now=datetime(2026, 8, 2, tzinfo=timezone.utc),
                )

    def test_expired_or_legacy_active_mechanism_fails_scanner_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as td:
            live = Path(td) / "live.sqlite"
            conn = sqlite3.connect(live)
            conn.execute(
                "CREATE TABLE mechanisms(id TEXT,status TEXT,direction TEXT,"
                "horizon TEXT,posterior_mean REAL,notes TEXT)"
            )
            conn.execute(
                "INSERT INTO mechanisms VALUES(?,?,?,?,?,?)",
                ("legacy", "active", "long", "position_1_4w", 0.5, "{}"),
            )
            conn.commit()
            conn.close()
            with self.assertRaisesRegex(RuntimeError, "runtime_candidate"):
                signal_scan.live_authorized_calibrations(str(live))

    def test_feature_writer_contract_rejects_unavailable_and_unprovenanced_rows(self) -> None:
        valid = ("aapl", "2026-08-01", "ret_1d", 1.2, "2026-08-01", "price")
        self.assertEqual(feature_contract.validate_feature_row(valid)[0], "AAPL")
        with self.assertRaisesRegex(ValueError, "must equal knowable_at"):
            feature_contract.validate_feature_row(
                ("AAPL", "2026-07-01", "eps", 1.0, "2026-08-01", "fundamental")
            )
        with self.assertRaisesRegex(ValueError, "source are required"):
            feature_contract.validate_feature_row(
                ("AAPL", "2026-08-01", "eps", 1.0, "2026-08-01", "")
            )

    def test_feature_store_sql_guards_block_bypasses(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE features(ticker TEXT,as_of TEXT,name TEXT,value REAL,"
            "knowable_at TEXT,source TEXT,PRIMARY KEY(ticker,as_of,name))"
        )
        report = feature_contract.install_guards(conn)
        self.assertEqual(report["rows"], 0)
        self.assertEqual(report["guard_count"], 2)
        conn.execute(
            "INSERT INTO features VALUES(?,?,?,?,?,?)",
            ("AAPL", "2026-08-01", "ret_1d", 1.2, "2026-08-01", "price"),
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "point-in-time contract"):
            conn.execute(
                "INSERT INTO features VALUES(?,?,?,?,?,?)",
                ("MSFT", "2026-07-01", "eps", 1.0, "2026-08-01", "fmp"),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "point-in-time contract"):
            conn.execute(
                "INSERT INTO features VALUES(?,?,?,?,?,?)",
                ("MSFT", "2026-08-01", "eps", 1.0, "2026-08-01", ""),
            )
        conn.close()

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

    def test_deprecate_all_never_preserves_calibrated_survivors(self) -> None:
        eligibility = {"eligible_survivors": [{"id": "apparently-good"}]}
        self.assertEqual(
            integrate_calibrated.selected_survivors_for_mode(
                eligibility, deprecate_all=True,
            ),
            [],
        )
        self.assertEqual(
            integrate_calibrated.selected_survivors_for_mode(
                eligibility, deprecate_all=False,
            ),
            eligibility["eligible_survivors"],
        )

    def test_promotion_candidate_digest_is_order_stable_and_value_sensitive(self) -> None:
        one = {
            "id": "m1", "horizon": "month_21d", "direction": "long",
            "kind": "state", "source": "seed", "conds_json": '[["x",">",1]]',
            "net_alpha_pct": 1.25, "test_p": 0.001, "bonf_sig": 1,
            "hit_te": 0.55, "te_n": 120, "cluster_n": 60,
            "posterior_mean": 0.54,
        }
        two = {**one, "id": "m2"}
        self.assertEqual(
            promotion_gate.candidate_set_sha256([one, two]),
            promotion_gate.candidate_set_sha256([two, one]),
        )
        self.assertNotEqual(
            promotion_gate.candidate_set_sha256([one]),
            promotion_gate.candidate_set_sha256([{**one, "net_alpha_pct": 1.26}]),
        )

    def test_only_exact_completed_forward_artifact_can_cross_promotion_gate(self) -> None:
        candidate = {
            "id": "m1", "horizon": "month_21d", "direction": "long",
            "kind": "state", "source": "locked_forward_shadow_v1",
            "conditions": [["mom", ">", 0.1]],
            "net_alpha_pct": 1.25, "test_p": 0.001, "bonf_sig": 1,
            "hit_te": 0.55, "te_n": 120, "cluster_n": 60,
            "ticker_n": 25, "posterior_mean": 0.54,
            "beta_neutral_alpha_pct": 0.80, "rationale": "test",
            "skew_edge": 0,
        }
        with tempfile.TemporaryDirectory(dir="/tmp") as td:
            root = Path(td)
            approval_root = root / "approvals"
            artifact_root = root / "state/research-artifacts"
            approval_root.mkdir()
            artifact_root.mkdir(parents=True)
            digest = promotion_gate.candidate_set_sha256([candidate])
            report_path = artifact_root / "forward.json"
            report = {
                "status": "complete",
                "evaluation_class": "locked_forward_shadow",
                "development_only": False,
                "minimum_sessions_met": True,
                "promotion_authority": "human_manifest_only",
                "evaluator_sha256": promotion_gate.file_sha256(
                    Path(promotion_gate.__file__).with_name("forward_shadow_report.py")
                ),
                "recorder_engine_sha256": promotion_gate.file_sha256(
                    Path(promotion_gate.__file__).with_name("forward_shadow.py")
                ),
                "promotion_gate_sha256": promotion_gate.file_sha256(
                    Path(promotion_gate.__file__)
                ),
                "candidate_set_sha256": digest,
                "promotion_candidates": [candidate],
            }
            report_path.write_text(json.dumps(report))
            manifest_path = approval_root / "D999.json"
            manifest = {
                "schema_version": 1,
                "artifact_type": "autotrade_strategy_promotion",
                "status": "approved",
                "approval_role": "operator",
                "approved_by": "test-operator",
                "decision_id": "D999",
                "approved_at": "2026-08-01T00:00:00Z",
                "expires_at": "2026-08-10T00:00:00Z",
                "candidate_set_sha256": digest,
                "candidate_ids": ["m1__month_21d"],
                "source_artifact": {
                    "path": "state/research-artifacts/forward.json",
                    "sha256": promotion_gate.file_sha256(report_path),
                },
            }
            manifest_path.write_text(json.dumps(manifest))
            validated = promotion_gate.validate_approval_manifest(
                manifest_path, [candidate],
                now=datetime(2026, 8, 2, tzinfo=timezone.utc),
                approval_root=approval_root, repo_root=root,
                require_committed=False,
            )
            self.assertEqual(validated["decision_id"], "D999")
            self.assertEqual(validated["_promotion_candidates"], [candidate])

            report["development_only"] = True
            report_path.write_text(json.dumps(report))
            manifest["source_artifact"]["sha256"] = promotion_gate.file_sha256(report_path)
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "development artifacts"):
                promotion_gate.validate_approval_manifest(
                    manifest_path, [candidate],
                    now=datetime(2026, 8, 2, tzinfo=timezone.utc),
                    approval_root=approval_root, repo_root=root,
                    require_committed=False,
                )

    def test_forward_artifact_candidates_stage_exactly_and_safely(self) -> None:
        candidate = {
            "id": "m1", "horizon": "month_21d", "direction": "long",
            "kind": "state", "source": "locked_forward_shadow_v1",
            "conditions": [["mom", ">", 0.1]], "rationale": "test",
            "net_alpha_pct": 1.25, "beta_neutral_alpha_pct": 0.8,
            "test_p": 0.001, "bonf_sig": 1, "hit_te": 0.55,
            "te_n": 120, "cluster_n": 60, "ticker_n": 25,
            "posterior_mean": 0.54, "skew_edge": 0,
        }
        normalized = integrate_calibrated.normalize_approved_candidates([candidate])
        with tempfile.TemporaryDirectory(dir="/tmp") as td:
            path = str(Path(td) / "features.sqlite")
            integrate_calibrated.stage_approved_candidates(
                path, normalized,
                {"decision_id": "D999", "_source_artifact_sha256": "a" * 64},
            )
            conn = sqlite3.connect(path)
            row = conn.execute(
                "SELECT id,horizon,conds_json,source FROM calibrated_mechanisms"
            ).fetchone()
            conn.close()
        self.assertEqual(row[0:2], ("m1", "month_21d"))
        self.assertEqual(json.loads(row[2]), [["mom", ">", 0.1]])
        self.assertEqual(row[3], "locked_forward_shadow_v1")

    def test_forward_metric_requires_maturity_breadth_and_dual_alpha(self) -> None:
        candidate = {
            "id": "m1", "horizon": "month_21d", "horizon_sessions": 21,
            "direction": "long", "kind": "state",
            "conditions": [["mom", ">", 0.1]], "rationale": "test",
            "threshold_source_fold": "latest",
        }
        decisions = []
        for index in range(30):
            decisions.append({
                "candidate_id": "m1", "horizon": "month_21d",
                "ticker": f"T{index % 20:02d}",
                "decision_date": f"2026-09-{index + 1:02d}", "status": "resolved",
                "raw_excess_return": 0.01, "beta_neutral_excess_return": 0.008,
            })
        decisions.append({
            "candidate_id": "m1", "horizon": "quarter_63d", "ticker": "NOPE",
            "decision_date": "2026-09-01", "status": "resolved",
            "raw_excess_return": -1.0, "beta_neutral_excess_return": -1.0,
        })
        metric = forward_shadow_report._candidate_metric(candidate, decisions, True)
        self.assertTrue(metric["matured"])
        self.assertEqual(metric["date_cluster_n"], 30)
        self.assertEqual(metric["ticker_n"], 20)
        self.assertGreater(metric["raw_spy_alpha_pct"], 0)
        self.assertGreater(metric["beta_neutral_alpha_pct"], 0)

    def test_promotion_gate_rejects_candidate_drift_and_expired_approval(self) -> None:
        source = (ROOT / "workspaces/trading-intel/scripts/promotion_gate.py").read_text()
        integrator = (
            ROOT / "workspaces/trading-intel/scripts/integrate_calibrated.py"
        ).read_text()
        self.assertIn("candidate set differs from the approved digest", source)
        self.assertIn("strategy approval has expired", source)
        self.assertIn("approval manifest differs from the committed HEAD version", source)
        self.assertIn('"--approval-manifest"', integrator)
        self.assertIn("refusing live integration", integrator)

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
        self.assertIn("m, p = _hac_mean_p(series, 1)", source)
        self.assertIn("robust_p = max(p_mean, beta_p_mean)", source)

    def test_backtest_trailing_beta_is_point_in_time_and_requires_history(self) -> None:
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        dates = [(start + timedelta(days=i)).date().isoformat() for i in range(180)]
        market_close = 100.0
        stock_close = 100.0
        market, stock = {}, {}
        for i, day in enumerate(dates):
            if i:
                market_return = 0.01 if i % 2 else -0.008
                market_close *= 1.0 + market_return
                stock_close *= 1.0 + 2.0 * market_return
            market[day] = market_close
            stock[day] = stock_close
        td = {"dates": dates, "close": stock}
        spy = {"dk": dates, "close": market}
        beta = mechanism_backtest.rolling_beta(td, spy, dates[-1])
        self.assertAlmostEqual(beta, 2.0, places=6)
        self.assertIsNone(
            mechanism_backtest.rolling_beta(
                {"dates": dates[:50], "close": {d: stock[d] for d in dates[:50]}},
                spy,
                dates[49],
            )
        )

    def test_historical_fold_purges_labels_across_both_boundaries(self) -> None:
        dates = [f"2020-01-{day:02d}" for day in range(1, 11)]
        td = {
            "dates": dates,
            "close": {day: 100.0 + i for i, day in enumerate(dates)},
            "dvol": {day: 10_000_000.0 for day in dates},
            "feats": {},
            "fkeys": {},
        }
        spy = {"dk": dates, "close": {day: 100.0 for day in dates}}

        train = mechanism_backtest.samples_for(
            td, spy, [], "state", 2,
            entry_end="2020-01-08",
            exit_before="2020-01-08",
            include_exit=True,
        )
        self.assertEqual([(row[0], row[1]) for row in train], [
            ("2020-01-02", "2020-01-04"),
        ])

        hidden = mechanism_backtest.samples_for(
            td, spy, [], "state", 2,
            entry_start="2020-01-06",
            entry_end="2020-01-09",
            exit_before="2020-01-09",
            include_exit=True,
        )
        self.assertEqual(hidden, [])
        visible = mechanism_backtest.samples_for(
            td, spy, [], "state", 2,
            entry_start="2020-01-06",
            entry_end="2020-01-10",
            exit_before="2020-01-10",
            include_exit=True,
        )
        self.assertEqual([(row[0], row[1]) for row in visible], [
            ("2020-01-07", "2020-01-09"),
        ])
        split_train, split_test = mechanism_backtest.split_samples_for(
            td, spy, [], "state", 2, None,
            train_start=None, test_start="2020-01-06", test_end="2020-01-10",
        )
        expected_train = mechanism_backtest.samples_for(
            td, spy, [], "state", 2,
            entry_end="2020-01-06", exit_before="2020-01-06",
        )
        self.assertEqual(split_train, expected_train)
        self.assertEqual(split_test, [row[:1] + row[2:] for row in visible])

    def test_historical_fold_rejects_overlapping_or_reversed_windows(self) -> None:
        with self.assertRaises(ValueError):
            mechanism_backtest._validate_window(
                "2020-01-01", "2020-01-01", "2021-01-01"
            )
        with self.assertRaises(ValueError):
            mechanism_backtest._validate_window(
                None, "2021-01-01", "2020-01-01"
            )

    def test_historical_fold_reports_every_loaded_empty_and_failed_symbol(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".sqlite", dir="/tmp")
        os.close(fd)
        coverage = {}

        def load(_conn, ticker):
            if ticker == "BAD":
                raise ValueError("corrupt cache")
            dates = [] if ticker == "EMPTY" else ["2019-01-02"]
            return {
                "dates": dates,
                "close": {day: 10.0 for day in dates},
                "dvol": {day: 10_000_000.0 for day in dates},
                "feats": {},
                "fkeys": {},
            }

        try:
            with mock.patch.object(mechanism_backtest, "FEAT_DB", Path(path)), \
                    mock.patch.object(mechanism_backtest, "load_ticker", side_effect=load):
                mechanism_backtest.run(
                    ["GOOD", "EMPTY", "BAD"],
                    {"dk": [], "close": {}},
                    "2020-01-01",
                    test_end="2021-01-01",
                    coverage_report=coverage,
                )
            self.assertEqual(coverage["loaded_symbols"], ["GOOD"])
            self.assertEqual(coverage["empty_symbols"], ["EMPTY"])
            self.assertEqual(coverage["load_errors"]["BAD"]["type"], "ValueError")
            self.assertEqual(coverage["pass_mismatch"], [])
        finally:
            os.unlink(path)

    def test_historical_fold_excludes_unvintaged_feature_families_everywhere(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".sqlite", dir="/tmp")
        os.close(fd)
        excluded = {"eps_surprise_pct", "net_margin_ttm", "revenue_growth_yoy", "pe_ttm"}
        hac_lags = []
        try:
            with mock.patch.object(mechanism_backtest, "FEAT_DB", Path(path)), \
                    mock.patch.object(
                        mechanism_backtest, "_hac_mean_p",
                        side_effect=lambda series, lag: (hac_lags.append(lag) or (0.0, 1.0)),
                    ):
                results, _, mechanisms, _ = mechanism_backtest.run(
                    [], {"dk": [], "close": {}}, "2020-01-01",
                    test_end="2021-01-01", train_start="2015-01-02",
                    excluded_features=excluded,
                )
            self.assertTrue(mechanisms)
            self.assertTrue(results)
            self.assertFalse(any(
                condition[0] in excluded
                for mechanism in mechanisms
                for condition in mechanism[2]
            ))
            self.assertFalse(any(
                row["kind"] == "cross" and row["conds"][0][0] in excluded
                for row in results
            ))
            self.assertEqual(set(hac_lags), {5, 21, 63})
        finally:
            os.unlink(path)

    def test_backtest_cached_name_count_excludes_empty_price_histories(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".sqlite", dir="/tmp")
        os.close(fd)
        empty = {"dates": [], "close": {}, "dvol": {}, "feats": {}, "fkeys": {}}
        day = "2020-01-02"
        populated = {
            "dates": [day], "close": {day: 100.0},
            "dvol": {day: 10_000_000.0}, "feats": {}, "fkeys": {},
        }
        try:
            with mock.patch.object(mechanism_backtest, "FEAT_DB", Path(path)), \
                    mock.patch.object(
                        mechanism_backtest, "load_ticker",
                        side_effect=lambda _conn, ticker: populated if ticker == "OK" else empty,
                    ):
                _results, _base, _mechanisms, names_seen = mechanism_backtest.run(
                    ["EMPTY", "OK"], {"dk": [day], "close": {day: 100.0}},
                    "2020-01-01", test_end="2021-01-01",
                )
            self.assertEqual(names_seen, 1)
        finally:
            os.unlink(path)

    def test_multi_mechanism_trade_direction_is_explicit_not_inferred_from_prose(self) -> None:
        features = {
            feature: [float(value) for value in range(250)]
            for pair in mechanism_backtest.MULTI_PAIRS
            for feature in (pair[0], pair[2])
        }
        generated = {row[0]: row for row in mechanism_backtest.gen_multi(features)}
        for first, first_side, second, second_side, direction, _label in (
            mechanism_backtest.MULTI_PAIRS
        ):
            mid = f"multi_{first}_{first_side}_{second}_{second_side}"
            self.assertEqual(generated[mid][3], direction)
        self.assertEqual(
            generated["multi_rate_10y_chg_63d_hi_pe_ttm_hi"][3], "short"
        )
        self.assertEqual(
            generated["multi_credit_spread_chg_63d_hi_vol_20d_annual_hi"][3],
            "short",
        )

    def test_bounded_fold_is_invariant_to_hidden_future_values(self) -> None:
        start = datetime(2015, 1, 1).date()
        dates = [(start + timedelta(days=i)).isoformat() for i in range(1500)]
        test_start = "2018-01-01"
        test_end = "2019-01-01"

        def ticker_data(future_multiplier: float) -> dict:
            close = {}
            rsi = []
            for i, day in enumerate(dates):
                value = 100.0 + i * 0.02
                if day >= test_end:
                    value *= future_multiplier
                close[day] = value
                feature = float(i % 100)
                if day >= test_start:
                    feature += 1_000_000.0 * future_multiplier
                rsi.append((day, feature))
            return {
                "dates": dates,
                "close": close,
                "dvol": {day: 20_000_000.0 for day in dates},
                "feats": {"rsi14": rsi},
                "fkeys": {"rsi14": dates},
            }

        spy = {"dk": dates, "close": {day: 100.0 for day in dates}}
        fd, path = tempfile.mkstemp(suffix=".sqlite", dir="/tmp")
        os.close(fd)
        try:
            outputs = []
            for multiplier in (1.0, 999.0):
                td = ticker_data(multiplier)
                with mock.patch.object(mechanism_backtest, "FEAT_DB", Path(path)), \
                        mock.patch.object(mechanism_backtest, "load_ticker", return_value=td):
                    outputs.append(mechanism_backtest.run(
                        ["TEST"], spy, test_start,
                        test_end=test_end, train_start="2015-01-02",
                    ))
            first_results, first_base, first_mechanisms, _ = outputs[0]
            second_results, second_base, second_mechanisms, _ = outputs[1]
            self.assertEqual(first_mechanisms, second_mechanisms)
            self.assertEqual(first_base, second_base)
            self.assertEqual(first_results, second_results)
            generated_rsi = [
                mechanism for mechanism in first_mechanisms
                if mechanism[0].startswith("gen_rsi14_")
            ]
            self.assertEqual(len(generated_rsi), 4)
            self.assertTrue(all(
                abs(mechanism[2][0][2]) < 100.0 for mechanism in generated_rsi
            ))
        finally:
            os.unlink(path)

    def test_fundamentals_never_fall_back_to_fiscal_period_date(self) -> None:
        statements = [
            {
                "date": f"2019-{quarter:02d}-30",
                "revenue": 100.0,
                "netIncome": 10.0,
                "eps": 1.0,
                "filingDate": f"2019-{quarter + 1:02d}-15",
            }
            for quarter in (1, 2, 3)
        ]
        statements.append({
            "date": "2019-04-30",
            "revenue": 100.0,
            "netIncome": 10.0,
            "eps": 1.0,
        })
        with mock.patch.object(
            feature_store.fmp, "income_statement", return_value=statements
        ):
            self.assertEqual(feature_store._fundamental("TEST"), [])

        statements[-1]["acceptedDate"] = "2019-05-15T20:01:02Z"
        with mock.patch.object(
            feature_store.fmp, "income_statement", return_value=statements
        ):
            rows = feature_store._fundamental("TEST")
        self.assertEqual(rows[0][0], "2019-05-15")

    def test_walkforward_aggregate_requires_repeated_broad_cost_net_alpha(self) -> None:
        gate = {
            "minimum_eligible_folds": 3,
            "minimum_positive_alpha_folds": 3,
            "minimum_entry_date_clusters_per_fold": 30,
            "minimum_tickers_per_fold": 20,
        }
        folds = []
        for i, alpha in enumerate((1.0, 0.8, 0.6), start=1):
            folds.append({
                "id": f"f{i}",
                "results": [{
                    "id": "candidate",
                    "horizon": "month_21d",
                    "direction": "long",
                    "kind": "state",
                    "alpha_te_pct": alpha,
                    "test_p": 0.001,
                    "test_p_raw": 0.001,
                    "cluster_n": 40,
                    "ticker_n": 30,
                    "sig": {"fdr": True, "bonf": True},
                }],
            })
        rows = historical_walkforward.aggregate(folds, gate)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["combined_bonferroni"])
        self.assertTrue(rows[0]["stable_development_candidate"])

        folds[2]["results"][0]["ticker_n"] = 2
        rows = historical_walkforward.aggregate(folds, gate)
        self.assertEqual(rows[0]["eligible_folds"], 2)
        self.assertFalse(rows[0]["stable_development_candidate"])

        folds[2]["results"][0]["ticker_n"] = 30
        folds[2]["results"][0]["beta_neutral_alpha_te_pct"] = -0.1
        rows = historical_walkforward.aggregate(folds, gate)
        self.assertEqual(rows[0]["positive_alpha_folds"], 2)
        self.assertFalse(rows[0]["stable_development_candidate"])

    def test_walkforward_data_snapshot_detects_midrun_input_mutation(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            (root / "cache").mkdir()
            feature_db = root / "features.sqlite"
            conn = sqlite3.connect(feature_db)
            conn.execute("CREATE TABLE features(ticker TEXT)")
            conn.commit()
            conn.close()
            price = root / "cache/massive_test_1d_2015-01-01_2026-01-01.json"
            fred = root / "cache/fred_dgs10.json"
            price.write_bytes(b"prices-v1")
            fred.write_bytes(b"fred-v1")
            historical_snapshot._write_manifest(root, missing_symbols=[])
            before = historical_walkforward._data_snapshot_signature(root)

            # A harmless SQLite read/open does not change content identity.
            conn = sqlite3.connect(feature_db)
            conn.execute("SELECT COUNT(*) FROM features").fetchone()
            conn.close()
            self.assertEqual(
                before, historical_walkforward._data_snapshot_signature(root)
            )

            price.write_bytes(b"prices-version-two")
            with self.assertRaisesRegex(RuntimeError, "snapshot file changed"):
                historical_walkforward._data_snapshot_signature(root)

    def test_canonical_walkforward_requires_immutable_snapshot(self) -> None:
        source = (
            ROOT / "workspaces/trading-intel/scripts/historical_walkforward.py"
        ).read_text()
        self.assertIn("canonical historical replay requires --snapshot-dir", source)
        self.assertIn("canonical historical replay requires --workers 1", source)
        self.assertIn("hs.validate_snapshot(snapshot_dir)", source)

    def test_snapshot_manifest_excludes_sqlite_transient_files(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            (root / "features.sqlite").write_bytes(b"db")
            (root / "features.sqlite-shm").write_bytes(b"transient")
            (root / "features.sqlite-wal").write_bytes(b"transient")
            manifest = historical_snapshot._write_manifest(root, missing_symbols=[])
            self.assertEqual(
                [row["path"] for row in manifest["files"]], ["features.sqlite"]
            )

    def test_snapshot_uses_same_corrupt_cache_fallback_as_engine(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            older = root / "massive_test_1d_2015-01-01_2026-07-30.json"
            newer = root / "massive_test_1d_2015-01-01_2026-07-31.json"
            older.write_text(json.dumps({"bars": [{"t": "2026-07-30", "c": 10.0}]}))
            newer.write_text("not-json")
            self.assertEqual(
                historical_snapshot._cached_price_path(root, "TEST"), older
            )

    def test_snapshot_preflight_exercises_the_engines_exact_feature_query(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE features(ticker TEXT,name TEXT,as_of TEXT,value REAL)"
        )
        conn.execute(
            "INSERT INTO features VALUES('PEG','mom','2026-01-01',1.0)"
        )
        historical_snapshot._validate_replay_feature_reads(conn, ["PEG"])
        trace = []
        conn.set_trace_callback(trace.append)
        historical_snapshot._validate_replay_feature_reads(conn, ["PEG"])
        self.assertTrue(any("ORDER BY as_of" in query for query in trace))
        conn.close()

    def test_snapshot_rejects_unmanifested_sqlite_wal(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            (root / "cache").mkdir()
            (root / "features.sqlite").write_bytes(b"db")
            (root / "cache/fred_test.json").write_bytes(b"fred")
            historical_snapshot._write_manifest(root, missing_symbols=[])
            (root / "features.sqlite-wal").write_bytes(b"untrusted")
            with self.assertRaisesRegex(RuntimeError, "transient SQLite state"):
                historical_snapshot.validate_snapshot(root)

    def test_walkforward_report_fingerprints_code_policy_data_and_authority(self) -> None:
        spec = json.loads(
            (ROOT / "workspaces/trading-intel/config/evaluation_policy.json").read_text()
        )["historical_walkforward_development"]
        report = historical_walkforward._new_report(
            spec, ["AAA"], "engine-hash", "runner-hash", "spec-hash",
            "policy-file-hash", "forward-policy-hash",
            {"sha256": "data-hash", "file_count": 3, "total_bytes": 10},
        )
        self.assertEqual(report["engine_sha256"], "engine-hash")
        self.assertEqual(report["runner_sha256"], "runner-hash")
        self.assertEqual(report["historical_spec_sha256"], "spec-hash")
        self.assertEqual(report["policy_file_sha256"], "policy-file-hash")
        self.assertEqual(report["forward_policy_sha256"], "forward-policy-hash")
        self.assertEqual(report["universe_symbols"], ["AAA"])
        self.assertEqual(report["data_snapshot_signature"]["sha256"], "data-hash")
        self.assertTrue(report["development_only"])
        self.assertEqual(report["promotion_authority"], "none")

    def test_forward_candidate_freeze_binds_conditions_from_latest_fold(self) -> None:
        folds = [
            {
                "id": "old",
                "test_start": "2020-01-01",
                "results": [{
                    "id": "m", "horizon": "month_21d", "direction": "long",
                    "kind": "state", "conds": [["mom", ">", 0.1]],
                    "rationale": "old threshold",
                }],
            },
            {
                "id": "latest",
                "test_start": "2024-01-01",
                "results": [{
                    "id": "m", "horizon": "month_21d", "direction": "long",
                    "kind": "state", "conds": [["mom", ">", 0.3]],
                    "rationale": "latest pre-test threshold",
                }],
            },
        ]
        stable = [{
            "id": "m", "horizon": "month_21d", "direction": "long",
            "kind": "state",
        }]
        frozen = historical_walkforward.freeze_forward_candidate_set(
            folds, stable, {"folds": [{"id": "old"}, {"id": "latest"}]},
            {"start": "2026-08-04", "minimum_end": "2026-10-30", "minimum_sessions": 60},
        )
        self.assertEqual(frozen["candidates"][0]["conditions"], [["mom", ">", 0.3]])
        self.assertEqual(frozen["candidates"][0]["threshold_source_fold"], "latest")
        self.assertEqual(len(frozen["candidate_set_sha256"]), 64)

    def test_forward_shadow_excludes_the_current_et_bar_and_ids_are_stable(self) -> None:
        spy = {"dk": ["2026-08-03", "2026-08-04", "2026-08-05"]}
        now = datetime(2026, 8, 5, 16, 12, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(
            forward_shadow._eligible_decision_dates(spy, "2026-08-04", now),
            ["2026-08-04"],
        )
        candidate = {"id": "m", "horizon": "month_21d"}
        first = forward_shadow._decision_id(candidate, "ABC", "2026-08-04", "long")
        self.assertEqual(
            first,
            forward_shadow._decision_id(candidate, "ABC", "2026-08-04", "long"),
        )
        self.assertNotEqual(
            first,
            forward_shadow._decision_id(candidate, "ABC", "2026-08-04", "short"),
        )

    def test_forward_shadow_starts_end_to_end_with_its_frozen_spy_loader(self) -> None:
        """Exercise startup, session recording, SQLite, and atomic output together."""
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            database = root / "features.sqlite"
            sqlite3.connect(database).close()
            snapshot = root / "snapshot"
            snapshot.mkdir()
            (snapshot / "manifest.json").write_text(
                json.dumps({"missing_price_symbols": []}) + "\n"
            )
            output = root / "forward.json"
            report = {
                "forward_candidate_set": {"candidates": []},
                "universe_symbols": [],
            }
            protocol = {
                "schema_version": 1,
                "evaluation_class": "locked_forward_shadow",
                "recorder_sha256": "recorder",
                "report_sha256": "report",
                "candidate_set_sha256": "candidates",
                "universe_sha256": "universe",
                "start": "2026-08-04",
                "minimum_end": "2026-10-30",
                "minimum_sessions": 60,
                "promotion_authority": "none",
            }
            original_features = mechanism_backtest.FEAT_DB
            original_cache = mechanism_backtest.CACHE_DIR
            original_network = mechanism_backtest.ALLOW_NETWORK
            try:
                with (
                    mock.patch.object(forward_shadow, "LIVE_FEATURES", database),
                    mock.patch.object(forward_shadow, "LIVE_CACHE", root / "cache"),
                    mock.patch.object(
                        forward_shadow, "_protocol", return_value=(report, protocol)
                    ),
                    mock.patch.object(
                        mechanism_backtest,
                        "_backtest_prices",
                        return_value=[
                            {"t": "2026-08-03", "c": 100.0},
                            {"t": "2026-08-04", "c": 101.0},
                            {"t": "2026-08-05", "c": 102.0},
                        ],
                    ),
                ):
                    artifact = forward_shadow.run(
                        root / "report.json", snapshot, output,
                        now=datetime(
                            2026, 8, 5, 16, 12,
                            tzinfo=ZoneInfo("America/New_York"),
                        ),
                    )
            finally:
                mechanism_backtest.FEAT_DB = original_features
                mechanism_backtest.CACHE_DIR = original_cache
                mechanism_backtest.ALLOW_NETWORK = original_network
            self.assertEqual(artifact["sessions_recorded"], ["2026-08-04"])
            self.assertEqual(artifact["summary"]["candidate_count"], 0)
            self.assertEqual(json.loads(output.read_text()), artifact)

    def test_daily_learning_chain_owns_forward_shadow_recorder(self) -> None:
        source = (ROOT / "scripts/learning-chain.sh").read_text()
        self.assertIn('step "forward-shadow"      "$PY" "$TI/forward_shadow.py"', source)

    def test_historical_walkforward_has_no_promotion_authority(self) -> None:
        policy = json.loads(
            (ROOT / "workspaces/trading-intel/config/evaluation_policy.json").read_text()
        )["historical_walkforward_development"]
        self.assertEqual(policy["promotion_authority"], "none")
        source = (
            ROOT / "workspaces/trading-intel/scripts/historical_walkforward.py"
        ).read_text()
        self.assertNotIn("persist(", source)
        self.assertNotIn("trading-intel.sqlite", source)
        self.assertFalse(
            (ROOT / "workspaces/trading-intel/scripts/backtest.py").exists(),
            "the retired survivorship-biased same-close backtester must not return",
        )
        self.assertFalse(
            (ROOT / "workspaces/trading-intel/scripts/validate_mechanism.py").exists(),
            "the retired iid single-candidate p-value shortcut must not return",
        )

    def test_ml_ranker_is_labeled_as_a_separate_shadow_not_no_trading(self) -> None:
        ranker = (ROOT / "workspaces/trading-intel/scripts/ml_ranker.py").read_text()
        chain = (ROOT / "scripts/learning-chain.sh").read_text()
        architecture = (ROOT / "SYSTEM_ARCHITECTURE.md").read_text()
        self.assertIn("separate internal shadow model book", ranker)
        self.assertIn("separate internal shadow model book", chain)
        self.assertIn("GBM shadow lane", architecture)
        self.assertNotIn("Nothing trades on this", chain)
        self.assertNotIn("Nothing reads this table for trading", ranker)

    def test_single_split_persistence_records_bounded_window_provenance(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".sqlite", dir="/tmp")
        os.close(fd)
        result = {
            "id": "m",
            "horizon": "month_21d",
            "direction": "long",
            "rationale": "test",
            "conds": [["x", ">", 1.0]],
            "kind": "state",
            "base": 0.5,
            "tr_n": 40,
            "te_n": 30,
            "cluster_n": 30,
            "ticker_n": 20,
            "hit_te": 0.6,
            "alpha_te_pct": 1.0,
            "test_p": 0.01,
            "weight_mean": 0.55,
            "sig": {"fdr": True, "bonf": False},
        }
        try:
            with mock.patch.object(mechanism_backtest, "FEAT_DB", Path(path)):
                mechanism_backtest.persist(
                    [result], "2020-01-01", "development_test", "2026-07-31", 1,
                    test_end="2021-01-01", train_start="2015-01-02",
                )
            conn = sqlite3.connect(path)
            row = conn.execute(
                "SELECT test_start,test_end,train_start,evaluation_label "
                "FROM discovered_mechanisms"
            ).fetchone()
            conn.close()
            self.assertEqual(row, (
                "2020-01-01", "2021-01-01", "2015-01-02", "development_test"
            ))
        finally:
            os.unlink(path)

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

    def test_runtime_short_policy_blocks_new_risk_but_allows_reductions(self) -> None:
        self.assertTrue(trading_policy.blocks_new_short("open", "short"))
        self.assertTrue(trading_policy.blocks_new_short("add", "short"))
        self.assertFalse(trading_policy.blocks_new_short("exit", "short"))
        self.assertFalse(trading_policy.blocks_new_short("trim", "short"))
        self.assertTrue(trading_policy.would_increase_short(0, "sell", 1))
        self.assertTrue(trading_policy.would_increase_short(-10, "sell", 1))
        self.assertFalse(trading_policy.would_increase_short(10, "sell", 5))
        self.assertFalse(trading_policy.would_increase_short(-10, "buy", 5))

        blocked = gate_risk_intents.gate(
            sqlite3.connect(":memory:"),
            {
                "id": "ti-short",
                "ticker": "ABC",
                "entry_price_target": 10,
                "size": 1,
                "action": "open",
                "direction": "short",
            },
            equity=100_000,
            day_pl=0,
            regime="neutral",
        )
        self.assertEqual(blocked["verdict"], "blocked")
        self.assertEqual(blocked["breaches"], ["short_borrow_model_missing"])

        reducing = gate_risk_intents.gate(
            sqlite3.connect(":memory:"),
            {
                "id": "ti-cover",
                "ticker": "ABC",
                "entry_price_target": 10,
                "size": 1,
                "action": "exit",
                "direction": "short",
            },
            equity=100_000,
            day_pl=0,
            regime="risk_off",
        )
        self.assertEqual(reducing["verdict"], "approved")

    def test_executor_defense_in_depth_rejects_approved_short_open(self) -> None:
        conn = sqlite3.connect(":memory:")
        result = execute_intent.process(
            {
                "id": "ti-short",
                "ticker": "ABC",
                "vehicle": "equity",
                "action": "open",
                "size": 1,
                "entry_price_target": 10,
                "created_at": _iso(),
                "state": "approved",
                "direction": "short",
            },
            dry_run=True,
            conn=conn,
        )
        self.assertTrue(result["short_open_blocked"])
        self.assertFalse(result["submitted"])
        conn.close()

    def test_simulator_never_creates_or_enlarges_short_inventory(self) -> None:
        conn, path = _scratch()
        try:
            sim_broker.ensure_book(conn, "desk", cash=1_000)
            sim_broker.apply_fill(conn, "desk", "ABC", "buy", 5, 10)
            sim_broker.apply_fill(conn, "desk", "ABC", "sell", 2, 12)
            before = (
                sim_broker.get_cash(conn, "desk"),
                sim_broker.positions(conn, "desk")["ABC"]["qty"],
                conn.execute("SELECT COUNT(*) FROM sim_orders").fetchone()[0],
            )
            with self.assertRaisesRegex(ValueError, "short_open_disabled"):
                sim_broker.apply_fill(conn, "desk", "ABC", "sell", 4, 12)
            after = (
                sim_broker.get_cash(conn, "desk"),
                sim_broker.positions(conn, "desk")["ABC"]["qty"],
                conn.execute("SELECT COUNT(*) FROM sim_orders").fetchone()[0],
            )
            self.assertEqual(after, before)

            conn.execute(
                "UPDATE sim_positions SET qty=-5,cost_basis=20 WHERE book='desk' AND ticker='ABC'"
            )
            conn.commit()
            sim_broker.apply_fill(conn, "desk", "ABC", "buy", 2, 18)
            self.assertEqual(sim_broker.positions(conn, "desk")["ABC"]["qty"], -3)
        finally:
            conn.close()
            os.unlink(path)

    def test_cash_yield_skips_closed_sessions_and_restricts_short_proceeds(self) -> None:
        conn, path = _scratch()
        try:
            sim_broker.ensure_book(conn, "desk", cash=1_200)
            sim_broker._ensure_cash_yield_tables(conn)
            with mock.patch.object(sim_broker, "_iso_today", return_value="2026-08-02"):
                result = sim_broker._apply_cash_yield_once_per_day(
                    conn,
                    "desk",
                    restricted_short_collateral=200,
                )
            self.assertTrue(result["non_trading_day"])
            self.assertEqual(result["credit"], 0)
            self.assertEqual(sim_broker.get_cash(conn, "desk"), 1_200)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM sim_cash_yield_events").fetchone()[0],
                0,
            )

            with mock.patch.object(sim_broker, "_iso_today", return_value="2026-08-03"), \
                    mock.patch.object(sim_broker, "_sgov_proxy_apy", return_value=0.0504):
                result = sim_broker._apply_cash_yield_once_per_day(
                    conn,
                    "desk",
                    restricted_short_collateral=200,
                )
            self.assertTrue(result["applied"])
            self.assertEqual(result["cash_start"], 1_000)
            self.assertEqual(result["credit"], 0.2)
        finally:
            conn.close()
            os.unlink(path)

    def test_health_pages_on_nontrading_cash_yield_credit(self) -> None:
        conn, path = _scratch()
        try:
            conn.execute(
                "INSERT INTO sim_cash_yield_events(id,book,as_of_date,annual_yield,cash_start,"
                "credit,applied_at) VALUES('bad','desk','2026-08-02',0.045,1000,1,?)",
                (_iso(),),
            )
            conn.commit()
        finally:
            conn.close()
        try:
            result = system_health_sweep.check_trading_accounting(Path(path))
            self.assertEqual(result["severity"], "crit")
            self.assertIn("non-trading-session", result["detail"])
        finally:
            os.unlink(path)

    def test_cash_yield_history_repair_is_audited_and_idempotent(self) -> None:
        conn, path = _scratch()
        try:
            sim_broker.ensure_book(conn, "desk", cash=1_511)
            conn.execute(
                "INSERT INTO sim_orders(order_id,book,symbol,side,qty,fill_price,source,filled_at) "
                "VALUES('short','desk','ABC','sell',5,100,'test','2026-07-11T15:00:00Z')"
            )
            conn.execute(
                "INSERT INTO sim_positions(id,book,ticker,qty,cost_basis,state,opened_at) "
                "VALUES('short-pos','desk','ABC',-5,100,'open','2026-07-11T15:00:00Z')"
            )
            conn.executemany(
                "INSERT INTO sim_cash_yield_events(id,book,as_of_date,annual_yield,cash_start,"
                "credit,applied_at) VALUES(?,?,?,?,?,?,?)",
                [
                    ("sun", "desk", "2026-07-12", 0.252, 1_500, 1.0, "2026-07-12T16:00:00Z"),
                    ("mon", "desk", "2026-07-13", 0.252, 1_501, 1.501, "2026-07-13T16:00:00Z"),
                ],
            )
            conn.executemany(
                "INSERT INTO book_equity(book,date,equity,cash) VALUES('desk',?,?,?)",
                [
                    ("2026-07-12", 1_001, 1_501),
                    ("2026-07-13", 1_002.501, 1_502.501),
                ],
            )
            conn.executemany(
                "INSERT INTO book_return_attribution(book,date,equity,last_equity,trading_pl,"
                "cash_yield_pl,total_pl,created_at) VALUES('desk',?,?,?,?,?,?,?)",
                [
                    ("2026-07-12", 1_001, None, 0, 1, 0, _iso()),
                    ("2026-07-13", 1_002.501, 1_001, 0, 1.501, 1.501, _iso()),
                ],
            )
            conn.commit()

            plan = repair_cash_yield_history.plan_corrections(conn)
            self.assertEqual(plan[0]["corrected_credit"], 0)
            self.assertEqual(plan[1]["short_collateral"], 500)
            self.assertEqual(plan[1]["corrected_cash_start"], 1_000)
            self.assertEqual(plan[1]["corrected_credit"], 1.0)

            result = repair_cash_yield_history.apply_repair(conn)
            self.assertTrue(result["applied"])
            self.assertAlmostEqual(result["cash_delta"], -1.501)
            self.assertEqual(
                [tuple(row) for row in conn.execute(
                    "SELECT credit,original_credit FROM sim_cash_yield_events ORDER BY applied_at"
                )],
                [(0.0, 1.0), (1.0, 1.501)],
            )
            self.assertAlmostEqual(sim_broker.get_cash(conn, "desk"), 1_509.499)
            self.assertEqual(
                tuple(conn.execute(
                    "SELECT equity,cash FROM book_equity WHERE date='2026-07-13'"
                ).fetchone()),
                (1_001.0, 1_501.0),
            )
            again = repair_cash_yield_history.apply_repair(conn)
            self.assertTrue(again["already_applied"])
            self.assertAlmostEqual(sim_broker.get_cash(conn, "desk"), 1_509.499)
        finally:
            conn.close()
            os.unlink(path)

    def test_cash_yield_replay_distinguishes_seeded_long_sales_from_shorts(self) -> None:
        conn, path = _scratch()
        try:
            conn.execute(
                "INSERT INTO sim_orders(order_id,book,symbol,side,qty,fill_price,source,filled_at) "
                "VALUES('close-seed','desk','ABC','sell',5,100,'test','2026-07-11T15:00:00Z')"
            )
            conn.commit()
            orders = conn.execute(
                "SELECT symbol,side,qty,fill_price,filled_at FROM sim_orders "
                "WHERE book='desk' ORDER BY filled_at,order_id"
            ).fetchall()
            initial = repair_cash_yield_history._initial_positions(conn, orders, "desk")
            self.assertEqual(initial["ABC"]["qty"], 5)
            self.assertEqual(
                repair_cash_yield_history._short_collateral_at(
                    orders,
                    "2026-07-12T16:00:00Z",
                    initial,
                ),
                0,
            )
        finally:
            conn.close()
            os.unlink(path)

    def test_legacy_shorts_are_forced_to_exit_through_normal_gates(self) -> None:
        conn, path = _scratch()
        try:
            conn.execute(
                "INSERT INTO hypotheses(id,created_at,created_by,tickers,thesis_summary,state) "
                "VALUES('h-short',?,'researcher','[\"ABC\"]','legacy short','active')",
                (_iso(),),
            )
            conn.execute(
                "INSERT INTO positions(id,hypothesis_id,ticker,vehicle,qty,cost_basis,state,opened_at,book) "
                "VALUES('p-short','h-short','ABC','direct_equity',-5,100,'open',?,'desk')",
                (_iso(-60),),
            )
            conn.commit()
            output = io.StringIO()
            with mock.patch.object(enforce_stops, "DB_PATH", Path(path)), \
                    mock.patch.object(
                        enforce_stops,
                        "latest_trades",
                        return_value={"ABC": {"price": 90}},
                    ), redirect_stdout(output):
                rc = enforce_stops.main(["--dry-run"])
            report = json.loads(output.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(len(report["stop_breaches"]), 1)
            self.assertTrue(report["stop_breaches"][0]["policy_forced_exit"])
            self.assertEqual(report["stop_breaches"][0]["action"], "exit")
        finally:
            conn.close()
            os.unlink(path)

    def test_capital_efficiency_uses_gross_exposure_and_deployable_cash(self) -> None:
        snapshot = capital_efficiency_audit._deployment_snapshot(
            1_200,
            [
                {"qty": 10, "current_price": 10, "cost_basis": 9},
                {"qty": -5, "current_price": 20, "cost_basis": 18},
            ],
        )
        self.assertEqual(snapshot["gross_exposure"], 200)
        self.assertEqual(snapshot["short_collateral"], 100)
        self.assertEqual(snapshot["deployable_cash"], 1_100)

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

    def test_flat_open_market_is_a_successful_mark_not_a_quote_failure(self) -> None:
        mark = {
            "book": "desk",
            "mark_quality": {"position_count": 0, "live_bulk": 0},
        }
        with mock.patch.object(sim_broker, "connect", return_value=object()), \
                mock.patch.object(sim_broker, "mark_book", return_value=mark), \
                mock.patch.object(sim_broker, "market_clock", return_value={"is_open": True}), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(sim_broker.main(["mark", "--book", "desk"]), 0)

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
        self.assertIn("validation = audit_corpus(conn)", source)
        self.assertIn('validation["eligible_resolved_counts"]', source)
        self.assertIn('validation["structural_ok"]', source)
        self.assertIn('validation["reasoning_gate"]', source)
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
        live.execute(
            "CREATE TABLE mechanisms(id TEXT,status TEXT,direction TEXT,"
            "horizon TEXT,posterior_mean REAL,notes TEXT)"
        )
        candidate = integrate_calibrated.normalize_approved_candidates([{
            "id": "quality", "horizon": "month_21d", "direction": "long",
            "kind": "state", "source": "locked_forward_shadow_v1",
            "conditions": [["quality", ">", 1]], "rationale": "quality",
            "net_alpha_pct": 2.5, "beta_neutral_alpha_pct": 1.5,
            "test_p": 0.001, "bonf_sig": 1, "hit_te": 0.6, "te_n": 100,
            "cluster_n": 60, "ticker_n": 30, "posterior_mean": 0.58,
            "skew_edge": 0,
        }])[0]
        note = integrate_calibrated.approved_candidate_note(candidate, {
            "decision_id": "D999", "_manifest_sha256": "a" * 64,
            "_source_artifact_sha256": "b" * 64,
            "expires_at": "2026-09-01T00:00:00Z",
        }, "2026-08-01T00:00:00Z")
        live.execute(
            "INSERT INTO mechanisms VALUES(?,?,?,?,?,?)",
            ("quality__month_21d", "active", "long", "position_1_4w", 0.58, note),
        )
        try:
            rate, source, count = capital_efficiency_audit._prospective_edge_rate(live)
            self.assertEqual(count, 1)
            self.assertEqual(source, "approved_live_artifact_median")
            self.assertAlmostEqual(rate, 0.025)
        finally:
            live.close()

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

    def test_daily_learning_agent_never_reruns_the_money_path(self) -> None:
        jobs = json.loads((ROOT / "cron/jobs.json").read_text()).get("jobs", [])
        learning = next(
            job for job in jobs if job.get("name") == "overseer-daily-learning-1630-et"
        )
        message = str((learning.get("payload") or {}).get("message", ""))
        step_one = message.split("Step 1", 1)[1].split("Step 2", 1)[0]
        self.assertIn("never rerun it", step_one)
        self.assertIn("Do NOT execute `trader-pass-deterministic.sh`", step_one)
        self.assertIn("If the chain is still running or failed", step_one)
        self.assertNotIn("execute `~/.openclaw/scripts/run-with-trace.sh", step_one)

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
        self.assertIn("validate_approval_manifest", source)
        preflight = (ROOT / "scripts/autotrade-preflight.py").read_text()
        self.assertIn("feature-store-point-in-time-contract", preflight)

    def test_weekly_rediscovery_is_analytics_only_and_creates_no_backup(self) -> None:
        source = (ROOT / "scripts/learning-rediscovery.sh").read_text()
        self.assertNotIn("PRE-REDISCOVERY", source)
        self.assertNotIn('run "backup db"', source)
        self.assertNotIn('run "integrate"', source)
        self.assertIn('historical_report_check.py"', source)
        self.assertIn("purged_walkforward_v2.json", source)
        self.assertIn("purged_walkforward_v2", source)
        self.assertNotIn('"$TI/promote_mechanisms.py"', source)
        self.assertNotIn('"$TI/mechanism_correlation.py"', source)
        self.assertNotIn('"$TI/mechanism_regime.py"', source)
        self.assertNotIn('"$TI/integrate_calibrated.py"', source)
        self.assertIn("live state was untouched", source)

    def test_canonical_architecture_matches_live_contract_boundaries(self) -> None:
        architecture = (ROOT / "SYSTEM_ARCHITECTURE.md").read_text()
        risk_source = (
            ROOT / "workspaces/risk/scripts/gate_risk_intents.py"
        ).read_text()
        self.assertIn("migrations:** through 0030", architecture)
        self.assertIn("Concurrent names:** ≤ 48", architecture)
        self.assertIn("MAX_POSITIONS = 48", risk_source)
        self.assertIn("These are **not one unified graph**", architecture)
        self.assertIn(
            "do not directly contain thesis, prediction,", architecture
        )

    def test_money_path_allows_bounded_bulk_risk_reduction(self) -> None:
        source = (ROOT / "scripts/trader-pass-deterministic.sh").read_text()
        self.assertIn(
            'run_step "execute_intent" 300 python3 workspaces/executor/scripts/execute_intent.py --max 48',
            source,
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

    def test_telegram_health_rejects_enabled_account_with_missing_token(self) -> None:
        config = {
            "channels": {
                "telegram": {
                    "accounts": {"resi": {"enabled": True}},
                }
            }
        }
        live = {
            "channels": {
                "telegram": {
                    "accounts": {
                        "resi": {
                            "tokenStatus": "configured_unavailable",
                            "running": False,
                            "connected": False,
                            "lastError": "token file unavailable",
                        }
                    }
                }
            }
        }
        with (
            mock.patch.object(system_health_sweep, "_load_json", return_value=config),
            mock.patch.object(system_health_sweep, "_resolve_openclaw", return_value="openclaw"),
            mock.patch.object(
                system_health_sweep,
                "_run",
                return_value=(0, json.dumps(live)),
            ),
        ):
            result = system_health_sweep.check_telegram()
        self.assertEqual(result["severity"], "crit")
        self.assertIn("configured_unavailable", result["detail"])

    def test_model_policy_rejects_agent_override_that_drops_fallbacks(self) -> None:
        config = json.loads((ROOT / "openclaw.json").read_text())
        config["agents"]["list"][0]["model"] = "openai/gpt-5.5"
        errors = system_health_sweep.validate_model_policy(config, ROOT)
        self.assertTrue(any("overrides model" in error for error in errors))

    def test_live_model_policy_has_fleet_fallbacks_and_provider_runtime(self) -> None:
        config = json.loads((ROOT / "openclaw.json").read_text())
        self.assertEqual(
            system_health_sweep.validate_model_policy(config, ROOT),
            [],
        )
        defaults = config["agents"]["defaults"]["model"]
        self.assertEqual(defaults["primary"], "openai/gpt-5.5")
        self.assertEqual(defaults["fallbacks"], ["openai/gpt-5.4"])
        self.assertEqual(
            system_health_sweep.validate_bootstrap_policy(config),
            [],
        )
        self.assertEqual(
            system_health_sweep.validate_operator_policy(config),
            [],
        )
        self.assertEqual(
            system_health_sweep.validate_reference_policy(config, ROOT),
            [],
        )

    def test_bootstrap_policy_rejects_context_truncation(self) -> None:
        config = json.loads((ROOT / "openclaw.json").read_text())
        config["agents"]["defaults"]["bootstrapTotalMaxChars"] = 100
        errors = system_health_sweep.validate_bootstrap_policy(config)
        self.assertTrue(any("bootstrap needs" in error for error in errors))

    def test_reference_policy_rejects_legacy_agent_resurrection(self) -> None:
        config = json.loads((ROOT / "openclaw.json").read_text())
        config["bindings"].append(
            {
                "match": {"channel": "telegram", "accountId": "resi"},
                "agentId": "resi",
            }
        )
        errors = system_health_sweep.validate_reference_policy(config, ROOT)
        self.assertTrue(any("unknown agent 'resi'" in error for error in errors))
        self.assertTrue(any("unknown Telegram account 'resi'" in error for error in errors))

    def test_reference_policy_rejects_deleted_skill(self) -> None:
        config = json.loads((ROOT / "openclaw.json").read_text())
        config["agents"]["list"][0]["skills"] = ["deleted-skill"]
        errors = system_health_sweep.validate_reference_policy(config, ROOT)
        self.assertTrue(any("deleted-skill" in error for error in errors))

    def test_token_health_rejects_foreground_only_synthetic_auth(self) -> None:
        status = {
            "auth": {
                "runtimeAuthRoutes": [
                    {
                        "provider": "openai",
                        "authProvider": "openai-codex",
                        "status": "usable",
                        "effective": {
                            "kind": "synthetic",
                            "detail": "codex-app-server",
                        },
                    }
                ],
                "oauth": {"providers": []},
            }
        }
        with (
            mock.patch.object(system_health_sweep, "_resolve_openclaw", return_value="openclaw"),
            mock.patch.object(
                system_health_sweep,
                "_run",
                return_value=(0, json.dumps(status)),
            ),
        ):
            result = system_health_sweep.check_tokens()
        self.assertEqual(result["severity"], "crit")
        self.assertRegex(
            result["detail"],
            r"OAuth-only fleet policy violated|Overseer cron auth profile missing",
        )

    def test_oauth_fleet_policy_rejects_api_tokens_and_missing_agent_profiles(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as td:
            root = Path(td)
            (root / "credentials").mkdir()
            (root / "cron").mkdir()
            (root / "agents/overseer/agent").mkdir(parents=True)
            config = {
                "auth": {
                    "order": {"openai": ["openai-codex:test"]},
                    "profiles": {
                        "openai-codex:test": {
                            "provider": "openai-codex", "mode": "oauth",
                        }
                    },
                },
                "agents": {"list": [{"id": "overseer"}, {"id": "researcher"}]},
            }
            (root / "openclaw.json").write_text(json.dumps(config))
            (root / "cron/jobs.json").write_text(json.dumps({"jobs": []}))
            (root / "credentials/openclaw-gateway.env").write_text("OPENAI_API_KEY=bad\n")
            (root / "agents/overseer/agent/auth-profiles.json").write_text(json.dumps({
                "version": 1,
                "profiles": {
                    "openai:default": {"provider": "openai", "type": "token"},
                    "openai-codex:test": {"provider": "openai-codex", "type": "oauth"},
                },
            }))
            errors = system_health_sweep.audit_codex_oauth_fleet(root)
        self.assertTrue(any("OPENAI_API_KEY" in error for error in errors))
        self.assertTrue(any("overseer: direct OpenAI" in error for error in errors))
        self.assertTrue(any("researcher: auth profile" in error for error in errors))

    def test_oauth_enforcer_creates_a_missing_configured_agent_store(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            (root / "agents/overseer/agent").mkdir(parents=True)
            (root / "credentials/token-backups").mkdir(parents=True)
            (root / "credentials/openclaw-gateway.env").write_text("")
            (root / "openclaw.json").write_text(json.dumps({
                "agents": {"list": [{"id": "overseer"}, {"id": "risk"}]},
            }))
            profile = {
                "version": 1,
                "profiles": {
                    "openai-codex:test": {
                        "provider": "openai-codex", "type": "oauth",
                        "access": "test", "refresh": "test",
                    },
                },
            }
            (root / "agents/overseer/agent/auth-profiles.json").write_text(
                json.dumps(profile)
            )
            result = enforce_codex_oauth.enforce(root, apply=True)
            risk = json.loads(
                (root / "agents/risk/agent/auth-profiles.json").read_text()
            )
            self.assertEqual(result["active_stores_seeded_with_codex_oauth"], 1)
            self.assertEqual(
                risk["profiles"]["openai-codex:test"]["provider"],
                "openai-codex",
            )

    def test_token_restore_cannot_resurrect_direct_openai_tokens(self) -> None:
        source = (ROOT / "scripts/token-restore.sh").read_text()
        self.assertIn('select(.value.provider != "openai"', source)
        self.assertIn('provider == "openai-codex"', source)

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
    def test_tm_git_reconciler_is_active_project_scoped_not_legacy_autotap(self) -> None:
        source = (ROOT / "scripts/reconcile-task-manager-with-git.py").read_text()
        self.assertIn('default="AutoTrade"', source)
        self.assertIn('project_scope(args.project)', source)
        self.assertIn('api/issues?sprint_id={sprint_id}', source)
        self.assertIn('credentials/task-manager-agent.json', source)
        self.assertIn('default_branch(repo)', source)
        self.assertNotIn('/home/aaron/repos/AutoTap-iosApp', source)

    def test_gateway_restart_requires_real_rpc_and_verified_scheduler_restore(self) -> None:
        compose = (ROOT / "docker-compose.openclaw.yml").read_text()
        restart = (ROOT / "scripts/safe-restart.sh").read_text()
        self.assertIn('test: ["CMD", "openclaw", "health"]', compose)
        self.assertNotIn('test: ["CMD", "openclaw", "gateway", "status"]', compose)
        self.assertIn('&& "${OPENCLAW_BIN}" health &>/dev/null', restart)
        self.assertIn("START_WAIT=120", restart)
        self.assertIn("consecutive >= 2", restart)
        self.assertIn("SECONDS + START_WAIT", restart)
        self.assertIn("--restore-cron-only", restart)
        self.assertIn("Restore-only cron recovery complete", restart)
        self.assertIn('[[ -n "${_next_wake}" ]]', restart)
        self.assertIn('recovery manifests retained', restart)
        self.assertIn('fail "Cron restore was not verified', restart)

    def test_token_restore_can_target_a_specific_agent_safely(self) -> None:
        source = (ROOT / "scripts/token-restore.sh").read_text()
        self.assertIn('--agent) agent_id="$2"', source)
        self.assertIn('[[ ! "$agent_id" =~ ^[a-z0-9_-]+$ ]]', source)
        self.assertIn('agents/${agent_id}/agent/auth-profiles.json', source)

    def test_auth_health_checks_real_overseer_store_not_foreground_synthetic_auth(self) -> None:
        source = (ROOT / "scripts/system-health-sweep.py").read_text()
        self.assertIn('[ocl, "models", "--agent", "overseer", "status", "--json"]', source)
        self.assertNotIn('fleet auth healthy via plugin-owned codex-app-server', source)

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
        builder = (ROOT / "workspaces/developer/scripts/snapshot_builder.py").read_text()
        provenance = (ROOT / "scripts/provenance-check.py").read_text()
        projector = Path(
            "/home/aaron/repos/lidi-solutions/scripts/snapshot-trader-intel.mjs"
        ).read_text()
        tracked_path = "public/solutions/trader_intel/app/data.json"

        self.assertIn("state/trader-intel-snapshot", pipeline)
        self.assertIn("state/trader-intel-snapshot", publisher)
        self.assertNotIn(tracked_path, pipeline)
        self.assertNotIn(tracked_path, publisher)
        self.assertIn("TRADER_INTEL_OUT_DIR", pipeline)
        self.assertIn("TRADER_INTEL_OUT_DIR", publisher)
        self.assertIn("canonical-state.json", pipeline)
        self.assertIn("canonical-state.json", publisher)
        self.assertIn("trader-intel-snapshot/canonical-state.json", builder)
        self.assertNotIn('trader-intel-snapshot/data.json")', builder)
        self.assertIn("TRADER_INTEL_BASE_FILE", pipeline)
        self.assertIn("TRADER_INTEL_BASE_FILE", publisher)
        self.assertIn("TRADER_INTEL_SKIP_DIST", projector)
        self.assertIn("BASE_FILE", projector)
        self.assertIn("audit_app_snapshot.py", publisher)
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

    def test_clean_live_repo_also_launches_in_an_isolated_worktree(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            repo = Path(temp_dir) / "sample"
            repo.mkdir()
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.test"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Guardrail Test"], check=True)
            (repo / "tracked.txt").write_text("committed\n")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
            issue = {"title": "Clean isolation", "branch": "issue-998-clean-isolation"}
            launch_repo, original, isolated = dwight_launch_from_issue.prepare_launch_repo(
                str(repo), "TM-998", "998", issue,
            )
            try:
                self.assertEqual(original, "main")
                self.assertEqual(dwight_launch_from_issue.git_current_branch(str(repo)), "main")
                self.assertEqual(dwight_launch_from_issue.git_current_branch(launch_repo), issue["branch"])
                self.assertIsNotNone(isolated)
                self.assertNotEqual(launch_repo, str(repo))
            finally:
                subprocess.run(
                    ["git", "-C", str(repo), "worktree", "remove", launch_repo],
                    check=True, capture_output=True,
                )

    def test_sequential_issue_worktrees_cannot_contaminate_each_other(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            repo = Path(temp_dir) / "sample"
            repo.mkdir()
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.test"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Guardrail Test"], check=True)
            (repo / "base.txt").write_text("base\n")
            subprocess.run(["git", "-C", str(repo), "add", "base.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)

            first, _, _ = dwight_launch_from_issue.prepare_launch_repo(
                str(repo), "TM-991", "991", {"title": "First isolated run"},
            )
            try:
                (Path(first) / "first-only.txt").write_text("must not leak\n")
                subprocess.run(["git", "-C", first, "add", "first-only.txt"], check=True)
                subprocess.run(["git", "-C", first, "commit", "-m", "first"], check=True, capture_output=True)
            finally:
                subprocess.run(
                    ["git", "-C", str(repo), "worktree", "remove", first],
                    check=True, capture_output=True,
                )

            second, _, _ = dwight_launch_from_issue.prepare_launch_repo(
                str(repo), "TM-992", "992", {"title": "Second isolated run"},
            )
            try:
                self.assertFalse((Path(second) / "first-only.txt").exists())
                self.assertEqual((repo / "base.txt").read_text(), "base\n")
                self.assertEqual(dwight_launch_from_issue.git_current_branch(str(repo)), "main")
                self.assertEqual(
                    dwight_launch_from_issue.git_current_branch(second),
                    "issue-992-second-isolated-run",
                )
            finally:
                subprocess.run(
                    ["git", "-C", str(repo), "worktree", "remove", second],
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
            self.assertEqual(job.get("sessionTarget"), "isolated", msg=job.get("name"))
            self.assertNotIn("sessionKey", job, msg=job.get("name"))
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
