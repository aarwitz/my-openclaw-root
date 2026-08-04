#!/usr/bin/env python3
"""Durable intake for every operator-facing bot output.

Telegram is a view, not a work queue.  This module records outbound narration
and promotes explicit warnings/failures to the existing append-only priority
queue, where Dwight's authenticated rail deduplicates and reconciles Task
Manager sprint 5.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path("/home/aaron/.openclaw")
LEDGER = ROOT / "state/operator-events.jsonl"
QUEUE = ROOT / "state/priority-queue.jsonl"
LOCK = ROOT / "state/operator-events.lock"
ACTIONABLE = {"warn", "crit"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(value: str, limit: int = 80) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (clean[:limit].rstrip("-") or "operator-output")


def severity_for(content: str) -> str:
    """Conservative prose fallback; structured producers should override."""
    if re.search(r"🚨|\bHEALTH SWEEP CRIT\b|\bCron job\b.{0,120}\bfailed\b|\bFAILED\b", content):
        return "crit"
    if re.search(r"⚠️|\bHEALTH SWEEP WARN\b|\bWARN(?:ING)?\b", content):
        return "warn"
    return "info"


def family_for(content: str, source: str, explicit: str | None = None) -> str:
    if explicit:
        return _slug(explicit)
    bracket = re.search(r"\[([a-z][a-z0-9-]{2,80})\]", content, re.I)
    if bracket:
        return _slug(f"{source}-{bracket.group(1)}")
    cron = re.search(r"Cron job\s+[\"']?([a-z0-9-]{3,100})", content, re.I)
    if cron:
        return _slug(f"cron-{cron.group(1)}")
    headline = next((line.strip() for line in content.splitlines() if line.strip()), "operator output")
    words = re.findall(r"[A-Za-z0-9_-]+", headline)[:10]
    return _slug(f"{source}-{' '.join(words)}")


def assignee_for(content: str) -> str:
    lowered = content.lower()
    if re.search(r"broker|reconcile|fill|order divergence|position divergence", lowered):
        return "Executor"
    if re.search(r"market.event|news intake|research coverage|evidence source", lowered):
        return "Researcher"
    if re.search(r"risk gate|risk review|sizing headroom", lowered):
        return "Risk"
    if re.search(r"prediction|learning.loop|calibrat|brier|debrief|grade.outcome", lowered):
        return "Archivist"
    return "Developer"


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def ingest(
    content: str,
    *,
    source: str = "telegram",
    channel: str = "telegram",
    target: str = "",
    session_key: str = "",
    severity: str = "auto",
    family: str | None = None,
    now: datetime | None = None,
    ledger_path: Path = LEDGER,
    queue_path: Path = QUEUE,
    lock_path: Path = LOCK,
    cooldown_seconds: int = 86_400,
) -> dict:
    content = content.strip()
    if not content:
        raise ValueError("operator event content cannot be empty")
    now = now or _utc_now()
    resolved_severity = severity_for(content) if severity == "auto" else severity
    if resolved_severity not in {"info", "warn", "crit"}:
        raise ValueError("severity must be auto, info, warn, or crit")
    resolved_family = family_for(content, source, family)
    fingerprint = hashlib.sha256(content.encode()).hexdigest()
    event_id = "oe-" + hashlib.sha256(
        f"{resolved_family}|{fingerprint}|{_iso(now)}".encode()
    ).hexdigest()[:20]
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        prior = [row for row in _load(ledger_path) if row.get("family") == resolved_family]
        duplicate = False
        if prior:
            latest = prior[-1]
            try:
                prior_at = datetime.fromisoformat(str(latest["observed_at"]).replace("Z", "+00:00"))
            except (KeyError, ValueError):
                prior_at = datetime.min.replace(tzinfo=timezone.utc)
            duplicate = (
                latest.get("fingerprint") == fingerprint
                and (now - prior_at).total_seconds() < cooldown_seconds
            )
        pq_id = "pq-op-" + hashlib.sha256(resolved_family.encode()).hexdigest()[:12]
        event = {
            "id": event_id,
            "observed_at": _iso(now),
            "source": source,
            "channel": channel,
            "target": target,
            "session_key": session_key or None,
            "severity": resolved_severity,
            "family": resolved_family,
            "fingerprint": fingerprint,
            "content": content,
            "disposition": (
                "duplicate" if duplicate else
                "queued" if resolved_severity in ACTIONABLE else "observed"
            ),
            "priority_queue_id": pq_id if resolved_severity in ACTIONABLE else None,
        }
        _append(ledger_path, event)
        if resolved_severity in ACTIONABLE and not duplicate:
            headline = next(line.strip() for line in content.splitlines() if line.strip())
            queue_row = {
                "id": pq_id,
                "submitted_by": "overseer",
                "submitted_at": _iso(now),
                "category": "ops",
                "title": f"[Bot output] {headline[:170]}",
                "details": (
                    f"Durable operator-event {event_id}.\n"
                    f"Source: {source}; channel: {channel}; target: {target or 'unknown'}; "
                    f"session: {session_key or 'none'}; severity: {resolved_severity}.\n"
                    f"Family: {resolved_family}; fingerprint: {fingerprint}.\n\n{content}"
                ),
                "priority": 1 if resolved_severity == "crit" else 2,
                "status": "open",
                "claimed_by": None,
                "assigned_to": assignee_for(content),
                "task_id": None,
                "operator_event_id": event_id,
            }
            _append(queue_path, queue_row)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("ingest")
    add.add_argument("--content", required=True)
    add.add_argument("--source", default="telegram")
    add.add_argument("--channel", default="telegram")
    add.add_argument("--target", default="")
    add.add_argument("--session-key", default="")
    add.add_argument("--severity", default="auto", choices=("auto", "info", "warn", "crit"))
    add.add_argument("--family", default=None)
    args = parser.parse_args()
    try:
        result = ingest(
            args.content, source=args.source, channel=args.channel,
            target=args.target, session_key=args.session_key,
            severity=args.severity, family=args.family,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
