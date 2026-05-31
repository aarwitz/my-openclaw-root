#!/usr/bin/env python3
"""Validate validation corpus JSON files and produce index.json.

Usage:
  python3 validate_corpus.py
  python3 validate_corpus.py --strict-target

Default mode validates schema-like shape and emits index + summary.
--strict-target additionally enforces today_target thresholds.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
CASES = BASE / "cases"
SEEDS = BASE / "seeds"
INDEX = BASE / "index.json"
PROFILE = BASE / "target_profile.json"
QUALITY_MIN_SCORE = 45

ALLOWED_CLASS = {"winner", "negative_control", "post_cutoff"}
ALLOWED_DECISION = {"open", "no_trade", "block"}
ALLOWED_DIRECTION = {"long", "short", "none"}
ALLOWED_CONF = {"low", "medium", "high"}
ALLOWED_OUTCOME = {"thesis_confirmed", "thesis_refuted", "inconclusive"}
ID_RE = re.compile(r"^vc_(winner|negative_control|post_cutoff)_[a-z0-9_]+_[0-9]{3}(_fakedate)?$")
IDENTIFIER_RE = re.compile(r"\b[A-Z]{1,5}\b|\d{4}-\d{2}-\d{2}|\$\d")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def check_case(payload: dict[str, Any], path: Path) -> list[str]:
    errs: list[str] = []
    req = {
        "id",
        "case_class",
        "fake_date_variant",
        "masked_case_json",
        "model_decision_json",
        "resolved_outcome_json",
        "passed",
        "created_at",
        "experiment_id",
    }
    missing = req - payload.keys()
    if missing:
        errs.append(f"missing keys: {sorted(missing)}")
        return errs

    cid = payload["id"]
    cclass = payload["case_class"]
    if not isinstance(cid, str) or not ID_RE.match(cid):
        errs.append("invalid id format")
    if cclass not in ALLOWED_CLASS:
        errs.append("invalid case_class")

    mj = payload["masked_case_json"]
    if not isinstance(mj, dict):
        errs.append("masked_case_json must be object")
    else:
        for k in ("world_change", "sector_or_theme", "structural_features", "primary_source_class"):
            if k not in mj:
                errs.append(f"masked_case_json missing {k}")
        wc = str(mj.get("world_change", ""))
        if len(wc) < 20:
            errs.append("world_change too short")
        # Lightweight leakage heuristic; human review still required.
        if IDENTIFIER_RE.search(wc):
            errs.append("world_change may contain identifiers (uppercase ticker/date/$amount pattern)")

    md = payload["model_decision_json"]
    if not isinstance(md, dict):
        errs.append("model_decision_json must be object")
    else:
        if md.get("decision") not in ALLOWED_DECISION:
            errs.append("invalid decision")
        if md.get("direction") not in ALLOWED_DIRECTION:
            errs.append("invalid direction")
        if md.get("confidence_bucket") not in ALLOWED_CONF:
            errs.append("invalid confidence_bucket")
        rh = str(md.get("rationale_hash", ""))
        if not rh.startswith("sha256:"):
            errs.append("rationale_hash must start with sha256:")

    ro = payload["resolved_outcome_json"]
    if not isinstance(ro, dict):
        errs.append("resolved_outcome_json must be object")
    else:
        if ro.get("outcome") not in ALLOWED_OUTCOME:
            errs.append("invalid outcome")
        hd = ro.get("horizon_days")
        if not isinstance(hd, int) or hd <= 0:
            errs.append("horizon_days must be positive integer")
        emc = str(ro.get("external_mechanism_check", ""))
        if len(emc) < 10:
            errs.append("external_mechanism_check too short")

    if payload.get("passed") not in (0, 1):
        errs.append("passed must be 0 or 1")

    if cclass == "post_cutoff" and payload.get("fake_date_variant"):
        if not cid.endswith("_fakedate"):
            errs.append("fake-date variant id must end with _fakedate")

    return errs


def collect_files() -> list[Path]:
    files: list[Path] = []
    for root in (CASES, SEEDS):
        if root.exists():
            files.extend(sorted(root.glob("*.json")))
    return files


def assess_quality() -> dict[str, Any]:
    profiler = BASE / "assess_case_quality.py"
    if not profiler.exists():
        return {"available": False, "rows": []}
    result = subprocess.run(
        [sys.executable, str(profiler), str(CASES)],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        rows = json.loads(result.stdout) if result.stdout.strip() else []
    except json.JSONDecodeError:
        rows = []
    return {"available": True, "returncode": result.returncode, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict-target", action="store_true", help="enforce today target counts")
    args = parser.parse_args()

    profile = load_json(PROFILE)
    files = collect_files()
    if not files:
        print("no case files found")
        return 1

    seen_ids: set[str] = set()
    errors: list[str] = []

    counts = {
        "winner": 0,
        "negative_control": 0,
        "post_cutoff_substantive": 0,
        "post_cutoff_fake_date": 0,
    }

    index_rows: list[dict[str, Any]] = []
    quality = assess_quality()
    quality_by_id = {
        row.get("id"): row
        for row in quality.get("rows", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }

    for path in files:
        try:
            payload = load_json(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path.name}: invalid json: {exc}")
            continue

        row_errors = check_case(payload, path)
        if row_errors:
            for err in row_errors:
                errors.append(f"{path.name}: {err}")

        cid = payload.get("id")
        if isinstance(cid, str):
            if cid in seen_ids:
                errors.append(f"{path.name}: duplicate id {cid}")
            seen_ids.add(cid)

        cclass = payload.get("case_class")
        fvar = payload.get("fake_date_variant")
        if cclass == "winner":
            counts["winner"] += 1
        elif cclass == "negative_control":
            counts["negative_control"] += 1
        elif cclass == "post_cutoff":
            if fvar:
                counts["post_cutoff_fake_date"] += 1
            else:
                counts["post_cutoff_substantive"] += 1

        index_rows.append(
            {
                "id": payload.get("id"),
                "case_class": cclass,
                "fake_date_variant": fvar,
                "passed": payload.get("passed"),
                "quality_score": quality_by_id.get(payload.get("id"), {}).get("score"),
                "source_file": str(path.relative_to(BASE)),
            }
        )

    # Pairing check for each substantive post_cutoff case.
    substantive_ids = {
        r["id"]
        for r in index_rows
        if r["case_class"] == "post_cutoff" and not r["fake_date_variant"] and isinstance(r["id"], str)
    }
    fake_ids = {
        r["id"].replace("_fakedate", "")
        for r in index_rows
        if r["case_class"] == "post_cutoff" and r["fake_date_variant"] and isinstance(r["id"], str) and r["id"].endswith("_fakedate")
    }
    missing_pairs = sorted(substantive_ids - fake_ids)
    for sid in missing_pairs:
        errors.append(f"missing fake-date pair for {sid}")

    target_key = "today_target" if args.strict_target else "go_live_minimum"
    target = profile[target_key]

    checks = {
        "post_cutoff_substantive": (counts["post_cutoff_substantive"], target[f"post_cutoff_substantive_{'target' if args.strict_target else 'min'}"]),
        "post_cutoff_fake_date": (counts["post_cutoff_fake_date"], target[f"post_cutoff_fake_date_variants_{'target' if args.strict_target else 'min'}"]),
        "negative_control": (counts["negative_control"], target[f"negative_control_{'target' if args.strict_target else 'min'}"]),
        "winner": (counts["winner"], target[f"winner_{'target' if args.strict_target else 'min'}"]),
    }

    for label, (actual, minimum) in checks.items():
        if actual < minimum:
            errors.append(f"count check failed for {label}: {actual} < {minimum}")

    quality_warnings: list[str] = []
    if quality.get("available"):
        for row in quality.get("rows", []):
            if not isinstance(row, dict):
                continue
            score = row.get("score")
            cid = row.get("id")
            if isinstance(score, int) and score < QUALITY_MIN_SCORE:
                quality_warnings.append(f"quality score below minimum for {cid}: {score} < {QUALITY_MIN_SCORE}")
            for flag in row.get("flags", []):
                if isinstance(flag, str) and flag in {"possible leakage in world_change", "generic narrative language", "weak mechanism language"}:
                    quality_warnings.append(f"quality flag for {cid}: {flag}")

    summary = {
        "profile_id": profile["profile_id"],
        "mode": target_key,
        "counts": counts,
        "quality_available": bool(quality.get("available")),
        "quality_warnings": quality_warnings[:50],
        "total_files": len(index_rows),
        "errors": errors,
        "cases": sorted(index_rows, key=lambda x: (x["case_class"], x["id"] or "")),
    }
    INDEX.write_text(json.dumps(summary, indent=2) + "\n")

    if errors:
        print("validation failed")
        print(f"wrote: {INDEX}")
        for err in errors[:30]:
            print(f"- {err}")
        if len(errors) > 30:
            print(f"- ... and {len(errors) - 30} more")
        if quality_warnings:
            print(f"quality warnings: {len(quality_warnings)}")
        return 1

    print("validation passed")
    print(f"wrote: {INDEX}")
    print(json.dumps(counts, indent=2))
    if quality_warnings:
        print(f"quality warnings: {len(quality_warnings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
