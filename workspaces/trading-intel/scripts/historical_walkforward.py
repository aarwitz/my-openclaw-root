#!/usr/bin/env python3
"""Preregistered, purged multi-fold historical mechanism validation.

This is a development stress test, not a promotion path. It replays fixed,
non-overlapping hidden periods from ``config/evaluation_policy.json``, resumes
after interruption, and applies a second multiplicity correction across the
fold aggregates. It never writes ``discovered_mechanisms`` or the live ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist

import mechanism_backtest as mb
import historical_snapshot as hs


ROOT = Path(os.path.expanduser("~/.openclaw"))
POLICY_PATH = ROOT / "workspaces/trading-intel/config/evaluation_policy.json"
DEFAULT_OUTPUT = ROOT / "state/historical-validation/purged_walkforward_v2.json"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_json(value: dict) -> str:
    return _sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )


def _universe(conn: sqlite3.Connection) -> list[str]:
    return sorted(
        row[0] for row in conn.execute(
            "SELECT DISTINCT ticker FROM features WHERE source='price'"
        )
    )


def _universe_sha(universe: list[str]) -> str:
    return _sha256_bytes(("\n".join(universe) + "\n").encode())


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _data_snapshot_signature(snapshot_dir: Path | None = None) -> dict:
    """Fingerprint file identity and mutation state for every replay input family.

    The large caches are not copied. Instead, the run records the complete
    path/size/mtime manifest before evaluation and refuses a result if any
    relevant file changes before aggregation completes. Each worker also holds
    one SQLite read transaction across both passes.
    """
    if snapshot_dir is not None:
        return hs.validate_snapshot(snapshot_dir)
    feature_db = Path(mb.FEAT_DB)
    paths = [feature_db, Path(str(feature_db) + "-wal")]
    paths.extend(sorted(mb.CACHE_DIR.glob("massive_*_1d_*.json")))
    paths.extend(sorted(mb.CACHE_DIR.glob("fred_*.json")))
    entries = []
    for path in paths:
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        entries.append((str(path), int(stat.st_size), int(stat.st_mtime_ns)))
    encoded = json.dumps(entries, separators=(",", ":")).encode()
    return {
        "sha256": _sha256_bytes(encoded),
        "file_count": len(entries),
        "total_bytes": sum(entry[1] for entry in entries),
    }


def _validate_preregistration(spec: dict, universe: list[str]) -> None:
    if spec.get("promotion_authority") != "none":
        raise RuntimeError("historical development lane must have promotion_authority=none")
    if spec.get("universe_n") != len(universe):
        raise RuntimeError(
            f"frozen universe count changed: expected {spec.get('universe_n')}, got {len(universe)}"
        )
    actual_sha = _universe_sha(universe)
    if spec.get("universe_sha256") != actual_sha:
        raise RuntimeError(
            "frozen universe membership changed; preregister a new evaluation version"
        )
    unknown_exclusions = set(spec.get("excluded_features") or ()) - {
        *mb.GEN_FEATURES,
        *(condition[0] for mechanism in mb.SEEDS for condition in mechanism[2]),
    }
    if unknown_exclusions:
        raise RuntimeError(f"unknown excluded features: {sorted(unknown_exclusions)}")
    folds = spec.get("folds") or []
    if len(folds) < 3:
        raise RuntimeError("walk-forward validation requires at least three folds")
    prior_end = None
    for fold in folds:
        mb._validate_window(
            fold.get("train_start"), fold["test_start"], fold["test_end"]
        )
        if prior_end is not None and fold["test_start"] < prior_end:
            raise RuntimeError("test folds overlap")
        if fold["test_end"] > spec["price_data_cutoff"]:
            raise RuntimeError("test fold exceeds preregistered price-data cutoff")
        prior_end = fold["test_end"]


def _combined_p(p_values: list[float]) -> float:
    """One-sided Stouffer p-value for non-overlapping fold tests."""
    if not p_values:
        return 1.0
    normal = NormalDist()
    z_values = [normal.inv_cdf(1.0 - min(1.0 - 1e-12, max(1e-12, p))) for p in p_values]
    z = sum(z_values) / math.sqrt(len(z_values))
    return 1.0 - normal.cdf(z)


def aggregate(fold_reports: list[dict], gate: dict) -> list[dict]:
    grouped: dict[tuple[str, str, str, str], list[dict]] = {}
    for fold in fold_reports:
        for row in fold["results"]:
            key = (row["id"], row["horizon"], row["direction"], row["kind"])
            grouped.setdefault(key, []).append({"fold_id": fold["id"], **row})

    aggregates = []
    raw_p = []
    for (mid, horizon, direction, kind), rows in sorted(grouped.items()):
        eligible = [
            row for row in rows
            if row.get("alpha_te_pct") is not None
            and row.get("cluster_n", 0) >= gate["minimum_entry_date_clusters_per_fold"]
            and row.get("ticker_n", 0) >= gate["minimum_tickers_per_fold"]
        ]
        alphas = [float(row["alpha_te_pct"]) for row in eligible]
        beta_alphas = [
            float(row.get("beta_neutral_alpha_te_pct", row["alpha_te_pct"]))
            for row in eligible
        ]
        p_values = [float(row.get("test_p_raw", row["test_p"])) for row in eligible]
        combined = _combined_p(p_values)
        positive = sum(
            alpha > 0 and beta_alpha > 0
            for alpha, beta_alpha in zip(alphas, beta_alphas)
        )
        aggregate_row = {
            "id": mid,
            "horizon": horizon,
            "direction": direction,
            "kind": kind,
            "eligible_folds": len(eligible),
            "positive_alpha_folds": positive,
            "median_alpha_pct": round(statistics.median(alphas), 4) if alphas else None,
            "worst_alpha_pct": round(min(alphas), 4) if alphas else None,
            "best_alpha_pct": round(max(alphas), 4) if alphas else None,
            "median_beta_neutral_alpha_pct": (
                round(statistics.median(beta_alphas), 4) if beta_alphas else None
            ),
            "worst_beta_neutral_alpha_pct": (
                round(min(beta_alphas), 4) if beta_alphas else None
            ),
            "combined_p": combined,
            "folds": [
                {
                    "id": row["fold_id"],
                    "alpha_pct": row.get("alpha_te_pct"),
                    "beta_neutral_alpha_pct": row.get(
                        "beta_neutral_alpha_te_pct", row.get("alpha_te_pct")
                    ),
                    "p": row.get("test_p_raw", row.get("test_p")),
                    "clusters": row.get("cluster_n"),
                    "tickers": row.get("ticker_n"),
                    "within_fold_fdr": bool(row.get("sig", {}).get("fdr")),
                    "within_fold_bonferroni": bool(row.get("sig", {}).get("bonf")),
                }
                for row in rows
            ],
        }
        aggregates.append(aggregate_row)
        raw_p.append(combined)

    bonferroni = mb.st.bonferroni(raw_p, 0.05)
    fdr = mb.st.benjamini_hochberg(raw_p, 0.05)
    for row, bonf, bh in zip(aggregates, bonferroni, fdr):
        row["combined_bonferroni"] = bool(bonf)
        row["combined_fdr"] = bool(bh)
        row["stable_development_candidate"] = bool(
            row["eligible_folds"] >= gate["minimum_eligible_folds"]
            and row["positive_alpha_folds"] >= gate["minimum_positive_alpha_folds"]
            and row["median_alpha_pct"] is not None
            and row["median_alpha_pct"] > 0
            and row["median_beta_neutral_alpha_pct"] is not None
            and row["median_beta_neutral_alpha_pct"] > 0
            and row["combined_bonferroni"]
        )
    return aggregates


def freeze_forward_candidate_set(
    fold_reports: list[dict],
    stable_candidates: list[dict],
    historical_spec: dict,
    forward_spec: dict,
) -> dict:
    """Bind the exact executable definitions that enter the forward shadow.

    Generated thresholds vary by training fold. A bare mechanism id therefore
    is not an executable hypothesis. We deliberately freeze the definitions
    trained for the chronologically latest development fold; its thresholds
    were fixed before that fold's test period and are not refit after seeing
    the aggregate result.
    """
    source_fold_id = historical_spec["folds"][-1]["id"]
    source_fold = next(
        (fold for fold in fold_reports if fold.get("id") == source_fold_id),
        None,
    )
    if source_fold is None:
        raise RuntimeError("latest development fold missing from candidate freeze")
    definitions = {
        (row["id"], row["horizon"], row["direction"], row["kind"]): row
        for row in source_fold.get("results", [])
    }
    candidates = []
    for stable in sorted(
        stable_candidates,
        key=lambda row: (row["id"], row["horizon"], row["direction"], row["kind"]),
    ):
        key = (
            stable["id"], stable["horizon"],
            stable["direction"], stable["kind"],
        )
        definition = definitions.get(key)
        if definition is None:
            raise RuntimeError(f"forward definition missing for {stable['id']}")
        candidates.append({
            "id": stable["id"],
            "horizon": stable["horizon"],
            "horizon_sessions": mb.HORIZONS[stable["horizon"]],
            "direction": stable["direction"],
            "kind": stable["kind"],
            "conditions": definition.get("conds") or [],
            "rationale": definition.get("rationale"),
            "threshold_source_fold": source_fold_id,
            "threshold_information_end_exclusive": source_fold["test_start"],
        })
    return {
        "status": "frozen_no_trading_authority",
        "start": forward_spec["start"],
        "minimum_end": forward_spec["minimum_end"],
        "minimum_sessions": forward_spec["minimum_sessions"],
        "threshold_source_fold": source_fold_id,
        "candidate_set_sha256": _sha256_json(candidates),
        "candidates": candidates,
    }


def _new_report(
    spec: dict,
    universe: list[str],
    engine_sha: str,
    runner_sha: str,
    historical_spec_sha: str,
    policy_file_sha: str,
    forward_policy_sha: str,
    data_snapshot: dict,
) -> dict:
    return {
        "schema_version": 2,
        "evaluation_version": spec["version"],
        "status": "running",
        "development_only": True,
        "promotion_authority": "none",
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "completed_at": None,
        "engine_sha256": engine_sha,
        "runner_sha256": runner_sha,
        "historical_spec_sha256": historical_spec_sha,
        "policy_file_sha256": policy_file_sha,
        "forward_policy_sha256": forward_policy_sha,
        "data_snapshot_signature": data_snapshot,
        "price_data_cutoff": spec["price_data_cutoff"],
        "universe_n": len(universe),
        "universe_sha256": _universe_sha(universe),
        "universe_symbols": universe,
        "known_limitations": spec["known_limitations"],
        "excluded_features": spec["excluded_features"],
        "excluded_feature_reason": spec["excluded_feature_reason"],
        "folds": [],
        "aggregates": [],
        "stable_development_candidates": [],
    }


def _execute_fold(
    fold: dict, universe: list[str], spy: dict, excluded_features: list[str]
) -> dict:
    """Process-safe fold runner; all inputs and outputs are deterministic data."""
    mb.ALLOW_NETWORK = False
    coverage = {}
    results, base, mechanisms, nseen = mb.run(
        universe,
        spy,
        fold["test_start"],
        test_end=fold["test_end"],
        train_start=fold["train_start"],
        excluded_features=excluded_features,
        coverage_report=coverage,
    )
    return {
        **fold,
        "names_with_cached_bars": nseen,
        "coverage": coverage,
        "mechanism_count": len(mechanisms),
        "base_rate_beat_spy": base,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--universe-limit", type=int,
                        help="deterministic smoke-test subset; requires an explicit noncanonical output")
    parser.add_argument("--max-folds", type=int, help="run only the first N folds (partial report)")
    parser.add_argument("--workers", type=int, default=1,
                        help="independent fold processes; canonical evidence is locked to 1")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument(
        "--snapshot-dir", type=Path,
        help="immutable snapshot from historical_snapshot.py; required for canonical output",
    )
    args = parser.parse_args()

    canonical_output = args.output.resolve() == DEFAULT_OUTPUT.resolve()
    if canonical_output and args.snapshot_dir is None:
        raise RuntimeError("canonical historical replay requires --snapshot-dir")
    if canonical_output and args.workers != 1:
        raise RuntimeError("canonical historical replay requires --workers 1")
    if args.snapshot_dir is not None:
        snapshot_dir = args.snapshot_dir.resolve()
        mb.FEAT_DB = snapshot_dir / "features.sqlite"
        mb.CACHE_DIR = snapshot_dir / "cache"
    else:
        snapshot_dir = None

    policy = json.loads(POLICY_PATH.read_text())
    spec = policy["historical_walkforward_development"]
    forward_spec = policy["forward_shadow_holdout"]
    conn = sqlite3.connect(mb.FEAT_DB, timeout=60.0)
    universe = _universe(conn)
    conn.close()
    _validate_preregistration(spec, universe)

    if args.universe_limit is not None:
        if args.output.resolve() == DEFAULT_OUTPUT.resolve():
            raise RuntimeError("a subset cannot overwrite the canonical full-universe report")
        universe = universe[:max(1, args.universe_limit)]

    engine_sha = _sha256_file(Path(mb.__file__))
    runner_sha = _sha256_file(Path(__file__))
    historical_spec_sha = _sha256_json(spec)
    policy_file_sha = _sha256_file(POLICY_PATH)
    forward_policy_sha = _sha256_json(forward_spec)
    data_snapshot = _data_snapshot_signature(snapshot_dir)
    report = _new_report(
        spec, universe, engine_sha, runner_sha, historical_spec_sha,
        policy_file_sha, forward_policy_sha, data_snapshot
    )
    if not args.no_resume and args.output.exists():
        prior = json.loads(args.output.read_text())
        identity = (
            "evaluation_version", "engine_sha256", "runner_sha256",
            "historical_spec_sha256", "forward_policy_sha256",
            "universe_sha256", "data_snapshot_signature",
        )
        if all(prior.get(key) == report.get(key) for key in identity):
            report = prior

    completed = {fold["id"] for fold in report["folds"]}
    folds = spec["folds"][:args.max_folds] if args.max_folds else spec["folds"]
    prices = [
        bar for bar in mb._backtest_prices("SPY")
        if bar["t"] <= spec["price_data_cutoff"]
    ]
    if not prices:
        raise RuntimeError("no frozen SPY price data")
    spy = {"close": {bar["t"]: bar["c"] for bar in prices}, "dk": [bar["t"] for bar in prices]}

    for fold in folds:
        if fold["id"] in completed:
            print(f"RESUME {fold['id']}: already complete", flush=True)
    pending = [fold for fold in folds if fold["id"] not in completed]
    for fold in pending:
        print(
            f"QUEUE {fold['id']}: train={fold['train_start']}..{fold['test_start']} "
            f"test=[{fold['test_start']},{fold['test_end']}) names={len(universe)}",
            flush=True,
        )

    workers = max(1, min(int(args.workers), len(pending) or 1))
    if workers == 1:
        completed_reports = (
            _execute_fold(fold, universe, spy, spec["excluded_features"])
            for fold in pending
        )
        for fold_report in completed_reports:
            report["folds"].append(fold_report)
            _atomic_json(args.output, report)
            survivors = sum(row["sig"]["fdr"] for row in fold_report["results"])
            print(
                f"DONE {fold_report['id']}: rows={len(fold_report['results'])} "
                f"within-fold FDR={survivors}", flush=True,
            )
    elif pending:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _execute_fold, fold, universe, spy, spec["excluded_features"]
                ): fold for fold in pending
            }
            for future in as_completed(futures):
                fold_report = future.result()
                report["folds"].append(fold_report)
                order = {fold["id"]: i for i, fold in enumerate(spec["folds"])}
                report["folds"].sort(key=lambda row: order[row["id"]])
                _atomic_json(args.output, report)
                survivors = sum(row["sig"]["fdr"] for row in fold_report["results"])
                print(
                    f"DONE {fold_report['id']}: rows={len(fold_report['results'])} "
                    f"within-fold FDR={survivors}", flush=True,
                )

    final_snapshot = _data_snapshot_signature(snapshot_dir)
    if final_snapshot != report["data_snapshot_signature"]:
        report["status"] = "invalid_data_changed_during_run"
        report["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        report["final_data_snapshot_signature"] = final_snapshot
        report["aggregates"] = []
        report["stable_development_candidates"] = []
        _atomic_json(args.output, report)
        raise RuntimeError("replay input files changed during evaluation; result invalidated")

    report["aggregates"] = aggregate(report["folds"], spec["aggregate_gate"])
    report["stable_development_candidates"] = [
        {key: row[key] for key in (
            "id", "horizon", "direction", "kind", "eligible_folds",
            "positive_alpha_folds", "median_alpha_pct", "worst_alpha_pct",
            "median_beta_neutral_alpha_pct", "worst_beta_neutral_alpha_pct",
            "combined_p", "combined_bonferroni",
        )}
        for row in report["aggregates"] if row["stable_development_candidate"]
    ]
    report["forward_candidate_set"] = freeze_forward_candidate_set(
        report["folds"], report["stable_development_candidates"],
        spec, forward_spec,
    )
    report["status"] = "complete" if len(report["folds"]) == len(spec["folds"]) else "partial"
    report["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _atomic_json(args.output, report)

    stable = report["stable_development_candidates"]
    print(
        f"RESULT status={report['status']} folds={len(report['folds'])}/{len(spec['folds'])} "
        f"stable_development_candidates={len(stable)} promotion_authority=none",
        flush=True,
    )
    for row in stable:
        print(
            f"  {row['id']} {row['horizon']} {row['direction']} "
            f"positive={row['positive_alpha_folds']}/{row['eligible_folds']} "
            f"median_alpha={row['median_alpha_pct']:.3f}% p={row['combined_p']:.6g}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
