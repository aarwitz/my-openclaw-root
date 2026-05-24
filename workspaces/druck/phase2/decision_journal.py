"""Decision journal (Priority 8).

Structured JSONL logging for trade decisions. Append-only, day-rotated.

Storage:
    phase2/logs/decision_journal/YYYY-MM-DD.jsonl
    phase2/logs/candidate_snapshots/YYYY-MM-DD-HHMM.json
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


LOGS_ROOT = Path(
    os.environ.get(
        "DRUCK_PHASE2_LOGS",
        str(Path.home() / ".openclaw/workspaces/druck/phase2/logs"),
    )
)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _journal_path() -> Path:
    p = LOGS_ROOT / "decision_journal"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{_today()}.jsonl"


def _snapshot_path(label: str = "scan") -> Path:
    p = LOGS_ROOT / "candidate_snapshots"
    p.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    return p / f"{stamp}-{label}.json"


def log_decision(
    *,
    ticker: str,
    side: str,                              # "buy" | "sell" | "hold" | "rotate"
    price_at_decision: Optional[float],
    reason_category: str,                   # "alpha_rotation" | "stop_hit" | "regime_change" | etc.
    catalyst: Optional[dict] = None,
    setup_state: Optional[str] = None,
    weekly_alpha_score: Optional[float] = None,
    benchmark_context: Optional[dict] = None,   # {regime, spy_5d_pct, vix_close}
    why_chosen: Optional[str] = None,
    rejected_alternatives: Optional[list[dict]] = None,
    extra: Optional[dict] = None,
) -> str:
    """Append a decision row to today's journal. Returns the line written."""
    rec = {
        "timestamp": _now_iso(),
        "ticker": ticker.upper(),
        "side": side,
        "price_at_decision": price_at_decision,
        "reason_category": reason_category,
        "catalyst": catalyst,
        "setup_state": setup_state,
        "weekly_alpha_score": weekly_alpha_score,
        "benchmark_context": benchmark_context,
        "why_chosen": why_chosen,
        "rejected_alternatives": rejected_alternatives or [],
    }
    if extra:
        rec["extra"] = extra
    line = json.dumps(rec, default=str)
    p = _journal_path()
    with p.open("a") as f:
        f.write(line + "\n")
    return line


def snapshot_candidates(payload: Any, label: str = "scan") -> Path:
    """Write a one-shot full-candidate-set snapshot. Returns path."""
    p = _snapshot_path(label)
    p.write_text(json.dumps(payload, indent=2, default=str))
    return p


def read_today() -> list[dict]:
    """Read today's decisions back as a list of dicts."""
    p = _journal_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
