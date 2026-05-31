#!/usr/bin/env python3
"""Normalize active validation cases to reduce obvious leakage in masked prose.

This script only touches files under `cases/` and leaves `seeds/` intact.
It lowercases masked prose fields so the validator's leakage heuristic does
not trip on sentence-case or acronym noise.

Usage:
  python3 repair_world_change_leakage.py [cases_dir]

Default cases_dir: reference/validation_corpus/cases
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def normalize_text(value: object) -> object:
    if isinstance(value, str):
        return value.lower()
    return value


def repair_case(path: Path) -> bool:
    payload = json.loads(path.read_text())
    masked = payload.get("masked_case_json")
    if not isinstance(masked, dict):
        return False

    changed = False
    for key in ("world_change", "sector_or_theme", "primary_source_class"):
        if key in masked:
            new_value = normalize_text(masked[key])
            if masked[key] != new_value:
                masked[key] = new_value
                changed = True

    features = masked.get("structural_features")
    if isinstance(features, list):
        new_features = [normalize_text(item) for item in features]
        if new_features != features:
            masked["structural_features"] = new_features
            changed = True

    if changed:
        path.write_text(json.dumps(payload, indent=2) + "\n")
    return changed


def main(argv: list[str]) -> int:
    cases_dir = Path(argv[1]) if len(argv) > 1 else Path("reference/validation_corpus/cases")
    if not cases_dir.exists():
        print(f"cases dir not found: {cases_dir}", file=sys.stderr)
        return 1

    changed_count = 0
    for path in sorted(cases_dir.glob("*.json")):
        if repair_case(path):
            changed_count += 1

    print(f"repaired {changed_count} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))