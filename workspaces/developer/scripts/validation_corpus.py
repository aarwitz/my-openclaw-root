#!/usr/bin/env python3
"""Audit and atomically ingest the masked AutoTrade evaluation corpus.

This is deliberately separate from the named episode library.  Episodes are a
research-memory surface; ``validation_cases`` is a blinded evaluation surface.
An empty corpus is structurally valid but cannot clear the reasoning gate.

The default command is read-only.  Ingestion accepts only a human-approved,
fully evaluated batch and rolls the entire batch back if any row is malformed,
unpaired, hindsight-inconsistent, or identifier-bearing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from developer_db import audit, now_iso  # noqa: E402

DEFAULT_DB = Path("/home/aaron/.openclaw/state/trading-intel.sqlite")
MIN_POST_CUTOFF = 30
MIN_NEGATIVE_CONTROL = 60
MIN_POST_ACCURACY = 0.57
MAX_NEGATIVE_FALSE_POSITIVE = 0.25
MIN_FAKE_DATE_INVARIANCE = 0.95
MAX_ECE = 0.10

CLASSES = {"winner", "negative_control", "post_cutoff"}
DECISIONS = {"open", "no_trade", "block"}
DIRECTIONS = {"long", "short", "none"}
OUTCOMES = {"thesis_confirmed", "thesis_refuted", "inconclusive"}
ID_RE = re.compile(
    r"^vc_(winner|negative_control|post_cutoff)_[a-z0-9_]+_[0-9]{3}(_fakedate)?$"
)
DATE_RE = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")
MONEY_RE = re.compile(r"(?:\$\s?\d|\bUSD\s+\d)", re.IGNORECASE)
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FORBIDDEN_MASK_KEYS = {
    "ticker", "tickers", "symbol", "symbols", "company", "company_name",
    "deal", "deal_name", "executive", "executive_name", "actual_date",
    "event_date", "resolved_at", "knowable_at",
}


def _object(value: Any, label: str, errors: list[str]) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        errors.append(f"{label} must be a JSON object")
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        errors.append(f"{label} is not valid JSON")
        return None
    if not isinstance(parsed, dict):
        errors.append(f"{label} must decode to an object")
        return None
    return parsed


def _iso(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _decision_signature(decision: dict[str, Any]) -> tuple[str, str]:
    return str(decision.get("decision")), str(decision.get("direction"))


def _confidence(decision: dict[str, Any], errors: list[str]) -> float | None:
    value = decision.get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append("model_decision_json.confidence must be numeric in [0,1]")
        return None
    value = float(value)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        errors.append("model_decision_json.confidence must be finite and in [0,1]")
        return None
    return value


def _validate_masked(masked: dict[str, Any] | None, errors: list[str]) -> None:
    if masked is None:
        errors.append("masked_case_json is required")
        return
    required = ("world_change", "sector_or_theme", "structural_features", "primary_source_class")
    for key in required:
        if key not in masked:
            errors.append(f"masked_case_json missing {key}")
    if len(str(masked.get("world_change", "")).strip()) < 20:
        errors.append("masked_case_json.world_change is too short")
    features = masked.get("structural_features")
    if not isinstance(features, list) or len(features) < 2:
        errors.append("masked_case_json.structural_features needs at least two items")
    forbidden = sorted(set(_walk_keys(masked)) & FORBIDDEN_MASK_KEYS)
    if forbidden:
        errors.append(f"masked_case_json contains identifier keys: {','.join(forbidden)}")
    serialized = json.dumps(masked, sort_keys=True)
    if DATE_RE.search(serialized):
        errors.append("masked_case_json contains an exact date")
    if MONEY_RE.search(serialized):
        errors.append("masked_case_json contains an exact money amount")


def _masked_hash(masked: dict[str, Any]) -> str:
    payload = json.dumps(masked, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _validate_decision(
    decision: dict[str, Any] | None,
    masked: dict[str, Any] | None,
    errors: list[str],
) -> float | None:
    if decision is None:
        return None
    action = decision.get("decision")
    direction = decision.get("direction")
    if action not in DECISIONS:
        errors.append("model_decision_json.decision is invalid")
    if direction not in DIRECTIONS:
        errors.append("model_decision_json.direction is invalid")
    if action == "open" and direction not in {"long", "short"}:
        errors.append("an open decision requires long or short direction")
    if action in {"no_trade", "block"} and direction != "none":
        errors.append("no_trade/block requires direction=none")
    rationale_hash = str(decision.get("rationale_hash", ""))
    if not HASH_RE.fullmatch(rationale_hash):
        errors.append("model_decision_json.rationale_hash must be sha256 plus 64 hex characters")
    if not str(decision.get("model_id") or "").strip():
        errors.append("model_decision_json.model_id is required")
    if not HASH_RE.fullmatch(str(decision.get("policy_hash", ""))):
        errors.append("model_decision_json.policy_hash must freeze the exact evaluation policy")
    if masked is not None and decision.get("masked_case_hash") != _masked_hash(masked):
        errors.append("model_decision_json.masked_case_hash does not match the frozen packet")
    if not _iso(decision.get("decided_at")):
        errors.append("model_decision_json.decided_at is not an ISO timestamp")
    if not _iso(decision.get("knowledge_cutoff")):
        errors.append("model_decision_json.knowledge_cutoff is not an ISO timestamp")
    return _confidence(decision, errors)


def _validate_outcome(outcome: dict[str, Any] | None, errors: list[str]) -> None:
    if outcome is None:
        return
    if outcome.get("outcome") not in OUTCOMES:
        errors.append("resolved_outcome_json.outcome is invalid")
    horizon = outcome.get("horizon_days")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        errors.append("resolved_outcome_json.horizon_days must be a positive integer")
    if len(str(outcome.get("external_mechanism_check", "")).strip()) < 10:
        errors.append("resolved_outcome_json.external_mechanism_check is too short")
    expected = outcome.get("expected_decision")
    direction = outcome.get("expected_direction")
    if expected not in DECISIONS:
        errors.append("resolved_outcome_json.expected_decision is invalid")
    if direction not in DIRECTIONS:
        errors.append("resolved_outcome_json.expected_direction is invalid")
    if expected == "open" and direction not in {"long", "short"}:
        errors.append("an expected open requires long or short direction")
    if expected in {"no_trade", "block"} and direction != "none":
        errors.append("expected no_trade/block requires direction=none")
    if not _iso(outcome.get("resolved_at")):
        errors.append("resolved_outcome_json.resolved_at is not an ISO timestamp")


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _validate_temporal_lineage(
    row: dict[str, Any], decision: dict[str, Any] | None,
    outcome: dict[str, Any] | None, errors: list[str],
) -> None:
    if decision is None or not _iso(decision.get("decided_at")) or not _iso(row.get("created_at")):
        return
    try:
        decided_at = _timestamp(decision["decided_at"])
        frozen_at = _timestamp(row["created_at"])
        if decided_at > frozen_at:
            errors.append("model decision was timestamped after the case was frozen")
        if outcome is None or not _iso(outcome.get("resolved_at")):
            return
        resolved_at = _timestamp(outcome["resolved_at"])
        if frozen_at >= resolved_at:
            errors.append("case was not frozen before its outcome resolved")
        if decided_at >= resolved_at:
            errors.append("model decision was not committed before outcome resolution")
        if row.get("case_class") == "post_cutoff" and _iso(decision.get("knowledge_cutoff")):
            if _timestamp(decision["knowledge_cutoff"]) >= resolved_at:
                errors.append("post_cutoff outcome does not post-date the model knowledge cutoff")
    except (KeyError, TypeError, ValueError):
        errors.append("decision/outcome lineage timestamps must be timezone-aware")


def _row_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def _ece(items: list[tuple[float, int]], bins: int = 10) -> float | None:
    if not items:
        return None
    total = len(items)
    result = 0.0
    for idx in range(bins):
        lo, hi = idx / bins, (idx + 1) / bins
        bucket = [item for item in items if lo <= item[0] <= hi if idx == bins - 1 or item[0] < hi]
        if not bucket:
            continue
        mean_conf = sum(x[0] for x in bucket) / len(bucket)
        accuracy = sum(x[1] for x in bucket) / len(bucket)
        result += (len(bucket) / total) * abs(accuracy - mean_conf)
    return result


def audit_corpus(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return structural diagnostics and an honest production-readiness verdict."""
    conn.row_factory = sqlite3.Row
    rows = [_row_dict(row) for row in conn.execute(
        "SELECT id,masked_case_json,case_class,fake_date_variant,model_decision_json,"
        "resolved_outcome_json,passed,created_at,experiment_id FROM validation_cases ORDER BY id"
    )]
    records: dict[str, dict[str, Any]] = {}
    structural_errors: list[str] = []
    pending = 0

    for row in rows:
        cid = str(row.get("id") or "")
        errors: list[str] = []
        if not ID_RE.fullmatch(cid):
            errors.append("id format is invalid")
        if row.get("case_class") not in CLASSES:
            errors.append("case_class is invalid")
        if not _iso(row.get("created_at")):
            errors.append("created_at is not an ISO timestamp")
        if not str(row.get("experiment_id") or "").strip():
            errors.append("experiment_id is required")

        masked = _object(row.get("masked_case_json"), "masked_case_json", errors)
        decision = _object(row.get("model_decision_json"), "model_decision_json", errors)
        outcome = _object(row.get("resolved_outcome_json"), "resolved_outcome_json", errors)
        _validate_masked(masked, errors)
        confidence = _validate_decision(decision, masked, errors)
        _validate_outcome(outcome, errors)
        _validate_temporal_lineage(row, decision, outcome, errors)
        if decision is None:
            errors.append("a validation row must freeze a model decision at creation")
        resolved = decision is not None and outcome is not None
        if not resolved:
            pending += 1
            if row.get("passed") != 0:
                errors.append("pending validation row must have passed=0")

        individual_pass: bool | None = None
        if resolved and not errors:
            individual_pass = _decision_signature(decision) == (
                str(outcome.get("expected_decision")), str(outcome.get("expected_direction"))
            )
        records[cid] = {
            "row": row,
            "errors": errors,
            "decision": decision,
            "outcome": outcome,
            "confidence": confidence,
            "resolved": resolved,
            "individual_pass": individual_pass,
            "effective_pass": individual_pass,
            "pair_invariant": None,
        }

    # A fake-date row is linked by the stable ``_fakedate`` suffix.  The fake
    # date itself stays in fake_date_variant; the masked evidence stays blind.
    for cid, record in records.items():
        row = record["row"]
        is_fake = bool(row.get("fake_date_variant")) or cid.endswith("_fakedate")
        if not is_fake:
            continue
        if not cid.endswith("_fakedate"):
            record["errors"].append("fake-date row id must end in _fakedate")
            continue
        base_id = cid.removesuffix("_fakedate")
        base = records.get(base_id)
        if base is None:
            record["errors"].append(f"fake-date base row is missing: {base_id}")
            continue
        if not row.get("fake_date_variant"):
            record["errors"].append("fake-date row requires fake_date_variant")
        if row.get("case_class") != base["row"].get("case_class"):
            record["errors"].append("fake-date pair has a different case_class")
        if record["decision"] is None or base["decision"] is None:
            continue
        invariant = _decision_signature(record["decision"]) == _decision_signature(base["decision"])
        record["pair_invariant"] = invariant
        base["pair_invariant"] = invariant
        if record["effective_pass"] is not None:
            record["effective_pass"] = bool(record["effective_pass"] and invariant)
        if base["effective_pass"] is not None:
            base["effective_pass"] = bool(base["effective_pass"] and invariant)

    # A substantive post-cutoff row must have a resolved fake-date companion
    # before it is eligible for the 30-case threshold.  Missing pairs are a
    # readiness deficit, not corruption while a corpus is being assembled.
    missing_pairs: list[str] = []
    unresolved_pairs: list[str] = []
    for cid, record in records.items():
        row = record["row"]
        if row.get("case_class") != "post_cutoff" or row.get("fake_date_variant"):
            continue
        fake = records.get(f"{cid}_fakedate")
        if fake is None:
            missing_pairs.append(cid)
        elif not fake["resolved"]:
            unresolved_pairs.append(cid)

    for cid, record in records.items():
        if record["errors"]:
            structural_errors.extend(f"{cid}: {error}" for error in record["errors"])
            continue
        if record["resolved"] and record["effective_pass"] is not None:
            stored = record["row"].get("passed")
            expected = int(bool(record["effective_pass"]))
            if stored != expected:
                structural_errors.append(
                    f"{cid}: stored passed={stored!r} disagrees with derived passed={expected}"
                )

    valid_base: list[dict[str, Any]] = []
    for cid, record in records.items():
        row = record["row"]
        if row.get("fake_date_variant") or cid.endswith("_fakedate"):
            continue
        if record["errors"] or not record["resolved"]:
            continue
        if any(error.startswith(f"{cid}:") for error in structural_errors):
            continue
        if row.get("case_class") == "post_cutoff" and (
            cid in missing_pairs or cid in unresolved_pairs
        ):
            continue
        valid_base.append(record)

    by_class = {
        name: [record for record in valid_base if record["row"].get("case_class") == name]
        for name in sorted(CLASSES)
    }
    post = by_class["post_cutoff"]
    negative = by_class["negative_control"]
    fake_pairs = [record for record in post if record["pair_invariant"] is not None]
    post_accuracy = (
        sum(bool(record["effective_pass"]) for record in post) / len(post) if post else None
    )
    negative_fpr = (
        sum(record["decision"].get("decision") == "open" for record in negative) / len(negative)
        if negative else None
    )
    fake_invariance = (
        sum(bool(record["pair_invariant"]) for record in fake_pairs) / len(fake_pairs)
        if fake_pairs else None
    )
    calibration = _ece([
        (float(record["confidence"]), int(bool(record["effective_pass"])))
        for record in post if record["confidence"] is not None
    ])
    counts = {name: len(by_class[name]) for name in sorted(CLASSES)}
    checks = {
        "post_cutoff_sample": counts["post_cutoff"] >= MIN_POST_CUTOFF,
        "negative_control_sample": counts["negative_control"] >= MIN_NEGATIVE_CONTROL,
        "post_cutoff_accuracy": post_accuracy is not None and post_accuracy >= MIN_POST_ACCURACY,
        "negative_control_false_positive": (
            negative_fpr is not None and negative_fpr <= MAX_NEGATIVE_FALSE_POSITIVE
        ),
        "fake_date_invariance": (
            fake_invariance is not None and fake_invariance >= MIN_FAKE_DATE_INVARIANCE
        ),
        "calibration_ece": calibration is not None and calibration <= MAX_ECE,
    }
    return {
        "ok": not structural_errors,
        "structural_ok": not structural_errors,
        "reasoning_gate": not structural_errors and all(checks.values()),
        "total_rows": len(rows),
        "pending_rows": pending,
        "invalid_rows": len({error.split(":", 1)[0] for error in structural_errors}),
        "eligible_resolved_counts": counts,
        "fake_date_missing_pairs": missing_pairs,
        "fake_date_unresolved_pairs": unresolved_pairs,
        "metrics": {
            "post_cutoff_accuracy": post_accuracy,
            "negative_control_false_positive_rate": negative_fpr,
            "fake_date_invariance": fake_invariance,
            "post_cutoff_ece": calibration,
        },
        "checks": checks,
        "errors": structural_errors,
    }


def _canonical_json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _load_batch(batch_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    payload = json.loads(batch_path.read_text())
    if not isinstance(payload, dict) or payload.get("approved_by") != "human":
        raise ValueError("batch must be an object with approved_by='human'")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("batch cases must be a non-empty list")
    experiment_id = str(payload.get("experiment_id") or "").strip()
    if not experiment_id:
        raise ValueError("batch experiment_id is required")
    if any(not isinstance(case, dict) for case in cases):
        raise ValueError("every case must be an object")
    return payload, cases, experiment_id


def _write_batch_audit(
    conn: sqlite3.Connection, batch_path: Path, operation: str,
    experiment_id: str, inserted: int, unchanged: int,
) -> None:
    batch_hash = hashlib.sha256(batch_path.read_bytes()).hexdigest()
    audit(
        conn,
        actor="developer",
        entity_type="validation_batch",
        entity_id=f"sha256:{batch_hash}",
        action=f"validation_batch_{operation}",
        rationale=(
            f"human-approved atomic validation {operation}: "
            f"inserted_or_resolved={inserted}, unchanged={unchanged}"
        ),
        after_state=json.dumps({"operation": operation, "changed": inserted, "unchanged": unchanged}),
        experiment_id=experiment_id,
    )


def _freeze(conn: sqlite3.Connection, batch_path: Path) -> dict[str, Any]:
    _payload, cases, experiment_id = _load_batch(batch_path)

    fields = (
        "id", "masked_case_json", "case_class", "fake_date_variant",
        "model_decision_json", "resolved_outcome_json", "passed", "created_at",
        "experiment_id",
    )
    inserted = 0
    unchanged = 0
    frozen_at = now_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for case in cases:
            row = dict(case)
            if row.get("resolved_outcome_json") is not None:
                raise ValueError("freeze batches cannot contain resolved outcomes")
            row["created_at"] = frozen_at
            row["experiment_id"] = experiment_id
            row["resolved_outcome_json"] = None
            row["passed"] = 0
            for json_field in ("masked_case_json", "model_decision_json"):
                row[json_field] = _canonical_json(row.get(json_field))
            values = tuple(row.get(field) for field in fields)
            existing = conn.execute(
                "SELECT " + ",".join(fields[1:]) + " FROM validation_cases WHERE id=?",
                (row.get("id"),),
            ).fetchone()
            if existing is not None:
                immutable_existing = tuple(existing)[:5] + tuple(existing)[7:]
                immutable_new = values[1:6] + values[8:]
                if immutable_existing != immutable_new or existing[4] is not None or existing[5] != 0:
                    raise ValueError(f"case id already exists with different content: {row.get('id')}")
                unchanged += 1
                continue
            conn.execute(
                "INSERT INTO validation_cases(" + ",".join(fields) + ") VALUES(" +
                ",".join("?" for _ in fields) + ")",
                values,
            )
            inserted += 1

        report = audit_corpus(conn)
        new_ids = {str(case.get("id")) for case in cases if isinstance(case, dict)}
        relevant_errors = [error for error in report["errors"] if error.split(":", 1)[0] in new_ids]
        relevant_missing = [cid for cid in report["fake_date_missing_pairs"] if cid in new_ids]
        if relevant_errors or relevant_missing:
            detail = relevant_errors + [f"{cid}: missing fake-date pair" for cid in relevant_missing]
            raise ValueError("batch rejected: " + "; ".join(detail[:20]))
        _write_batch_audit(conn, batch_path, "freeze", experiment_id, inserted, unchanged)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    report["freeze"] = {"inserted": inserted, "unchanged": unchanged}
    return report


def _effective_pass(conn: sqlite3.Connection, cid: str) -> int:
    row = conn.execute(
        "SELECT id,case_class,fake_date_variant,model_decision_json,resolved_outcome_json "
        "FROM validation_cases WHERE id=?", (cid,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown validation case: {cid}")
    decision = json.loads(row["model_decision_json"])
    outcome = json.loads(row["resolved_outcome_json"])
    passed = _decision_signature(decision) == (
        str(outcome.get("expected_decision")), str(outcome.get("expected_direction"))
    )
    if row["case_class"] == "post_cutoff":
        base_id = cid.removesuffix("_fakedate")
        pair_id = base_id if cid.endswith("_fakedate") else f"{cid}_fakedate"
        pair = conn.execute(
            "SELECT model_decision_json,resolved_outcome_json FROM validation_cases WHERE id=?",
            (pair_id,),
        ).fetchone()
        if pair is None or pair["resolved_outcome_json"] is None:
            raise ValueError(f"post-cutoff pair must resolve atomically: {base_id}")
        pair_decision = json.loads(pair["model_decision_json"])
        passed = passed and _decision_signature(decision) == _decision_signature(pair_decision)
    return int(passed)


def _resolve(conn: sqlite3.Connection, batch_path: Path) -> dict[str, Any]:
    _payload, cases, experiment_id = _load_batch(batch_path)
    changed = 0
    unchanged = 0
    target_ids: set[str] = set()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for case in cases:
            if set(case) - {"id", "resolved_outcome_json"}:
                raise ValueError("resolve cases may contain only id and resolved_outcome_json")
            cid = str(case.get("id") or "")
            outcome = _canonical_json(case.get("resolved_outcome_json"))
            if outcome is None:
                raise ValueError(f"resolved outcome is required: {cid}")
            existing = conn.execute(
                "SELECT resolved_outcome_json,experiment_id FROM validation_cases WHERE id=?", (cid,)
            ).fetchone()
            if existing is None:
                raise ValueError(f"case must be frozen before resolve: {cid}")
            if existing["experiment_id"] != experiment_id:
                raise ValueError(f"experiment_id cannot change at resolution: {cid}")
            if existing["resolved_outcome_json"] is not None:
                if _canonical_json(existing["resolved_outcome_json"]) != outcome:
                    raise ValueError(f"resolved outcome is immutable: {cid}")
                unchanged += 1
            else:
                conn.execute(
                    "UPDATE validation_cases SET resolved_outcome_json=? WHERE id=?", (outcome, cid)
                )
                changed += 1
            target_ids.add(cid)

        # Grade from frozen decisions only.  Post-cutoff base and fake-date
        # companions must both be present and resolved in this transaction.
        grade_ids = set(target_ids)
        for cid in list(target_ids):
            row = conn.execute(
                "SELECT case_class FROM validation_cases WHERE id=?", (cid,)
            ).fetchone()
            if row["case_class"] == "post_cutoff":
                base_id = cid.removesuffix("_fakedate")
                grade_ids.update({base_id, f"{base_id}_fakedate"})
        for cid in grade_ids:
            passed = _effective_pass(conn, cid)
            conn.execute("UPDATE validation_cases SET passed=? WHERE id=?", (passed, cid))

        report = audit_corpus(conn)
        relevant_errors = [
            error for error in report["errors"] if error.split(":", 1)[0] in grade_ids
        ]
        unresolved = [cid for cid in report["fake_date_unresolved_pairs"] if cid in grade_ids]
        if relevant_errors or unresolved:
            detail = relevant_errors + [f"{cid}: fake-date pair remains unresolved" for cid in unresolved]
            raise ValueError("batch rejected: " + "; ".join(detail[:20]))
        _write_batch_audit(conn, batch_path, "resolve", experiment_id, changed, unchanged)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    report["resolve"] = {"resolved": changed, "unchanged": unchanged}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--freeze", type=Path, help="freeze a human-approved decision batch")
    action.add_argument("--resolve", type=Path, help="resolve an already-frozen decision batch")
    parser.add_argument("--strict", action="store_true", help="fail unless the reasoning gate passes")
    args = parser.parse_args()
    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        if args.freeze:
            report = _freeze(conn, args.freeze)
        elif args.resolve:
            report = _resolve(conn, args.resolve)
        else:
            report = audit_corpus(conn)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        report = {"ok": False, "structural_ok": False, "reasoning_gate": False, "error": str(exc)}
    finally:
        conn.close()
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report.get("structural_ok"):
        return 1
    if args.strict and not report.get("reasoning_gate"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
