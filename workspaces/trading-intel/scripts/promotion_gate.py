#!/usr/bin/env python3
"""Fail-closed boundary between offline research and the paper-trading ledger.

Research output is untrusted until a source-controlled approval manifest binds an
exact completed forward artifact to an exact candidate set.  This module has no
database write path; ``integrate_calibrated.py`` is the only consumer allowed to
cross the boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path("/home/aaron/.openclaw")
APPROVAL_ROOT = ROOT / "workspaces/trading-intel/config/approved-strategies"
SCHEMA_VERSION = 1
ARTIFACT_TYPE = "autotrade_strategy_promotion"
EVALUATION_CLASS = "locked_forward_shadow"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("candidate contains a non-finite number")
    return number


def canonical_candidates(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the promotion-relevant candidate payload in stable order.

    Volatile fields such as creation timestamps are deliberately excluded.  Any
    change to an edge estimate, condition, sample size, or posterior changes the
    digest and invalidates approval.
    """
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        conds = row.get("conds_json", "[]")
        if isinstance(conds, str):
            conds = json.loads(conds)
        out.append({
            "id": str(row["id"]),
            "horizon": str(row["horizon"]),
            "direction": str(row["direction"]),
            "kind": str(row.get("kind") or ""),
            "source": str(row.get("source") or ""),
            "conditions": conds,
            "net_alpha_pct": _finite(row.get("net_alpha_pct")),
            "test_p": _finite(row.get("test_p")),
            "bonferroni": bool(row.get("bonf_sig")),
            "hit_rate": _finite(row.get("hit_te")),
            "raw_sample_n": int(row.get("te_n") or 0),
            "date_cluster_n": int(row.get("cluster_n") or 0),
            "posterior_mean": _finite(row.get("posterior_mean")),
        })
    return sorted(
        out,
        key=lambda row: (row["id"], row["horizon"], row["direction"]),
    )


def candidate_set_sha256(rows: Iterable[dict[str, Any]]) -> str:
    payload = json.dumps(
        canonical_candidates(rows), sort_keys=True, separators=(",", ":"),
    ).encode()
    return _sha256_bytes(payload)


def _parse_utc(value: Any, field: str) -> datetime:
    text = str(value or "")
    if not text.endswith("Z"):
        raise ValueError(f"{field} must be an explicit UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid timestamp") from exc
    return parsed.astimezone(timezone.utc)


def _assert_committed(path: Path, repo_root: Path) -> None:
    try:
        rel = path.resolve().relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ValueError("approval manifest must live inside the repository") from exc
    rel_text = rel.as_posix()
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel_text],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if tracked.returncode != 0:
        raise ValueError("approval manifest is not tracked in source control")
    head = subprocess.run(
        ["git", "show", f"HEAD:{rel_text}"],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if head.returncode != 0 or head.stdout != path.read_bytes():
        raise ValueError("approval manifest differs from the committed HEAD version")


def validate_approval_manifest(
    manifest_path: Path,
    candidates: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
    approval_root: Path = APPROVAL_ROOT,
    repo_root: Path = ROOT,
    require_committed: bool = True,
) -> dict[str, Any]:
    """Validate and return a manifest, or raise ``ValueError``.

    A development backtest can never satisfy this contract.  The referenced
    artifact must itself identify a completed, minimum-duration locked forward
    shadow evaluation with human-manifest-only promotion authority.
    """
    path = manifest_path.resolve()
    try:
        path.relative_to(approval_root.resolve())
    except ValueError as exc:
        raise ValueError("approval manifest is outside approved-strategies") from exc
    if require_committed:
        _assert_committed(path, repo_root)

    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported approval-manifest schema_version")
    if manifest.get("artifact_type") != ARTIFACT_TYPE:
        raise ValueError("wrong approval-manifest artifact_type")
    if manifest.get("status") != "approved":
        raise ValueError("strategy manifest is not approved")
    if manifest.get("approval_role") != "operator":
        raise ValueError("strategy approval must come from the operator role")
    if not str(manifest.get("approved_by") or "").strip():
        raise ValueError("approved_by is required")
    if not re.fullmatch(r"D[0-9]+", str(manifest.get("decision_id") or "")):
        raise ValueError("decision_id must reference a committed D-number decision")

    approved_at = _parse_utc(manifest.get("approved_at"), "approved_at")
    expires_at = _parse_utc(manifest.get("expires_at"), "expires_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires_at <= approved_at:
        raise ValueError("expires_at must follow approved_at")
    if current > expires_at:
        raise ValueError("strategy approval has expired")

    expected_candidates = candidate_set_sha256(candidates)
    if manifest.get("candidate_set_sha256") != expected_candidates:
        raise ValueError("candidate set differs from the approved digest")
    approved_ids = sorted(str(value) for value in manifest.get("candidate_ids", []))
    actual_ids = sorted(
        f"{row['id']}__{row['horizon']}" for row in canonical_candidates(candidates)
    )
    if approved_ids != actual_ids:
        raise ValueError("candidate_ids differ from the live integration set")

    source = manifest.get("source_artifact") or {}
    source_path_text = str(source.get("path") or "")
    if not source_path_text:
        raise ValueError("source_artifact.path is required")
    source_path = (repo_root / source_path_text).resolve()
    try:
        source_path.relative_to((repo_root / "state/research-artifacts").resolve())
    except ValueError as exc:
        raise ValueError("source artifact must live under state/research-artifacts") from exc
    if not source_path.is_file():
        raise ValueError("source artifact does not exist")
    if source.get("sha256") != file_sha256(source_path):
        raise ValueError("source artifact digest mismatch")

    report = json.loads(source_path.read_text())
    if report.get("status") != "complete":
        raise ValueError("source artifact is not complete")
    if report.get("evaluation_class") != EVALUATION_CLASS:
        raise ValueError("only a locked forward-shadow artifact can be promoted")
    if report.get("development_only") is not False:
        raise ValueError("development artifacts have no promotion authority")
    if report.get("minimum_sessions_met") is not True:
        raise ValueError("forward-shadow minimum session count was not met")
    if report.get("promotion_authority") != "human_manifest_only":
        raise ValueError("source artifact has invalid promotion authority")
    if report.get("candidate_set_sha256") != expected_candidates:
        raise ValueError("source artifact and approved candidate set differ")

    manifest["_manifest_sha256"] = file_sha256(path)
    manifest["_source_artifact_sha256"] = source["sha256"]
    return manifest
