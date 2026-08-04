#!/usr/bin/env python3
"""Read-only integrity gate for the canonical frozen historical replay."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import historical_snapshot as hs
import historical_walkforward as hw
import mechanism_backtest as mb


ROOT = Path.home() / ".openclaw"
DEFAULT_REPORT = ROOT / "state/historical-validation/purged_walkforward_v2.json"
DEFAULT_SNAPSHOT = ROOT / "state/research-snapshots/purged_walkforward_v2"


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(report_path: Path, snapshot_dir: Path) -> dict:
    policy = json.loads(hw.POLICY_PATH.read_text())
    spec = policy["historical_walkforward_development"]
    forward_spec = policy["forward_shadow_holdout"]
    report = json.loads(report_path.read_text())
    errors: list[str] = []

    expected = {
        "evaluation_version": spec["version"],
        "engine_sha256": _file_sha(Path(mb.__file__)),
        "runner_sha256": _file_sha(Path(hw.__file__)),
        "historical_spec_sha256": hw._sha256_json(spec),
        "forward_policy_sha256": hw._sha256_json(forward_spec),
        "data_snapshot_signature": hs.validate_snapshot(snapshot_dir),
        "promotion_authority": "none",
        "status": "complete",
    }
    for key, value in expected.items():
        if report.get(key) != value:
            errors.append(f"{key} mismatch")
    universe = report.get("universe_symbols")
    if not isinstance(universe, list) or len(universe) != report.get("universe_n"):
        errors.append("frozen universe symbols missing or count mismatch")
    elif hw._universe_sha(universe) != report.get("universe_sha256"):
        errors.append("frozen universe digest mismatch")

    if len(report.get("folds", [])) != len(spec["folds"]):
        errors.append("fold count mismatch")
    for fold in report.get("folds", []):
        coverage = fold.get("coverage")
        if not isinstance(coverage, dict):
            errors.append(f"{fold.get('id')}: missing coverage report")
            continue
        if coverage.get("loaded_n") != fold.get("names_with_cached_bars"):
            errors.append(f"{fold.get('id')}: loaded-name count mismatch")
        if coverage.get("load_errors"):
            errors.append(f"{fold.get('id')}: ticker load errors present")
        if coverage.get("pass2_load_errors"):
            errors.append(f"{fold.get('id')}: second-pass load errors present")
        if coverage.get("pass_mismatch"):
            errors.append(f"{fold.get('id')}: pass coverage mismatch")

    recomputed = hw.aggregate(report.get("folds", []), spec["aggregate_gate"])
    if report.get("aggregates") != recomputed:
        errors.append("stored aggregate differs from deterministic recomputation")
    stable = [
        {key: row[key] for key in (
            "id", "horizon", "direction", "kind", "eligible_folds",
            "positive_alpha_folds", "median_alpha_pct", "worst_alpha_pct",
            "median_beta_neutral_alpha_pct", "worst_beta_neutral_alpha_pct",
            "combined_p", "combined_bonferroni",
        )}
        for row in recomputed if row["stable_development_candidate"]
    ]
    if report.get("stable_development_candidates") != stable:
        errors.append("stored stable-candidate set differs from recomputation")
    try:
        frozen_candidates = hw.freeze_forward_candidate_set(
            report.get("folds", []), stable, spec, forward_spec,
        )
        if report.get("forward_candidate_set") != frozen_candidates:
            errors.append("stored forward-candidate set differs from recomputation")
    except (KeyError, RuntimeError, TypeError, ValueError) as exc:
        frozen_candidates = None
        errors.append(f"forward-candidate freeze invalid: {exc}")

    return {
        "ok": not errors,
        "errors": errors,
        "evaluation_version": report.get("evaluation_version"),
        "completed_at": report.get("completed_at"),
        "folds": len(report.get("folds", [])),
        "stable_development_candidates": stable,
        "forward_candidate_set": frozen_candidates,
        "promotion_authority": report.get("promotion_authority"),
        "snapshot_sha256": report.get("data_snapshot_signature", {}).get("sha256"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT)
    args = parser.parse_args()
    result = validate(args.report.resolve(), args.snapshot_dir.resolve())
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
