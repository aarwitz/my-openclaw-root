#!/usr/bin/env python3
"""Deterministic regime classifier.

Implements `reference/regime_rules.md` §3-4 with the majority-of-available
aggregation rule. Fails loudly (exit non-zero + audit row) on broken config or
when more than two of four signals are missing; fails closed to `caution`
(writes a regime row with `fail_closed=true`) only when a majority cannot be
read or when `credit_spreads` is missing and another signal is at caution+.

Inputs:
  - Active regime_rules row from canonical SQLite (preferred)
  - Falls back to `sql/seeds/regime_rules.json` only if the seed flag is set

Outputs:
  - Writes one row to `regime` with full `signals_json` provenance.
  - Writes one row to `audits` describing the classification (or failure).
  - Exit code 0 on success (including fail_closed); non-zero on fail_loud.

This script is the only sanctioned writer of regime rows. The quant LLM turn
must call this script and then optionally write a human-readable rationale; it
must not score severity itself.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
SEED_PATH = Path(
    "/home/aaron/.openclaw/workspaces/trading-intel/sql/seeds/regime_rules.json"
)

SEVERITY = {"risk_on": 0, "neutral": 1, "caution": 2, "risk_off": 3, "crisis": 4}
INV_SEVERITY = {v: k for k, v in SEVERITY.items()}

EXIT_OK = 0
EXIT_FAIL_LOUD = 2


@dataclass
class SignalReading:
    name: str
    value: dict[str, Any] | None
    retrieved_at: str | None
    source: str
    state: str | None = None
    fresh: bool = False
    error: str | None = None


@dataclass
class ClassifierResult:
    current: str
    fail_closed: bool
    partial: bool
    missing_signals: list[str]
    notes: dict[str, Any] = field(default_factory=dict)
    severity: int = 0


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_active_rules(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, rule_version, thresholds_json FROM regime_rules "
        "WHERE rule_version = 'live' ORDER BY effective_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("no active regime_rules row (rule_version='live') in DB")
    try:
        thresholds = json.loads(row[2])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"regime_rules thresholds_json malformed: {exc}") from exc
    if "aggregation" not in thresholds or "signals" not in thresholds:
        raise RuntimeError("regime_rules thresholds_json missing required sections")
    return thresholds


def load_seed_rules() -> dict[str, Any]:
    if not SEED_PATH.exists():
        raise RuntimeError(f"seed regime_rules missing at {SEED_PATH}")
    return json.loads(SEED_PATH.read_text())["thresholds_json"]


# ---------------------------------------------------------------------------
# Signal readers
#
# Each reader returns a SignalReading. Connectors that are not yet wired
# return SignalReading(value=None, error="connector_not_wired"). These are
# treated as missing for the aggregation purposes and surfaced in
# notes.missing_signals + the audit row, so the operator (and Bessent) can
# fix them.
# ---------------------------------------------------------------------------


def _hours_old(retrieved_at_iso: str | None) -> float | None:
    if not retrieved_at_iso:
        return None
    try:
        dt = datetime.fromisoformat(retrieved_at_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


# Connector library: shared FRED + Alpaca helpers under trading-intel/scripts/connectors.
# Imported lazily so a broken/missing connector for one signal does not crash the script.
_CONNECTORS_PATH = Path(
    "/home/aaron/.openclaw/workspaces/trading-intel/scripts"
)
if str(_CONNECTORS_PATH) not in sys.path:
    sys.path.insert(0, str(_CONNECTORS_PATH))


def _safe(fn, name: str, source: str) -> SignalReading:
    try:
        from connectors._http import ConnectorError  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        return SignalReading(
            name=name, value=None, retrieved_at=None, source=source,
            error=f"connector_import_failed: {exc!r}",
        )
    try:
        value = fn()
        return SignalReading(
            name=name,
            value=value,
            retrieved_at=value.get("retrieved_at"),
            source=value.get("source", source),
        )
    except ConnectorError as exc:
        return SignalReading(
            name=name, value=None, retrieved_at=None, source=source,
            error=f"connector_error: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        return SignalReading(
            name=name, value=None, retrieved_at=None, source=source,
            error=f"connector_unexpected: {exc!r}",
        )


def read_spy_trend() -> SignalReading:
    from connectors.marketdata import spy_trend
    return _safe(spy_trend, "spy_trend", "alpaca_market_data:SPY:1Day")


def read_credit_spreads() -> SignalReading:
    from connectors.fred import credit_spreads
    return _safe(credit_spreads, "credit_spreads", "fred:BAMLH0A0HYM2")


def read_vix_term_structure() -> SignalReading:
    # Yahoo is primary (reliable from this host); FRED is fallback.
    def _read():
        from connectors._http import ConnectorError
        try:
            from connectors.yahoo import vix_term_structure as y
            return y()
        except ConnectorError:
            from connectors.fred import vix_levels
            return vix_levels()
    return _safe(_read, "vix_term_structure", "yahoo:^VIX,^VIX3M")


def read_yield_curve() -> SignalReading:
    # FRED primary; Yahoo proxy (10y-5y) is a degraded fallback.
    def _read():
        from connectors._http import ConnectorError
        try:
            from connectors.fred import yield_curve
            return yield_curve()
        except ConnectorError:
            from connectors.yahoo import yield_curve_proxy
            return yield_curve_proxy()
    return _safe(_read, "yield_curve", "fred:T10Y2Y|yahoo_proxy")


SIGNAL_READERS = {
    "spy_trend": read_spy_trend,
    "credit_spreads": read_credit_spreads,
    "vix_term_structure": read_vix_term_structure,
    "yield_curve": read_yield_curve,
}


def collect_signals(
    rules: dict[str, Any],
    overrides: dict[str, SignalReading] | None = None,
) -> list[SignalReading]:
    freshness_max_h = float(rules.get("freshness_hours_max", 36))
    readings: list[SignalReading] = []
    for name, reader in SIGNAL_READERS.items():
        if overrides and name in overrides:
            reading = overrides[name]
        else:
            try:
                reading = reader()
            except Exception as exc:  # pragma: no cover - defensive
                reading = SignalReading(
                    name=name,
                    value=None,
                    retrieved_at=None,
                    source=SIGNAL_READERS[name].__name__,
                    error=f"reader_exception:{exc!s}",
                )
        if reading.value is None:
            reading.fresh = False
        else:
            age_h = _hours_old(reading.retrieved_at)
            reading.fresh = age_h is not None and age_h <= freshness_max_h
        if reading.fresh and reading.state is None:
            reading.state = classify_signal_state(name, reading.value, rules)
        readings.append(reading)
    return readings


def classify_signal_state(
    signal_name: str, value: dict[str, Any], rules: dict[str, Any]
) -> str:
    """Map a fresh signal value to one of the five severity states.

    For now this delegates to a per-signal helper because each signal has a
    distinct shape. When connectors are wired, populate the value dicts with
    the keys the helpers expect; until then the helpers return None which is
    treated as missing by the caller.
    """
    helper = {
        "spy_trend": _state_spy_trend,
        "credit_spreads": _state_credit_spreads,
        "vix_term_structure": _state_vix_term,
        "yield_curve": _state_yield_curve,
    }[signal_name]
    return helper(value, rules["signals"][signal_name]["states"])


def _state_spy_trend(value: dict[str, Any], states: dict[str, Any]) -> str:
    pct = value["close_vs_sma200_pct"]
    sma50_gt = value.get("sma50_gt_sma200", False)
    falling_sessions = value.get("sma50_lt_sma200_falling_sessions", 0)
    if pct >= states["risk_on"]["close_vs_sma200_pct_min"] and (
        not states["risk_on"].get("require_sma50_gt_sma200") or sma50_gt
    ):
        return "risk_on"
    if 0.0 <= pct < states["neutral"]["close_vs_sma200_pct_range"][1]:
        return "neutral"
    crisis = states["crisis"]
    if pct <= crisis["close_vs_sma200_pct_max"] or (
        not sma50_gt
        and falling_sessions >= crisis["or"]["sma50_lt_sma200_falling_sessions_min"]
    ):
        return "crisis"
    if -10.0 <= pct < -5.0:
        return "risk_off"
    return "caution"


def _state_credit_spreads(value: dict[str, Any], states: dict[str, Any]) -> str:
    bps = value["level_bps"]
    delta = value["delta_20d_bps"]
    if bps >= 900 or delta >= 200:
        return "crisis"
    if 600 <= bps < 900:
        return "risk_off"
    if 450 <= bps < 600 or delta >= 75:
        return "caution"
    if 350 <= bps < 450 and delta <= 50:
        return "neutral"
    if bps <= 350 and delta <= 25:
        return "risk_on"
    return "caution"


def _state_vix_term(value: dict[str, Any], states: dict[str, Any]) -> str:
    ratio = value["ratio"]
    vix_spot = value.get("vix_spot")
    if ratio > 1.20 or (vix_spot is not None and vix_spot > 40):
        return "crisis"
    if 1.05 < ratio <= 1.20:
        return "risk_off"
    if 0.95 < ratio <= 1.05:
        return "caution"
    if 0.90 < ratio <= 0.95:
        return "neutral"
    return "risk_on"


def _state_yield_curve(value: dict[str, Any], states: dict[str, Any]) -> str:
    bps = value["level_bps"]
    slope = value.get("slope_60d", 0.0)
    resteepen_days = value.get("resteepening_from_deep_inversion_days")
    if bps < -100 or (
        resteepen_days is not None
        and resteepen_days <= states["crisis"]["or"]["resteepening_from_deep_inversion_days_max"]
    ):
        return "crisis"
    if -100 <= bps < -25:
        return "risk_off"
    if -25 <= bps < 0:
        return "caution"
    if 0 <= bps < 50:
        return "neutral"
    if bps >= 50 and slope >= 0:
        return "risk_on"
    return "neutral"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate(
    readings: list[SignalReading], rules: dict[str, Any]
) -> ClassifierResult:
    agg = rules["aggregation"]
    min_valid = int(agg.get("min_valid_signals_for_normal_classification", 3))
    breadth_fraction = float(agg.get("breadth_bump_fraction", 0.75))

    valid = [r for r in readings if r.fresh and r.state is not None]
    missing = sorted(r.name for r in readings if not r.fresh)
    valid_by_name = {r.name: r for r in valid}

    notes: dict[str, Any] = {}

    if len(valid) < min_valid:
        notes["fail_closed_reason"] = "majority_signals_missing"
        return ClassifierResult(
            current="caution",
            fail_closed=True,
            partial=True,
            missing_signals=missing,
            notes=notes,
            severity=SEVERITY["caution"],
        )

    # Credit override
    credit = valid_by_name.get("credit_spreads")
    if credit is not None and SEVERITY[credit.state] == 4:
        return ClassifierResult(
            current="crisis",
            fail_closed=False,
            partial=bool(missing),
            missing_signals=missing,
            notes={"credit_override": True},
            severity=SEVERITY["crisis"],
        )
    if credit is None:
        any_caution_plus = any(SEVERITY[r.state] >= 2 for r in valid)
        if any_caution_plus:
            notes.update(
                {
                    "fail_closed_reason": "credit_spreads_missing_and_any_other_caution_or_worse",
                    "credit_unknown": True,
                }
            )
            return ClassifierResult(
                current="caution",
                fail_closed=True,
                partial=True,
                missing_signals=missing,
                notes=notes,
                severity=SEVERITY["caution"],
            )

    worst = max(SEVERITY[r.state] for r in valid)
    breadth_count = sum(1 for r in valid if SEVERITY[r.state] >= 2)
    if breadth_count / len(valid) >= breadth_fraction:
        agg_sev = min(4, worst + 1)
        notes["breadth_bump"] = True
    else:
        agg_sev = worst

    return ClassifierResult(
        current=INV_SEVERITY[agg_sev],
        fail_closed=False,
        partial=bool(missing),
        missing_signals=missing,
        notes=notes,
        severity=agg_sev,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def write_regime_row(
    conn: sqlite3.Connection,
    result: ClassifierResult,
    readings: list[SignalReading],
    experiment_id: str | None,
) -> str:
    signals_json = {
        "partial": result.partial,
        "missing_signals": result.missing_signals,
        "fail_closed": result.fail_closed,
        "notes": result.notes,
        "signal_states": {
            r.name: {
                "state": r.state,
                "fresh": r.fresh,
                "retrieved_at": r.retrieved_at,
                "source": r.source,
                "error": r.error,
            }
            for r in readings
        },
        "freshness_hours_max": 36,
    }
    row_id = f"regime_{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO regime (id, determined_at, determined_by, current, signals_json, implications_json) "
        "VALUES (?, ?, 'quant', ?, ?, NULL)",
        (row_id, _now_utc_iso(), result.current, json.dumps(signals_json)),
    )
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise, journal_ref, experiment_id) "
        "VALUES (?, ?, 'quant', 'regime', ?, 'regime_classified', NULL, ?, ?, NULL, ?)",
        (
            f"audit_{uuid.uuid4().hex[:12]}",
            _now_utc_iso(),
            row_id,
            result.current,
            ("fail_closed=" + str(result.fail_closed) + " missing=" + ",".join(result.missing_signals))[:500],
            experiment_id,
        ),
    )
    conn.commit()
    return row_id


def write_failure_audit(conn: sqlite3.Connection, reason: str, detail: str) -> None:
    try:
        conn.execute(
            "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
            "before_state, after_state, rationale_concise, journal_ref, experiment_id) "
            "VALUES (?, ?, 'system', 'regime', 'classifier', 'regime_failed', NULL, ?, ?, NULL, NULL)",
            (
                f"audit_{uuid.uuid4().hex[:12]}",
                _now_utc_iso(),
                reason,
                detail[:500],
            ),
        )
        conn.commit()
    except Exception:
        traceback.print_exc(file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default=str(DB_PATH), help="Path to canonical SQLite DB"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the result without writing to DB",
    )
    parser.add_argument(
        "--fixture",
        help="Path to JSON fixture with signals overrides (for tests)",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Read rules from seed file instead of DB (bootstrap only)",
    )
    parser.add_argument(
        "--experiment-id", default=None, help="experiment_id tag for outputs"
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    conn: sqlite3.Connection | None = None
    if not args.dry_run and not db_path.exists():
        print(f"ERROR: db missing at {db_path}", file=sys.stderr)
        return EXIT_FAIL_LOUD

    try:
        if args.seed:
            rules = load_seed_rules()
        else:
            if args.dry_run and not db_path.exists():
                rules = load_seed_rules()
            else:
                conn = sqlite3.connect(db_path)
                rules = load_active_rules(conn)
    except Exception as exc:
        msg = f"rules_load_failed: {exc}"
        if conn is not None:
            write_failure_audit(conn, "regime_rules_unloadable", msg)
        print(f"ERROR: {msg}", file=sys.stderr)
        return EXIT_FAIL_LOUD

    overrides: dict[str, SignalReading] | None = None
    if args.fixture:
        try:
            fixture = json.loads(Path(args.fixture).read_text())
            overrides = {
                name: SignalReading(
                    name=name,
                    value=payload.get("value"),
                    retrieved_at=payload.get("retrieved_at"),
                    source=payload.get("source", "fixture"),
                    state=payload.get("state"),
                    error=payload.get("error"),
                )
                for name, payload in fixture.items()
            }
        except Exception as exc:
            print(f"ERROR: fixture_load_failed: {exc}", file=sys.stderr)
            return EXIT_FAIL_LOUD

    try:
        readings = collect_signals(rules, overrides=overrides)
        valid_count = sum(1 for r in readings if r.fresh)
        if valid_count == 0 and overrides is None:
            # All readers returned no data (e.g. connectors not wired). This
            # is a fail-loud condition for production runs; for --dry-run we
            # still emit a fail_closed result so the operator sees the shape.
            if not args.dry_run:
                if conn is not None:
                    write_failure_audit(
                        conn,
                        "all_signals_unavailable",
                        "no signal connectors returned data; fix Bessent connectors",
                    )
                print(
                    "ERROR: all signal connectors returned no data; fail-loud (see audits)",
                    file=sys.stderr,
                )
                return EXIT_FAIL_LOUD
        result = aggregate(readings, rules)
    except Exception as exc:
        if conn is not None:
            write_failure_audit(conn, "classifier_runtime_error", str(exc))
        print(f"ERROR: classifier_runtime_error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return EXIT_FAIL_LOUD

    output = {
        "current": result.current,
        "fail_closed": result.fail_closed,
        "partial": result.partial,
        "missing_signals": result.missing_signals,
        "notes": result.notes,
        "signal_states": {r.name: r.state for r in readings},
    }
    print(json.dumps(output, indent=2, sort_keys=True))

    if args.dry_run:
        return EXIT_OK
    if conn is None:
        # No DB connection (shouldn't happen given guards above)
        return EXIT_FAIL_LOUD
    row_id = write_regime_row(conn, result, readings, args.experiment_id)
    print(f"regime_id={row_id}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
