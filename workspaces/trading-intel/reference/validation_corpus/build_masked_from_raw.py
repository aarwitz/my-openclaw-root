#!/usr/bin/env python3
"""Build model-facing masked validation cases from internal raw cases.

Input:  raw_cases/*.json (detailed, real identifiers allowed)
Output: cases/*.json (masked_case_json contract)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
RAW_DIR = BASE / "raw_cases"
CASES_DIR = BASE / "cases"

DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\b")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _replace_ci(text: str, token: str, replacement: str) -> str:
    return re.sub(re.escape(token), replacement, text, flags=re.IGNORECASE)


def mask_world_change(raw: dict[str, Any]) -> str:
    override = raw.get("masked_world_change_override")
    if isinstance(override, str) and override.strip():
        text = override
    else:
        text = str(raw.get("raw_world_change", ""))

    entities = raw.get("entities", {}) if isinstance(raw.get("entities"), dict) else {}
    for company in entities.get("companies", []) if isinstance(entities.get("companies"), list) else []:
        if isinstance(company, str) and company.strip():
            text = _replace_ci(text, company, "a company")
    for person in entities.get("people", []) if isinstance(entities.get("people"), list) else []:
        if isinstance(person, str) and person.strip():
            text = _replace_ci(text, person, "an executive")
    for deal in entities.get("deals", []) if isinstance(entities.get("deals"), list) else []:
        if isinstance(deal, str) and deal.strip():
            text = _replace_ci(text, deal, "a transaction")
    for ticker in entities.get("tickers", []) if isinstance(entities.get("tickers"), list) else []:
        if isinstance(ticker, str) and ticker.strip():
            text = re.sub(rf"\b{re.escape(ticker)}\b", "a ticker", text, flags=re.IGNORECASE)

    text = DATE_RE.sub("a date", text)
    text = MONEY_RE.sub("a large amount", text)
    text = YEAR_RE.sub("a year", text)

    # Keep current validator happy while preserving semantics.
    return " ".join(text.lower().split())


def build_case(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": raw["id"],
        "case_class": raw["case_class"],
        "fake_date_variant": raw.get("fake_date_variant"),
        "masked_case_json": {
            "world_change": mask_world_change(raw),
            "sector_or_theme": str(raw["sector_or_theme"]).lower(),
            "structural_features": [str(x).lower() for x in raw["structural_features"]],
            "primary_source_class": str(raw["primary_source_class"]).lower(),
        },
        "model_decision_json": raw["model_decision_json"],
        "resolved_outcome_json": raw["resolved_outcome_json"],
        "passed": int(raw.get("passed", 0)),
        "created_at": raw["created_at"],
        "experiment_id": raw["experiment_id"],
    }


def main() -> int:
    CASES_DIR.mkdir(parents=True, exist_ok=True)

    raw_files = sorted(RAW_DIR.glob("*.json"))
    if not raw_files:
        print("no raw cases found")
        return 1

    built = 0
    for raw_path in raw_files:
        raw = json.loads(raw_path.read_text())
        out = build_case(raw)
        out_path = CASES_DIR / f"{out['id']}.json"
        out_path.write_text(json.dumps(out, indent=2) + "\n")
        built += 1

    print(f"built {built} masked cases from raw cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
