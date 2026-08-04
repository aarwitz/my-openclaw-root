#!/usr/bin/env python3
"""Record and resolve the locked, no-authority forward mechanism shadow."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import historical_report_check as report_check
import mechanism_backtest as mb


ROOT = Path("/home/aaron/.openclaw")
DEFAULT_REPORT = ROOT / "state/historical-validation/purged_walkforward_v2.json"
DEFAULT_SNAPSHOT = ROOT / "state/research-snapshots/purged_walkforward_v2"
DEFAULT_OUTPUT = ROOT / "state/historical-validation/forward_shadow_v1.json"
LIVE_FEATURES = ROOT / "state/features.sqlite"
LIVE_CACHE = ROOT / "state/market-data-cache"
ET = ZoneInfo("America/New_York")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=path.stem + ".", suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _protocol(report_path: Path, snapshot_dir: Path) -> tuple[dict, dict]:
    verified = report_check.validate(report_path, snapshot_dir)
    if not verified["ok"]:
        raise RuntimeError("historical report gate failed: " + "; ".join(verified["errors"]))
    report = json.loads(report_path.read_text())
    frozen = report.get("forward_candidate_set") or {}
    protocol = {
        "schema_version": 1,
        "evaluation_class": "locked_forward_shadow",
        "recorder_sha256": _sha256_file(Path(__file__)),
        "report_sha256": _sha256_file(report_path),
        "candidate_set_sha256": frozen.get("candidate_set_sha256"),
        "universe_sha256": report.get("universe_sha256"),
        "start": frozen.get("start"),
        "minimum_end": frozen.get("minimum_end"),
        "minimum_sessions": frozen.get("minimum_sessions"),
        "promotion_authority": "none",
    }
    if not protocol["candidate_set_sha256"] or not protocol["start"]:
        raise RuntimeError("forward candidate set is not frozen")
    return report, protocol


def _eligible_decision_dates(
    spy: dict, start: str, now: datetime, minimum_end: str | None = None,
    minimum_sessions: int | None = None,
) -> list[str]:
    # At the 16:12 post-close job, today's aggregate can still be incomplete.
    # Score only strictly prior ET dates; this is a one-session-delayed recorder,
    # not an intraday backfill that can ingest a partial close.
    today = now.astimezone(ET).date().isoformat()
    eligible = [date for date in spy["dk"] if start <= date < today]
    if minimum_end is None or minimum_sessions is None:
        return eligible
    # The decision window closes on the earliest session satisfying both
    # preregistered duration constraints.  Continuing to add observations after
    # that point would create a perpetually unmatured, discretionarily sized
    # holdout.
    for index, date in enumerate(eligible):
        if date >= minimum_end and index + 1 >= int(minimum_sessions):
            return eligible[:index + 1]
    return eligible


def _load_spy() -> dict:
    """Normalize the recorder's frozen SPY bars without changing the evaluator."""
    bars = mb._backtest_prices("SPY")
    usable = [
        bar for bar in bars
        if bar.get("t") and bar.get("c") is not None and float(bar["c"]) > 0
    ]
    if not usable:
        raise RuntimeError("no cached SPY bars for the locked forward recorder")
    close = {str(bar["t"]): float(bar["c"]) for bar in usable}
    return {"close": close, "dk": sorted(close)}


def _decision_id(candidate: dict, ticker: str, date: str, direction: str) -> str:
    raw = "|".join((candidate["id"], candidate["horizon"], ticker, date, direction))
    return "fs-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def _record_decision(
    decisions: list[dict], candidate: dict, ticker: str, date: str, direction: str,
) -> None:
    decisions.append({
        "id": _decision_id(candidate, ticker, date, direction),
        "candidate_id": candidate["id"],
        "horizon": candidate["horizon"],
        "horizon_sessions": candidate["horizon_sessions"],
        "ticker": ticker,
        "decision_date": date,
        "direction": direction,
        "status": "pending_entry",
    })


def _score_date(
    conn: sqlite3.Connection,
    universe: list[str],
    candidates: list[dict],
    decision_date: str,
    expected_unavailable: set[str],
) -> tuple[list[dict], dict]:
    decisions: list[dict] = []
    if not candidates:
        return decisions, {
            "decision_date": decision_date,
            "candidate_count": 0,
            "loaded_symbols": 0,
            "active_bar_symbols": 0,
            "inactive_bar_symbols": 0,
            "expected_unavailable_symbols": sorted(expected_unavailable),
            "unexpected_load_errors": {},
        }
    cross_values: dict[str, list[tuple[float, str]]] = {
        candidate["id"]: [] for candidate in candidates if candidate["kind"] == "cross"
    }
    loaded = active = inactive = 0
    unavailable: list[str] = []
    errors: dict[str, str] = {}
    for ticker in universe:
        try:
            td = mb.load_ticker(conn, ticker)
        except (OSError, ValueError, sqlite3.DatabaseError) as exc:
            if ticker in expected_unavailable:
                unavailable.append(ticker)
            else:
                errors[ticker] = str(exc)
            continue
        if not td["dates"]:
            if ticker in expected_unavailable:
                unavailable.append(ticker)
            else:
                errors[ticker] = "no cached price history"
            continue
        loaded += 1
        if decision_date not in td["close"]:
            inactive += 1
            continue
        active += 1
        date_index = bisect.bisect_left(td["dates"], decision_date)
        for candidate in candidates:
            kind = candidate["kind"]
            conditions = candidate["conditions"]
            if kind == "cross":
                feature = conditions[0][0]
                value = mb.fval(td, feature, decision_date)
                if value is not None:
                    cross_values[candidate["id"]].append((float(value), ticker))
                continue
            if kind == "event":
                event_feature = conditions[0][0]
                if decision_date not in td["fkeys"].get(event_feature, []):
                    continue
            elif date_index % 5 != 0:  # exact historical state-scan cadence
                continue
            if mb.holds(td, conditions, decision_date):
                _record_decision(
                    decisions, candidate, ticker, decision_date,
                    candidate["direction"],
                )

    spy_dates = mb._backtest_prices("SPY")
    spy_index = {bar["t"]: index for index, bar in enumerate(spy_dates)}
    for candidate in candidates:
        if candidate["kind"] != "cross":
            continue
        # Historical cross-sectional portfolios rebalance every 21 SPY sessions
        # on an anchor fixed at observation 252.
        index = spy_index.get(decision_date)
        if index is None or index < 252 or (index - 252) % 21:
            continue
        values = sorted(cross_values[candidate["id"]])
        if len(values) < 20:
            continue
        count = max(2, int(0.2 * len(values)))
        variant = candidate["conditions"][0][1]
        if variant == "hi":
            selected = [(ticker, "long") for _, ticker in values[-count:]]
        elif variant == "lo":
            selected = [(ticker, "long") for _, ticker in values[:count]]
        else:
            selected = (
                [(ticker, "short") for _, ticker in values[:count]]
                + [(ticker, "long") for _, ticker in values[-count:]]
            )
        for ticker, direction in selected:
            _record_decision(decisions, candidate, ticker, decision_date, direction)
    if errors:
        sample = "; ".join(f"{key}: {value}" for key, value in sorted(errors.items())[:5])
        raise RuntimeError(f"forward universe load failed for {len(errors)} symbol(s): {sample}")
    return decisions, {
        "decision_date": decision_date,
        "candidate_count": len(candidates),
        "loaded_symbols": loaded,
        "active_bar_symbols": active,
        "inactive_bar_symbols": inactive,
        "expected_unavailable_symbols": sorted(unavailable),
        "unexpected_load_errors": {},
    }


def _resolve(decisions: list[dict], conn: sqlite3.Connection, spy: dict) -> None:
    by_ticker: dict[str, list[dict]] = {}
    for row in decisions:
        if row["status"] in {"pending_entry", "open"}:
            by_ticker.setdefault(row["ticker"], []).append(row)
    for ticker, rows in by_ticker.items():
        try:
            td = mb.load_ticker(conn, ticker)
        except (OSError, ValueError, sqlite3.DatabaseError):
            continue
        dates = td["dates"]
        for row in rows:
            entry_index = bisect.bisect_right(dates, row["decision_date"])
            if entry_index >= len(dates):
                continue
            entry_date = dates[entry_index]
            entry_price = td["close"][entry_date]
            if entry_price < mb.PRICE_FLOOR or td["dvol"].get(entry_date, 0) < mb.DV_FLOOR:
                row.update({
                    "status": "ineligible", "entry_date": entry_date,
                    "ineligible_reason": "entry_liquidity_or_price_floor",
                })
                continue
            row.update({
                "status": "open", "entry_date": entry_date,
                "entry_price": round(entry_price, 6),
            })
            exit_index = entry_index + int(row["horizon_sessions"])
            if exit_index >= len(dates):
                continue
            exit_date = dates[exit_index]
            stock_return = td["close"][exit_date] / entry_price - 1.0
            market_return = mb.spy_ret(spy, entry_date, exit_date)
            beta = mb.rolling_beta(td, spy, row["decision_date"])
            if market_return is None or beta is None:
                continue
            direction = 1.0 if row["direction"] == "long" else -1.0
            cost = mb.COST_RT + (
                int(row["horizon_sessions"]) * mb.SHORT_BORROW_PER_DAY
                if direction < 0 else 0.0
            )
            raw_excess = direction * (stock_return - market_return) - cost
            beta_excess = direction * (stock_return - beta * market_return) - cost
            row.update({
                "status": "resolved", "exit_date": exit_date,
                "exit_price": round(td["close"][exit_date], 6),
                "stock_return": round(stock_return, 8),
                "spy_return": round(market_return, 8),
                "trailing_beta": round(beta, 6),
                "cost": round(cost, 8),
                "raw_excess_return": round(raw_excess, 8),
                "beta_neutral_excess_return": round(beta_excess, 8),
            })


def run(
    report_path: Path, snapshot_dir: Path, output: Path, *, now: datetime | None = None,
) -> dict:
    now = now or datetime.now(ET)
    report, protocol = _protocol(report_path, snapshot_dir)
    frozen = report["forward_candidate_set"]

    mb.FEAT_DB = LIVE_FEATURES
    mb.CACHE_DIR = LIVE_CACHE
    mb.ALLOW_NETWORK = False
    mb._MACRO.clear()
    spy = _load_spy()
    eligible_dates = _eligible_decision_dates(
        spy, protocol["start"], now, protocol["minimum_end"],
        protocol["minimum_sessions"],
    )
    manifest = json.loads((snapshot_dir / "manifest.json").read_text())
    expected_unavailable = set(manifest.get("missing_price_symbols") or [])

    if output.exists():
        artifact = json.loads(output.read_text())
        if artifact.get("protocol") != protocol:
            raise RuntimeError("forward artifact protocol drift")
    else:
        artifact = {
            "protocol": protocol,
            "sessions_recorded": [],
            "decisions": [],
            "session_coverage": [],
            "promotion_authority": "none",
        }

    recorded = set(artifact["sessions_recorded"])
    missing = [date for date in eligible_dates if date not in recorded]
    if artifact.get("recording_complete_at"):
        if missing:
            raise RuntimeError("completed forward recorder is missing locked sessions")
        if any(
            row.get("status") in {"pending_entry", "open"}
            for row in artifact.get("decisions", [])
        ):
            raise RuntimeError("completed forward recorder contains unmatured decisions")
        return artifact
    if len(missing) > 2:
        raise RuntimeError(
            f"forward recorder gap is {len(missing)} sessions; refusing retrospective reconstruction"
        )

    conn = sqlite3.connect(LIVE_FEATURES, timeout=60)
    try:
        for decision_date in missing:
            new_rows, coverage = _score_date(
                conn, report["universe_symbols"], frozen["candidates"], decision_date,
                expected_unavailable,
            )
            existing_ids = {row["id"] for row in artifact["decisions"]}
            if any(row["id"] in existing_ids for row in new_rows):
                raise RuntimeError("duplicate forward decision id")
            artifact["decisions"].extend(new_rows)
            artifact["sessions_recorded"].append(decision_date)
            artifact.setdefault("session_coverage", []).append(coverage)
        _resolve(artifact["decisions"], conn, spy)
    finally:
        conn.close()

    artifact["sessions_recorded"] = sorted(set(artifact["sessions_recorded"]))
    artifact["session_coverage"] = sorted(
        artifact.get("session_coverage", []), key=lambda row: row["decision_date"],
    )
    artifact["decisions"].sort(key=lambda row: row["id"])
    window_closed = bool(
        artifact["sessions_recorded"]
        and len(artifact["sessions_recorded"]) >= int(protocol["minimum_sessions"])
        and artifact["sessions_recorded"][-1] >= protocol["minimum_end"]
    )
    artifact["decision_window_closed_at"] = (
        artifact["sessions_recorded"][-1] if window_closed else None
    )
    all_matured = bool(
        window_closed
        and not any(
            row.get("status") in {"pending_entry", "open"}
            for row in artifact["decisions"]
        )
    )
    if all_matured and not artifact.get("recording_complete_at"):
        artifact["recording_complete_at"] = now.astimezone(ET).isoformat()
    artifact["updated_at"] = now.astimezone(ET).isoformat()
    artifact["summary"] = {
        "sessions_recorded": len(artifact["sessions_recorded"]),
        "candidate_count": len(frozen["candidates"]),
        "decisions": len(artifact["decisions"]),
        "resolved": sum(row["status"] == "resolved" for row in artifact["decisions"]),
        "open": sum(row["status"] == "open" for row in artifact["decisions"]),
        "pending_entry": sum(row["status"] == "pending_entry" for row in artifact["decisions"]),
        "ineligible": sum(row["status"] == "ineligible" for row in artifact["decisions"]),
        "decision_window_closed": window_closed,
        "recording_complete": all_matured,
    }
    _atomic_json(output, artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        artifact = run(args.report, args.snapshot_dir, args.output)
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        today = datetime.now(ET).date().isoformat()
        # Before the locked window starts, an unfinished development replay is
        # an honest not-armed state. From the start onward it is a hard failure.
        try:
            policy = json.loads(
                (ROOT / "workspaces/trading-intel/config/evaluation_policy.json").read_text()
            )
            start = policy["forward_shadow_holdout"]["start"]
        except Exception:
            start = "0000-00-00"
        print(json.dumps({"ok": False, "status": "not_armed", "error": str(exc)}))
        return 0 if today < start else 1
    print(json.dumps({"ok": True, **artifact["summary"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
