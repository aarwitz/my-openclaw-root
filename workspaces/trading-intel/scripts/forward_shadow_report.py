#!/usr/bin/env python3
"""Evaluate the immutable forward-shadow recorder into a promotion artifact.

This module has no trading or database write path.  It writes a deterministic
research artifact only after the preregistered decision window has closed and
every recorded position has matured.  Even then, promotion requires a separate
source-controlled operator manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import forward_shadow
import mechanism_backtest as mb
import promotion_gate


ROOT = Path("/home/aaron/.openclaw")
DEFAULT_RECORD = ROOT / "state/historical-validation/forward_shadow_v1.json"
DEFAULT_HISTORICAL_REPORT = (
    ROOT / "state/historical-validation/purged_walkforward_v2.json"
)
DEFAULT_SNAPSHOT = ROOT / "state/research-snapshots/purged_walkforward_v2"
DEFAULT_OUTPUT = ROOT / "state/research-artifacts/forward_shadow_v1.json"
PRIOR_N = 20.0


def _file_sha(path: Path) -> str:
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


def _max_drawdown(returns: list[float]) -> float | None:
    if not returns:
        return None
    equity = peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def _candidate_metric(candidate: dict, decisions: list[dict], window_closed: bool) -> dict:
    rows = [
        row for row in decisions
        if row["candidate_id"] == candidate["id"]
        and row["horizon"] == candidate["horizon"]
    ]
    unknown = sorted({
        str(row.get("status")) for row in rows
        if row.get("status") not in {"pending_entry", "open", "resolved", "ineligible"}
    })
    if unknown:
        raise RuntimeError(
            f"{candidate['id']}: unknown forward decision state(s): {unknown}"
        )
    unresolved = [row for row in rows if row["status"] in {"pending_entry", "open"}]
    resolved = [row for row in rows if row["status"] == "resolved"]
    raw_by_date: dict[str, list[float]] = defaultdict(list)
    beta_by_date: dict[str, list[float]] = defaultdict(list)
    for row in resolved:
        raw_by_date[row["decision_date"]].append(float(row["raw_excess_return"]))
        beta_by_date[row["decision_date"]].append(
            float(row["beta_neutral_excess_return"])
        )
    dates = sorted(set(raw_by_date) & set(beta_by_date))
    raw_series = [sum(raw_by_date[date]) / len(raw_by_date[date]) for date in dates]
    beta_series = [sum(beta_by_date[date]) / len(beta_by_date[date]) for date in dates]
    lag = int(candidate["horizon_sessions"])
    raw_mean, raw_p = mb._hac_mean_p(raw_series, lag)
    beta_mean, beta_p = mb._hac_mean_p(beta_series, lag)
    robust_p = max(raw_p, beta_p)
    wins = sum(value > 0 for value in beta_series)
    cluster_n = len(beta_series)
    posterior = (wins + 0.5 * PRIOR_N) / (cluster_n + PRIOR_N)
    return {
        "id": candidate["id"],
        "horizon": candidate["horizon"],
        "horizon_sessions": candidate["horizon_sessions"],
        "direction": candidate["direction"],
        "kind": candidate["kind"],
        "conditions": candidate["conditions"],
        "rationale": candidate.get("rationale"),
        "threshold_source_fold": candidate["threshold_source_fold"],
        "decision_count": len(rows),
        "resolved_count": len(resolved),
        "ineligible_count": sum(row["status"] == "ineligible" for row in rows),
        "unresolved_count": len(unresolved),
        "date_cluster_n": cluster_n,
        "ticker_n": len({row["ticker"] for row in resolved}),
        "raw_spy_alpha_pct": round(100.0 * raw_mean, 6) if raw_series else None,
        "beta_neutral_alpha_pct": round(100.0 * beta_mean, 6) if beta_series else None,
        "raw_spy_test_p": raw_p,
        "beta_neutral_test_p": beta_p,
        "robust_test_p": robust_p,
        "date_cluster_hit_rate": round(wins / cluster_n, 6) if cluster_n else None,
        "posterior_mean": round(posterior, 6),
        "max_drawdown_pct": (
            round(100.0 * _max_drawdown(beta_series), 6) if beta_series else None
        ),
        "average_positions_per_signal_date": (
            round(len(rows) / cluster_n, 6) if cluster_n else 0.0
        ),
        "matured": bool(window_closed and not unresolved),
    }


def evaluate(
    record_path: Path, historical_report_path: Path, snapshot_dir: Path,
    output_path: Path, *, now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    historical, protocol = forward_shadow._protocol(
        historical_report_path, snapshot_dir,
    )
    record = json.loads(record_path.read_text())
    if record.get("protocol") != protocol:
        raise RuntimeError("forward recorder protocol differs from frozen historical report")
    recorder_sha = _file_sha(record_path)
    if output_path.exists():
        try:
            prior = json.loads(output_path.read_text())
        except (OSError, json.JSONDecodeError):
            prior = None
        if (
            isinstance(prior, dict)
            and prior.get("status") == "complete"
            and prior.get("source_recorder_sha256") == recorder_sha
            and prior.get("historical_report_sha256") == protocol["report_sha256"]
        ):
            return prior
    sessions = record.get("sessions_recorded")
    coverage = record.get("session_coverage")
    if not isinstance(sessions, list) or sessions != sorted(set(sessions)):
        raise RuntimeError("forward sessions are missing, duplicated, or unsorted")
    if not isinstance(coverage, list) or len(coverage) != len(sessions):
        raise RuntimeError("forward session coverage is incomplete")
    if [row.get("decision_date") for row in coverage] != sessions:
        raise RuntimeError("forward session coverage does not align with sessions")
    if any(row.get("unexpected_load_errors") for row in coverage):
        raise RuntimeError("forward session coverage contains load errors")

    window_closed = bool(
        record.get("decision_window_closed_at")
        and len(sessions) >= int(protocol["minimum_sessions"])
        and sessions[-1] >= protocol["minimum_end"]
    )
    metrics = [
        _candidate_metric(candidate, record.get("decisions", []), window_closed)
        for candidate in historical["forward_candidate_set"]["candidates"]
    ]
    p_values = [
        row["robust_test_p"]
        if row["date_cluster_n"] >= 30 and row["ticker_n"] >= 20
        else 1.0
        for row in metrics
    ]
    bonferroni = mb.st.bonferroni(p_values, 0.05) if p_values else []
    for row, passed in zip(metrics, bonferroni):
        row["bonferroni"] = bool(passed)
        reasons = []
        if not row["matured"]:
            reasons.append("unmatured_forward_observations")
        if row["kind"] == "cross":
            reasons.append("cross_sectional_live_execution_unsupported")
        if row["date_cluster_n"] < 30:
            reasons.append("fewer_than_30_entry_date_clusters")
        if row["ticker_n"] < 20:
            reasons.append("fewer_than_20_tickers")
        if row["raw_spy_alpha_pct"] is None or row["raw_spy_alpha_pct"] <= 0:
            reasons.append("nonpositive_raw_spy_alpha")
        if (
            row["beta_neutral_alpha_pct"] is None
            or row["beta_neutral_alpha_pct"] <= 0
        ):
            reasons.append("nonpositive_beta_neutral_alpha")
        if not row["bonferroni"]:
            reasons.append("forward_bonferroni_not_passed")
        row["promotion_eligible"] = not reasons
        row["failure_reasons"] = reasons

    all_matured = bool(window_closed and all(row["matured"] for row in metrics))
    # An empty frozen candidate set is complete evidence of no promotable edge;
    # it is not a reason to keep an evaluation perpetually open.
    if not metrics:
        all_matured = window_closed
    status = "complete" if all_matured else (
        "maturing" if window_closed else "collecting"
    )
    promotion_candidates = []
    if status == "complete":
        for row in metrics:
            if not row["promotion_eligible"]:
                continue
            promotion_candidates.append({
                "id": row["id"],
                "horizon": row["horizon"],
                "direction": row["direction"],
                "kind": row["kind"],
                "source": "locked_forward_shadow_v1",
                "conditions": row["conditions"],
                "rationale": row["rationale"],
                "net_alpha_pct": row["raw_spy_alpha_pct"],
                "beta_neutral_alpha_pct": row["beta_neutral_alpha_pct"],
                "test_p": row["robust_test_p"],
                "bonf_sig": 1,
                "hit_te": row["date_cluster_hit_rate"],
                "te_n": row["resolved_count"],
                "cluster_n": row["date_cluster_n"],
                "ticker_n": row["ticker_n"],
                "posterior_mean": row["posterior_mean"],
                "skew_edge": int(
                    row["date_cluster_hit_rate"] is not None
                    and row["date_cluster_hit_rate"] < 0.5
                ),
            })
    digest = promotion_gate.candidate_set_sha256(promotion_candidates)
    prior_completed = None
    if output_path.exists():
        try:
            old = json.loads(output_path.read_text())
            if (
                old.get("status") == "complete"
                and old.get("source_recorder_sha256") == recorder_sha
            ):
                prior_completed = old.get("completed_at")
        except (OSError, json.JSONDecodeError):
            pass
    artifact = {
        "schema_version": 1,
        "status": status,
        "evaluation_class": "locked_forward_shadow",
        "development_only": False,
        "promotion_authority": "human_manifest_only" if status == "complete" else "none",
        "minimum_sessions_met": window_closed,
        "decision_window_closed_at": record.get("decision_window_closed_at"),
        "recorded_sessions": len(sessions),
        "minimum_sessions": protocol["minimum_sessions"],
        "minimum_end": protocol["minimum_end"],
        "all_observations_matured": all_matured,
        "historical_report_sha256": protocol["report_sha256"],
        "historical_candidate_set_sha256": protocol["candidate_set_sha256"],
        "universe_sha256": protocol["universe_sha256"],
        "source_recorder_sha256": recorder_sha,
        "recorder_engine_sha256": protocol["recorder_sha256"],
        "evaluator_sha256": _file_sha(Path(__file__)),
        "promotion_gate_sha256": _file_sha(Path(promotion_gate.__file__)),
        "candidate_metrics": metrics,
        "promotion_candidates": promotion_candidates,
        "candidate_set_sha256": digest,
        "completed_at": (
            prior_completed
            if status == "complete" and prior_completed
            else (now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                  if status == "complete" else None)
        ),
        "updated_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _atomic_json(output_path, artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    parser.add_argument(
        "--historical-report", type=Path, default=DEFAULT_HISTORICAL_REPORT,
    )
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        artifact = evaluate(
            args.record.resolve(), args.historical_report.resolve(),
            args.snapshot_dir.resolve(), args.output.resolve(),
        )
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        today = datetime.now(forward_shadow.ET).date().isoformat()
        try:
            policy = json.loads(
                (ROOT / "workspaces/trading-intel/config/evaluation_policy.json").read_text()
            )
            start = policy["forward_shadow_holdout"]["start"]
        except Exception:
            start = "0000-00-00"
        print(json.dumps({"ok": False, "status": "not_ready", "error": str(exc)}))
        return 0 if today < start else 1
    print(json.dumps({
        "ok": True,
        "status": artifact["status"],
        "recorded_sessions": artifact["recorded_sessions"],
        "candidate_count": len(artifact["candidate_metrics"]),
        "promotion_candidate_count": len(artifact["promotion_candidates"]),
        "promotion_authority": artifact["promotion_authority"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
