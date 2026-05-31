#!/usr/bin/env python3
"""Heuristically assess whether a validation case is anonymized but still readable.

This is not a replacement for human review. It produces a compact quality report
so you can tell the difference between:
- legible real situations with the identifiers removed, and
- generic or over-redacted text that no longer describes a concrete event.

Usage:
  python3 assess_case_quality.py [cases_dir]

Default cases_dir: reference/validation_corpus/cases
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


IDENTIFIER_RE = re.compile(r"\b[A-Z]{1,5}\b|\d{4}-\d{2}-\d{2}|\$\d")
MECHANISM_WORDS = {
    "guidance",
    "revenue",
    "demand",
    "supply",
    "allowance",
    "restructuring",
    "procurement",
    "capacity",
    "approval",
    "launch",
    "filing",
    "trial",
    "earnings",
    "cash",
    "margin",
    "throughput",
    "revenue",
    "cost",
    "debt",
    "office",
    "energy",
}
GENERIC_WORDS = {
    "company",
    "narrative",
    "growth",
    "update",
    "signal",
    "story",
    "pattern",
    "generic",
    "strong",
    "rapid",
    "large",
    "material",
}


def load_case(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def tokenise(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def assess_case(payload: dict[str, Any]) -> dict[str, Any]:
    masked = payload.get("masked_case_json", {})
    world_change = str(masked.get("world_change", ""))
    features = masked.get("structural_features", [])
    source_class = str(masked.get("primary_source_class", ""))
    sector = str(masked.get("sector_or_theme", ""))
    text = " ".join([world_change, sector, source_class, " ".join(features) if isinstance(features, list) else ""])

    tokens = tokenise(text)
    unique_tokens = len(set(tokens))
    token_count = len(tokens)

    mechanism_hits = sum(1 for word in MECHANISM_WORDS if word in text.lower())
    generic_hits = sum(1 for word in GENERIC_WORDS if word in text.lower())

    leakage_hits = 1 if IDENTIFIER_RE.search(world_change) else 0
    feature_count = len(features) if isinstance(features, list) else 0

    # Simple heuristic score from 0-100.
    score = 100
    score -= leakage_hits * 35
    score -= max(0, 3 - feature_count) * 10
    score -= max(0, 12 - unique_tokens) * 2
    score -= max(0, 18 - token_count) * 2
    score -= max(0, 3 - mechanism_hits) * 8
    score -= generic_hits * 5
    score = max(0, min(100, score))

    flags = []
    if leakage_hits:
        flags.append("possible leakage in world_change")
    if feature_count < 3:
        flags.append("thin structural_features")
    if mechanism_hits < 3:
        flags.append("weak mechanism language")
    if generic_hits >= 3:
        flags.append("generic narrative language")
    if token_count < 20:
        flags.append("too short to be concrete")

    return {
        "id": payload.get("id"),
        "case_class": payload.get("case_class"),
        "score": score,
        "flags": flags,
        "feature_count": feature_count,
        "token_count": token_count,
        "mechanism_hits": mechanism_hits,
    }


def main(argv: list[str]) -> int:
    cases_dir = Path(argv[1]) if len(argv) > 1 else Path("reference/validation_corpus/cases")
    if not cases_dir.exists():
        print(f"cases dir not found: {cases_dir}", file=sys.stderr)
        return 1

    rows = []
    for path in sorted(cases_dir.glob("*.json")):
        try:
            payload = load_case(path)
        except Exception as exc:  # noqa: BLE001
            rows.append({"path": path.name, "error": str(exc)})
            continue
        rows.append(assess_case(payload))

    rows.sort(key=lambda r: (r.get("score", -1), r.get("id") or ""))
    print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))