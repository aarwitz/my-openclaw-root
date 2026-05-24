#!/usr/bin/env python3
"""Task Manager MCP server.

Provides a stable MCP tool surface over the local Task Manager API.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

TM_BASE_URL = os.environ.get("TM_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TM_TIMEOUT_SECONDS = float(os.environ.get("TM_TIMEOUT_SECONDS", "15"))
TM_DEFAULT_ACTOR = os.environ.get("TM_DEFAULT_ACTOR", "Dwight")

mcp = FastMCP("task-manager-mcp")


def _request_json(
    path: str,
    *,
    method: str = "GET",
    query: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    url = f"{TM_BASE_URL}{path}"
    if query:
        encoded = urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        if encoded:
            url = f"{url}?{encoded}"

    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, method=method, data=body, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=TM_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8")
            if not raw.strip():
                return {"ok": True}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Task Manager HTTP {exc.code} {method} {path}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Task Manager request failed {method} {path}: {exc.reason}") from exc


def _compact_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


def _as_issue_id(task_id: str | int) -> int:
    if isinstance(task_id, int):
        return task_id
    if isinstance(task_id, str) and task_id.startswith("issue-"):
        return int(task_id.split("-", 1)[1])
    return int(task_id)


@mcp.tool()
def health() -> dict[str, Any]:
    """Check Task Manager connectivity and return basic status."""
    active_sprint = _request_json("/api/sprints/active")
    return {
        "ok": True,
        "task_manager_base_url": TM_BASE_URL,
        "active_sprint": active_sprint,
    }


@mcp.tool()
def issue_get(issue_id: int) -> dict[str, Any]:
    """Get one issue by ID."""
    return _request_json(f"/api/issues/{issue_id}")


@mcp.tool()
def issue_list(
    status: str | None = None,
    assigned_to: str | None = None,
    sprint_id: int | None = None,
    in_backlog: bool | None = None,
) -> list[dict[str, Any]]:
    """List issues with optional filters."""
    return _request_json(
        "/api/issues",
        query={
            "status": status,
            "assigned_to": assigned_to,
            "sprint_id": sprint_id,
            "in_backlog": str(in_backlog).lower() if in_backlog is not None else None,
        },
    )


@mcp.tool()
def issue_search(
    q: str,
    search_in: str = "all",
    status: str | None = None,
    assigned_to: str | None = None,
) -> list[dict[str, Any]]:
    """Search issues by text/metadata."""
    return _request_json(
        "/api/issues/search",
        query={
            "q": q,
            "search_in": search_in,
            "status": status,
            "assigned_to": assigned_to,
        },
    )


@mcp.tool()
def issue_create(
    title: str,
    description: str,
    assigned_to: str,
    created_by: str = TM_DEFAULT_ACTOR,
    branch: str | None = None,
    sprint_id: int | None = None,
) -> dict[str, Any]:
    """Create an issue."""
    payload = _compact_payload(
        {
            "title": title,
            "description": description,
            "assigned_to": assigned_to,
            "created_by": created_by,
            "branch": branch,
            "sprint_id": sprint_id,
        }
    )
    return _request_json("/api/issues", method="POST", payload=payload)


@mcp.tool()
def issue_update(
    issue_id: int,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    assigned_to: str | None = None,
    branch: str | None = None,
) -> dict[str, Any]:
    """Patch mutable issue fields."""
    payload = _compact_payload(
        {
            "title": title,
            "description": description,
            "status": status,
            "assigned_to": assigned_to,
            "branch": branch,
        }
    )
    if not payload:
        raise RuntimeError("issue_update requires at least one field to update")
    return _request_json(f"/api/issues/{issue_id}", method="PATCH", payload=payload)


@mcp.tool()
def issue_assign_to_sprint(issue_id: int, sprint_id: int) -> dict[str, Any]:
    """Assign issue to sprint."""
    return _request_json(
        f"/api/issues/{issue_id}/assign-to-sprint",
        method="POST",
        query={"sprint_id": sprint_id},
    )


@mcp.tool()
def issue_add_comment(issue_id: int, content: str, username: str = TM_DEFAULT_ACTOR) -> dict[str, Any]:
    """Add a comment to an issue."""
    return _request_json(
        f"/api/issues/{issue_id}/comments",
        method="POST",
        payload={"content": content, "username": username},
    )


@mcp.tool()
def sprint_list() -> list[dict[str, Any]]:
    """List all sprints."""
    return _request_json("/api/sprints")


@mcp.tool()
def sprint_active() -> dict[str, Any]:
    """Get the active sprint."""
    return _request_json("/api/sprints/active")


@mcp.tool()
def task_assign(
    owner: str,
    goal: str,
    context: list[str] | None = None,
    origin_chat: str | None = None,
    created_by: str = TM_DEFAULT_ACTOR,
) -> dict[str, Any]:
    """Create a structured handoff task as a Task Manager issue.

    Returns both issue_id and task_id (issue-<id>) for orchestration flows.
    """
    context_lines = "\n".join(f"- {item}" for item in (context or []))
    description = (
        "Agent handoff request.\n\n"
        f"Goal: {goal}\n"
        f"Owner: {owner}\n"
        f"Origin chat: {origin_chat or 'n/a'}\n"
        "\nContext:\n"
        f"{context_lines if context_lines else '- none provided'}"
    )

    created = issue_create(
        title=f"[Agent Task] {goal}",
        description=description,
        assigned_to=owner,
        created_by=created_by,
    )
    issue_id = created.get("id")
    return {
        "task_id": f"issue-{issue_id}",
        "issue_id": issue_id,
        "status": created.get("status"),
        "assigned_to": created.get("assigned_to"),
        "url": f"{TM_BASE_URL}/issues/{issue_id}",
    }


@mcp.tool()
def task_complete(
    task_id: str,
    outcome: str,
    artifacts: list[str] | None = None,
    completed_by: str = TM_DEFAULT_ACTOR,
) -> dict[str, Any]:
    """Mark a task issue done and add completion evidence."""
    issue_id = _as_issue_id(task_id)
    updated = issue_update(issue_id=issue_id, status="done")

    evidence_lines = "\n".join(f"- {a}" for a in (artifacts or []))
    note = (
        f"Task complete by {completed_by}.\n"
        f"Outcome: {outcome}\n"
        f"Artifacts:\n{evidence_lines if evidence_lines else '- none'}"
    )
    issue_add_comment(issue_id=issue_id, content=note, username=completed_by)

    return {
        "task_id": task_id,
        "issue_id": issue_id,
        "status": updated.get("status"),
        "outcome": outcome,
    }


@mcp.tool()
def task_heartbeat(task_id: str, note: str | None = None, username: str = TM_DEFAULT_ACTOR) -> dict[str, Any]:
    """Post a lightweight heartbeat comment for long-running tasks."""
    issue_id = _as_issue_id(task_id)
    now = datetime.now(timezone.utc).isoformat()
    message = f"Heartbeat at {now}"
    if note:
        message = f"{message}\n{note}"

    comment = issue_add_comment(issue_id=issue_id, content=message, username=username)
    return {
        "task_id": task_id,
        "issue_id": issue_id,
        "comment_id": comment.get("id"),
        "timestamp": now,
    }


if __name__ == "__main__":
    mcp.run()
