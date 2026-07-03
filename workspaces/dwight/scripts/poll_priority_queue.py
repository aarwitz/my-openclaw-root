#!/usr/bin/env python3
"""Dwight's priority-queue rail.

Reads `~/.openclaw/state/priority-queue.jsonl`, resolves the trading lane,
deduplicates against Task Manager, creates or updates the matching issue,
and appends a reconciliation row with `task_id` filled in.

Eligible rows are the latest `open` or `claimed` entries whose `task_id` is
still empty. This is the only Task Manager mutation path for Trading Intel.

Usage:
    poll_priority_queue.py [--sprint 5] [--dry-run]
"""
from __future__ import annotations
import argparse
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

QUEUE = Path(os.path.expanduser("~/.openclaw/state/priority-queue.jsonl"))
CANONICAL_TM_BASE = "https://tm.lidisolutions.ai"
RETIRED_TM_BASE_HOSTS = {"localhost", "rsl", "taskmanager"}


def canonicalize_tm_base(raw_base: str | None) -> str:
    base = (raw_base or CANONICAL_TM_BASE).strip().rstrip("/")
    if not base:
        return CANONICAL_TM_BASE
    parsed = urllib.parse.urlparse(base)
    host = (parsed.hostname or "").strip().lower()
    try:
        is_loopback = bool(host) and ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if host in RETIRED_TM_BASE_HOSTS or is_loopback:
        return CANONICAL_TM_BASE
    return base


def enforce_hosted_tm_base(raw_base: str | None, env_name: str = "TASK_MANAGER_URL") -> str:
    base = canonicalize_tm_base(raw_base)
    parsed = urllib.parse.urlparse(base)
    is_canonical = (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == "tm.lidisolutions.ai"
        and parsed.port in {None, 443}
        and (parsed.path or "") in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )
    if not is_canonical:
        raise RuntimeError(
            f"{env_name} must be {CANONICAL_TM_BASE}; got {raw_base!r}. "
            "Local or alternate Task Manager endpoints are not allowed."
        )
    return CANONICAL_TM_BASE


TM_BASE = enforce_hosted_tm_base(os.environ.get("TASK_MANAGER_URL"))
TM_HTTP_TIMEOUT = float(os.environ.get("TM_HTTP_TIMEOUT", "20"))
TM_BEARER_TOKEN = (os.environ.get("TASK_MANAGER_BEARER_TOKEN") or "").strip()
DEFAULT_SPRINT = 5
DEFAULT_OWNER = "Dwight"
QUEUE_MARKER_RE = re.compile(r"\bpq:([A-Za-z0-9-]+)\b")
CATEGORY_ASSIGNEE = {
    "research": "Researcher",
    "engineering": "Developer",
    "product": "Trader",
    "ops": "Overseer",
}
TITLE_RULES = [
    (re.compile(r"\b(spawn_agent|delegation|delegate|agent tool unavailable|tool unavailable)\b", re.I), "Developer"),
    (re.compile(r"\b(hypotheses?|falsifier|evidence|source|research)\b", re.I), "Researcher"),
    (re.compile(r"\b(score|regime|sizing|expression|quant)\b", re.I), "Quant"),
    (re.compile(r"\b(critic|challenge|review|falsify|falsified)\b", re.I), "Critic"),
    (re.compile(r"\b(intent|author|synthesis|operator-facing)\b", re.I), "Trader"),
    (re.compile(r"\b(broker|submit|reconcile|fill|fills|execution)\b", re.I), "Executor"),
    (re.compile(r"\b(postmortem|lessons?|patterns?|archive|archivist)\b", re.I), "Archivist"),
    (re.compile(r"\b(task manager|schema|api|watchdog|tool|delegate|control[- ]plane|spawn_agent)\b", re.I), "Developer"),
    (re.compile(r"\b(overseer|orchestrate|orchestration|control[- ]plane)\b", re.I), "Overseer"),
]


TM_USER_AGENT = os.environ.get("TM_USER_AGENT", "Mozilla/5.0 (compatible; LIDI-Agent/1.0)")


def tm_headers(*, with_json: bool = False) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": TM_USER_AGENT}
    if with_json:
        headers["Content-Type"] = "application/json"
    if TM_BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {TM_BEARER_TOKEN}"
    return headers


def load_rows() -> list[dict]:
    if not QUEUE.exists():
        return []
    rows: list[dict] = []
    for line in QUEUE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def coalesce_latest(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        rid = r.get("id")
        if rid:
            out[rid] = r
    return out


def canonicalize_assignee(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    lowered = normalized.lower()
    canonical = {
        "dwight": "Dwight",
        "researcher": "Researcher",
        "quant": "Quant",
        "critic": "Critic",
        "trader": "Trader",
        "executor": "Executor",
        "archivist": "Archivist",
        "developer": "Developer",
        "overseer": "Overseer",
    }.get(lowered)
    return canonical or normalized


def resolve_assignee(row: dict) -> str:
    explicit = canonicalize_assignee(row.get("assigned_to") or row.get("lane"))
    if explicit:
        return explicit

    text = f"{row.get('title') or ''} {row.get('details') or ''}"
    for pattern, assignee in TITLE_RULES:
        if pattern.search(text):
            return assignee

    category = str(row.get("category") or "").strip().lower()
    return CATEGORY_ASSIGNEE.get(category, "overseer")


def queue_marker(row_id: str) -> str:
    return f"pq:{row_id}"


def build_issue_payload(row: dict, assignee: str, sprint_id: int) -> dict:
    row_id = str(row.get("id") or "")
    title = str(row.get("title") or f"PQ {row_id}").strip()
    details = str(row.get("details") or "").strip()
    description = "\n".join([
        f"Promoted from priority queue {row_id}.",
        f"Queue marker: {queue_marker(row_id)}",
        f"Submitted by: {row.get('submitted_by') or 'unknown'}",
        f"Priority: {row.get('priority', '?')}",
        f"Category: {row.get('category') or 'unknown'}",
        f"Resolved assignee: {assignee}",
        "",
        details,
    ]).strip()
    acceptance_criteria = "\n".join([
        f"- Assigned to {assignee}.",
        "- Traceable back to the queue row by pq id.",
        "- Progress comment records the next concrete action.",
    ])
    return {
        "title": title,
        "description": description,
        "created_by": DEFAULT_OWNER,
        "assigned_to": assignee,
        "sprint_id": sprint_id,
        "acceptance_criteria": acceptance_criteria,
    }


def issue_mentions_queue_id(issue: dict, row_id: str) -> bool:
    marker = queue_marker(row_id)
    blobs = [str(issue.get("title") or ""), str(issue.get("description") or "")]
    for comment in issue.get("comments") or []:
        blobs.append(str(comment.get("content") or ""))
    return any(marker in blob or row_id in blob for blob in blobs)


def list_issues() -> list[dict]:
    status, parsed = tm_request("GET", "/api/issues")
    if status >= 400 or not isinstance(parsed, list):
        raise RuntimeError(f"GET /api/issues failed: status={status} body={str(parsed)[:200]}")
    return parsed


def find_existing_issue(issues: list[dict], row_id: str) -> dict | None:
    for issue in issues:
        if issue_mentions_queue_id(issue, row_id):
            return issue
    return None


DUP_LOOKBACK_HOURS = float(os.environ.get("PQ_DEDUP_LOOKBACK_HOURS", "48"))
DUP_TERMINAL_STATUSES = {"done", "resolved", "closed", "cancelled", "canceled"}


def normalize_title(value: object) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def _issue_timestamp(issue: dict) -> datetime | None:
    for key in ("created_at", "createdAt", "updated_at", "updatedAt"):
        raw = issue.get(key)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def find_duplicate_by_title(issues: list[dict], row: dict) -> dict | None:
    """Backstop dedup: an open, recent issue with the same normalized title.

    The marker-based ``find_existing_issue`` is the primary dedup path. This
    guards against duplicate promotions when the queue marker is absent (e.g.
    the same idea re-queued under a new row id). Skips terminal-status issues
    and anything older than ``PQ_DEDUP_LOOKBACK_HOURS`` so stale matches do not
    silently swallow genuinely new work. Returns the matching issue or None.
    """
    target = normalize_title(row.get("title"))
    if not target:
        return None
    now = datetime.now(timezone.utc)
    for issue in issues:
        if normalize_title(issue.get("title")) != target:
            continue
        if str(issue.get("status") or "").strip().lower() in DUP_TERMINAL_STATUSES:
            continue
        created = _issue_timestamp(issue)
        if created is not None and (now - created).total_seconds() > DUP_LOOKBACK_HOURS * 3600:
            continue
        return issue
    return None


def next_action_text(row: dict) -> str:
    title = str(row.get("title") or "").strip()
    if title:
        return title
    details = str(row.get("details") or "").strip()
    if details:
        return details.splitlines()[0][:140]
    return f"Resolve {row.get('id')}"


def append_reconciliation(row: dict, issue_id: int, assignee: str) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        **row,
        "status": "claimed",
        "claimed_by": "dwight",
        "assigned_to": assignee,
        "task_id": str(issue_id),
        "promoted_at": ts,
        "reconciled_at": ts,
    }


def ensure_comment(issue_id: int, row: dict, assignee: str, dry_run: bool) -> None:
    if dry_run:
        return
    comment = {
        "content": (
            f"Queue handoff from Dwight for {queue_marker(str(row.get('id') or ''))}.\n"
            f"Assigned lane: {assignee}.\n"
            f"Next action: {next_action_text(row)}."
        ),
        "username": DEFAULT_OWNER,
    }
    post_json(f"/api/issues/{issue_id}/comments", comment)


def _http_request(method: str, path: str, body: dict | None) -> tuple[int, object]:
    url = f"{TM_BASE}{path}"
    headers = tm_headers(with_json=body is not None)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TM_HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return exc.code, raw


def tm_request(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    """Send a hosted Task Manager request via HTTP only."""
    try:
        return _http_request(method, path, body)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {method} {TM_BASE}{path}: {exc.reason}") from exc


def post_json(path: str, body: dict) -> dict:
    status, parsed = tm_request("POST", path, body)
    if status >= 400:
        raise RuntimeError(f"POST {path} failed: status={status} body={str(parsed)[:200]}")
    if not isinstance(parsed, dict):
        raise RuntimeError(f"POST {path} returned non-object body: {str(parsed)[:200]}")
    return parsed


def patch_json(path: str, body: dict) -> dict:
    status, parsed = tm_request("PATCH", path, body)
    if status >= 400:
        raise RuntimeError(f"PATCH {path} failed: status={status} body={str(parsed)[:200]}")
    if not isinstance(parsed, dict):
        raise RuntimeError(f"PATCH {path} returned non-object body: {str(parsed)[:200]}")
    return parsed


def should_process(row: dict) -> bool:
    status = str(row.get("status") or "").strip().lower()
    task_id = str(row.get("task_id") or "").strip()
    claimed_by = str(row.get("claimed_by") or "").strip().lower()
    if task_id:
        return False
    if status not in {"open", "claimed"}:
        return False
    if status == "claimed" and claimed_by not in {"", "dwight"}:
        return False
    return True


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sprint", type=int, default=DEFAULT_SPRINT)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    rows = load_rows()
    latest = coalesce_latest(rows)
    pending = [r for r in latest.values() if should_process(r)]
    pending.sort(key=lambda r: (r.get("priority", 9), r.get("submitted_at", "")))

    if not pending:
        print(json.dumps({"polled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "claimed": 0, "reconciled": 0, "failed": 0}))
        return 0

    issues: list[dict] = []
    if not a.dry_run:
        try:
            issues = list_issues()
        except Exception as exc:
            print(f"ERROR: failed to list Task Manager issues: {exc}", file=sys.stderr)
            return 1

    created: list[dict] = []
    reconciled: list[dict] = []
    deduped: list[dict] = []
    failed: list[dict] = []
    for row in pending:
        row_id = str(row.get("id") or "")
        assignee = resolve_assignee(row)
        body = build_issue_payload(row, assignee, a.sprint)
        existing = find_existing_issue(issues, row_id) if issues else None
        dedup_link = find_duplicate_by_title(issues, row) if (issues and not existing) else None
        if a.dry_run:
            created.append({
                "id": row_id,
                "assignee": assignee,
                "existing_issue": bool(existing),
                "dedup_title_match": (dedup_link.get("id") if dedup_link else None),
                "would_post": body,
            })
            continue
        try:
            skip_handoff_comment = False
            if existing:
                issue_id = existing.get("id") or existing.get("issue_id")
                if issue_id is None:
                    raise RuntimeError(f"existing issue missing id: {existing}")
                patch_json(
                    f"/api/issues/{issue_id}",
                    {
                        "title": body["title"],
                        "description": body["description"],
                        "acceptance_criteria": body["acceptance_criteria"],
                        "assigned_to": body["assigned_to"],
                        "sprint_id": body["sprint_id"],
                        "updated_by": DEFAULT_OWNER,
                    },
                )
                reconciled.append({"id": row_id, "issue_id": issue_id, "assignee": assignee})
            elif dedup_link:
                issue_id = dedup_link.get("id") or dedup_link.get("issue_id")
                if issue_id is None:
                    raise RuntimeError(f"dedup-matched issue missing id: {dedup_link}")
                # Link the queue row to the pre-existing open issue and attach the
                # marker so future marker-based lookups match. Do NOT clobber the
                # existing issue body/assignee — this is only a duplicate guard.
                post_json(
                    f"/api/issues/{issue_id}/comments",
                    {
                        "content": (
                            f"Deduplicated: priority-queue row {row_id} "
                            f"({queue_marker(row_id)}) matches this open issue by title; "
                            f"not creating a duplicate. Resolved lane: {assignee}."
                        ),
                        "username": DEFAULT_OWNER,
                    },
                )
                skip_handoff_comment = True
                deduped.append({"id": row_id, "issue_id": issue_id, "assignee": assignee})
            else:
                issue = post_json(f"/api/issues", body)
                issue_id = issue.get("id") or issue.get("issue_id")
                if issue_id is None:
                    raise RuntimeError(f"issue POST returned no id: {issue}")
                created.append({"id": row_id, "issue_id": issue_id, "assignee": assignee})

            if not skip_handoff_comment:
                ensure_comment(int(issue_id), row, assignee, dry_run=False)
            update = append_reconciliation(row, int(issue_id), assignee)
            with QUEUE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(update, ensure_ascii=False) + "\n")
        
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            failed.append({"id": row_id, "error": str(e)})
        except Exception as e:
            failed.append({"id": row_id, "error": str(e)})

    out = {
        "polled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "claimed": len(created) + len(reconciled) + len(deduped),
        "reconciled": len(reconciled),
        "deduped": len(deduped),
        "failed": len(failed),
        "promoted": created,
        "reconciled_rows": reconciled,
        "deduped_rows": deduped,
        "errors": failed,
    }
    print(json.dumps(out, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
