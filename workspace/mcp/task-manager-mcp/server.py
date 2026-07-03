#!/usr/bin/env python3
"""Task Manager MCP server.

Provides a stable MCP tool surface over the hosted Task Manager API.
"""

from __future__ import annotations

import json
import os
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

CANONICAL_TM_BASE_URL = "https://tm.lidisolutions.ai"


def enforce_hosted_tm_base(raw_base: str | None, env_name: str = "TM_BASE_URL") -> str:
    base = (raw_base or CANONICAL_TM_BASE_URL).strip().rstrip("/")
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
            f"{env_name} must be {CANONICAL_TM_BASE_URL}; got {raw_base!r}. "
            "Local or alternate Task Manager endpoints are not allowed."
        )
    return CANONICAL_TM_BASE_URL


TM_BASE_URL = enforce_hosted_tm_base(os.environ.get("TM_BASE_URL"))
TM_TIMEOUT_SECONDS = float(os.environ.get("TM_TIMEOUT_SECONDS", "15"))
TM_DEFAULT_ACTOR = os.environ.get("TM_DEFAULT_ACTOR", "Dwight")
TM_READ_ONLY = os.environ.get("TM_READ_ONLY", "false").lower() in {"1", "true", "yes", "on"}
TM_WRITE_ACTOR = os.environ.get("TM_WRITE_ACTOR", "Dwight")
TM_INTERNAL_USER_HEADER = os.environ.get("TM_INTERNAL_USER_HEADER", "X-TM-User")
TM_BEARER_TOKEN = (os.environ.get("TASK_MANAGER_BEARER_TOKEN") or os.environ.get("TM_BEARER_TOKEN") or "").strip()
TM_USER_AGENT = os.environ.get("TM_USER_AGENT", "Mozilla/5.0 (compatible; LIDI-Agent/1.0)")

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
    headers = {"Accept": "application/json", "User-Agent": TM_USER_AGENT}
    if TM_BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {TM_BEARER_TOKEN}"
    if TM_DEFAULT_ACTOR:
        headers[TM_INTERNAL_USER_HEADER] = TM_DEFAULT_ACTOR
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


def _encode_multipart_form_data(
    fields: dict[str, str],
    file_field_name: str,
    file_path: str,
) -> tuple[bytes, str]:
    boundary = f"----OpenClawTaskManager{uuid.uuid4().hex}"
    filename = os.path.basename(file_path)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with open(file_path, "rb") as fh:
        file_bytes = fh.read()

    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="{file_field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), boundary


def _request_multipart(
    path: str,
    *,
    file_field_name: str,
    file_path: str,
    fields: dict[str, str] | None = None,
    method: str = "POST",
    query: dict[str, Any] | None = None,
) -> Any:
    url = f"{TM_BASE_URL}{path}"
    if query:
        encoded = urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        if encoded:
            url = f"{url}?{encoded}"

    body, boundary = _encode_multipart_form_data(fields or {}, file_field_name, file_path)
    headers = {
        "Accept": "application/json",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    if TM_BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {TM_BEARER_TOKEN}"
    if TM_DEFAULT_ACTOR:
        headers[TM_INTERNAL_USER_HEADER] = TM_DEFAULT_ACTOR
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


def _require_write_access(tool_name: str) -> None:
    if not TM_READ_ONLY:
        return
    raise RuntimeError(
        f"Task Manager MCP is read-only for actor={TM_DEFAULT_ACTOR}; write denied for tool={tool_name}. "
        f"Use actor={TM_WRITE_ACTOR} profile for mutations."
    )


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
    _require_write_access("issue_create")
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
    _require_write_access("issue_update")
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
    _require_write_access("issue_assign_to_sprint")
    return _request_json(
        f"/api/issues/{issue_id}/assign-to-sprint",
        method="POST",
        query={"sprint_id": sprint_id},
    )


@mcp.tool()
def issue_add_comment(issue_id: int, content: str, username: str = TM_DEFAULT_ACTOR) -> dict[str, Any]:
    """Add a comment to an issue."""
    _require_write_access("issue_add_comment")
    return _request_json(
        f"/api/issues/{issue_id}/comments",
        method="POST",
        payload={"content": content, "username": username},
    )


@mcp.tool()
def issue_upload_image(
    issue_id: int,
    file_path: str,
    source_type: str = "issue",
    comment_id: int | None = None,
    uploaded_by: str = TM_DEFAULT_ACTOR,
) -> dict[str, Any]:
    """Upload an image for an issue, description, or comment."""
    _require_write_access("issue_upload_image")
    if not os.path.isfile(file_path):
        raise RuntimeError(f"issue_upload_image file not found: {file_path}")
    normalized_source = source_type.strip().lower()
    if normalized_source not in {"issue", "description", "comment"}:
        raise RuntimeError("issue_upload_image source_type must be one of: issue, description, comment")
    if normalized_source == "comment" and comment_id is None:
        raise RuntimeError("issue_upload_image requires comment_id when source_type=comment")
    return _request_multipart(
        f"/api/issues/{issue_id}/images",
        file_field_name="file",
        file_path=file_path,
        query={
            "source_type": normalized_source,
            "comment_id": comment_id,
            "uploaded_by": uploaded_by,
        },
    )


@mcp.tool()
def issue_delete_image(issue_id: int, image_id: int) -> dict[str, Any]:
    """Delete an image from an issue."""
    _require_write_access("issue_delete_image")
    return _request_json(f"/api/issues/{issue_id}/images/{image_id}", method="DELETE")


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
    _require_write_access("task_assign")
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
    _require_write_access("task_complete")
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
    _require_write_access("task_heartbeat")
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
