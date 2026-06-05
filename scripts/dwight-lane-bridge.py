#!/usr/bin/env python3
"""Dwight Lane Bridge — TM issue -> lane execution.

Closes the second half of the Trading Intel control plane:

    queue row
        -> Dwight queue rail (creates TM issue, sets canonical assignee)
        -> Dwight lane bridge (this script)
        -> assigned lane actually executes
        -> evidence captured back as a TM comment by Dwight

Rules:
  - Dwight is still the only lane that mutates TM state.
  - Only issues created_by=Dwight with a `pq:` queue marker are eligible (so
    we never dispatch ad-hoc, human-authored issues).
  - Idempotency is provided by a `[LANE-BRIDGE]` marker comment from Dwight;
    if the marker is present, the issue is skipped on subsequent runs.
  - Dispatch by assignee:
        Developer  -> dwight-launch-from-issue.py --execute (real code work)
        any other trading lane -> `openclaw agent --agent <id> --message <brief>`
                                  and Dwight posts the response as a comment.
        Overseer / Dwight / Aaron / Jerry -> skip (control-plane lanes own
                                              their own follow-up).

Usage:
    dwight-lane-bridge.py [--dry-run] [--issue-id N] [--max-launches N]
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TM_BASE = os.environ.get("TASK_MANAGER_URL", "http://127.0.0.1:8000")
TM_CONTAINER = os.environ.get("TM_CONTAINER_NAME", "dwight-taskmanager")
TM_HTTP_TIMEOUT = float(os.environ.get("TM_HTTP_TIMEOUT", "20"))
DEFAULT_REPO = os.environ.get("TM_DEFAULT_REPO", "/home/aaron/.openclaw")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN") or shutil.which("openclaw") or "/usr/local/bin/openclaw"
LAUNCH_FROM_ISSUE = os.path.expanduser("~/.openclaw/scripts/dwight-launch-from-issue.py")
AGENT_TIMEOUT_SEC = int(os.environ.get("DWIGHT_LANE_AGENT_TIMEOUT", "600"))
MAX_LAUNCHES_DEFAULT = int(os.environ.get("DWIGHT_LANE_MAX_LAUNCHES", "3"))

# Marker comments used for idempotency. Both prefixes are recognized as
# bridge markers. A marker is "terminal" only when it records a clean dispatch
# (rc=0); markers recording a failed attempt (rc!=0 / status=timeout) are
# retryable up to MAX_DISPATCH_ATTEMPTS so a transient failure does not strand
# an issue forever.
BRIDGE_MARKERS = ("[LANE-BRIDGE]", "[LANE-EXEC]")
QUEUE_MARKER_RE = re.compile(r"\bpq:[A-Za-z0-9-]+\b")
RC_RE = re.compile(r"\brc=(\d+)\b")
FAILURE_HINT_RE = re.compile(r"status=(timeout|error)|state=(failed|error)", re.IGNORECASE)
# Max bridge dispatch attempts before an issue is parked for human review.
MAX_DISPATCH_ATTEMPTS = int(os.environ.get("DWIGHT_LANE_MAX_ATTEMPTS", "3"))

# Lanes that get real agent invocations through the bridge.
CODE_LANE = "Developer"
AGENT_LANES = {
    "Researcher": "researcher",
    "Quant": "quant",
    "Critic": "critic",
    "Trader": "trader",
    "Executor": "executor",
    "Archivist": "archivist",
}
# Control-plane lanes the bridge leaves alone (they self-drive).
CONTROL_LANES = {"Overseer", "Dwight", "Aaron", "Jerry"}


# ---------- HTTP layer with docker-exec fallback (mirrors tmctl.sh) ----------


def _docker_exec_request(method: str, path: str, payload: object | None) -> tuple[int, object]:
    if shutil.which("docker") is None:
        raise RuntimeError("docker CLI not available for fallback")
    snippet = (
        "import json, os, sys, urllib.error, urllib.request\n"
        "method = os.environ['TM_METHOD']\n"
        "path = os.environ['TM_PATH']\n"
        "body = os.environ.get('TM_BODY') or ''\n"
        "url = 'http://127.0.0.1:8000' + path\n"
        "headers = {'Accept': 'application/json'}\n"
        "data = None\n"
        "if body:\n"
        "    headers['Content-Type'] = 'application/json'\n"
        "    data = body.encode('utf-8')\n"
        "req = urllib.request.Request(url, method=method, data=data, headers=headers)\n"
        "try:\n"
        "    with urllib.request.urlopen(req, timeout=20) as resp:\n"
        "        out = {'status': resp.status, 'body': resp.read().decode('utf-8')}\n"
        "except urllib.error.HTTPError as exc:\n"
        "    out = {'status': exc.code, 'body': exc.read().decode('utf-8', errors='replace')}\n"
        "sys.stdout.write(json.dumps(out))\n"
    )
    env = os.environ.copy()
    env["TM_METHOD"] = method
    env["TM_PATH"] = path
    env["TM_BODY"] = json.dumps(payload) if payload is not None else ""
    proc = subprocess.run(
        ["docker", "exec", "-i",
         "-e", "TM_METHOD", "-e", "TM_PATH", "-e", "TM_BODY",
         TM_CONTAINER, "python", "-"],
        input=snippet,
        env=env,
        text=True,
        capture_output=True,
        timeout=TM_HTTP_TIMEOUT + 10,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker exec fallback failed (rc={proc.returncode}): stderr={proc.stderr.strip()[:300]}"
        )
    out = json.loads(proc.stdout)
    status = int(out.get("status", 0))
    raw = out.get("body") or ""
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = raw
    return status, parsed


def _http(method: str, path: str, payload: object | None = None) -> tuple[int, object]:
    url = f"{TM_BASE}{path}"
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TM_HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, (json.loads(raw) if raw else None)
        except json.JSONDecodeError:
            return exc.code, raw


def tm_request(method: str, path: str, payload: object | None = None) -> tuple[int, object]:
    try:
        return _http(method, path, payload)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if (isinstance(reason, OSError) and reason.errno in {111, 113, -2, -3}) or (
            "Connection refused" in str(reason) or "Name or service not known" in str(reason)
        ):
            return _docker_exec_request(method, path, payload)
        raise
    except (ConnectionError, TimeoutError):
        return _docker_exec_request(method, path, payload)


# ---------- TM helpers ----------


def list_issues() -> list[dict]:
    status, body = tm_request("GET", "/api/issues")
    if status >= 400 or not isinstance(body, list):
        raise RuntimeError(f"GET /api/issues failed: status={status} body={str(body)[:200]}")
    return body


def post_comment(issue_id: int, content: str) -> dict:
    status, body = tm_request(
        "POST", f"/api/issues/{issue_id}/comments",
        {"content": content, "username": "Dwight"},
    )
    if status >= 400 or not isinstance(body, dict):
        raise RuntimeError(f"POST comment failed: status={status} body={str(body)[:200]}")
    return body


def patch_issue(issue_id: int, payload: dict) -> dict:
    status, body = tm_request("PATCH", f"/api/issues/{issue_id}", payload)
    if status >= 400 or not isinstance(body, dict):
        raise RuntimeError(f"PATCH /api/issues/{issue_id} failed: status={status} body={str(body)[:200]}")
    return body


# ---------- Eligibility ----------


def bridge_marker_state(issue: dict) -> tuple[bool, int]:
    """Classify Dwight bridge markers on an issue.

    Returns (has_success, failed_attempts):
      - has_success: a marker recorded a clean dispatch (rc=0) -> terminal.
      - failed_attempts: count of markers recording a failed attempt
        (rc!=0, status=timeout/error, or state=failed/error).
    """
    has_success = False
    failed_attempts = 0
    for comment in issue.get("comments") or []:
        if str(comment.get("username") or "").strip().lower() != "dwight":
            continue
        content = str(comment.get("content") or "")
        if not any(marker in content for marker in BRIDGE_MARKERS):
            continue
        rc_match = RC_RE.search(content)
        if rc_match is not None:
            if rc_match.group(1) == "0":
                has_success = True
            else:
                failed_attempts += 1
            continue
        if FAILURE_HINT_RE.search(content):
            failed_attempts += 1
            continue
        # A bridge marker with no rc and no failure hint (e.g. a plain
        # [LANE-EXEC] state=launched) is treated as a terminal success.
        has_success = True
    return has_success, failed_attempts


def has_bridge_marker(issue: dict) -> bool:
    has_success, _ = bridge_marker_state(issue)
    return has_success


def issue_eligible(issue: dict) -> tuple[bool, str]:
    if str(issue.get("created_by") or "").strip().lower() != "dwight":
        return False, "not_dwight_created"
    status = str(issue.get("status") or "").strip().lower()
    if status not in {"to_do", "in_progress"}:
        return False, f"status_{status or 'missing'}"
    description = str(issue.get("description") or "")
    if not QUEUE_MARKER_RE.search(description):
        return False, "no_pq_marker"
    has_success, failed_attempts = bridge_marker_state(issue)
    if has_success:
        return False, "already_bridged"
    if failed_attempts >= MAX_DISPATCH_ATTEMPTS:
        return False, f"retry_exhausted_{failed_attempts}"
    assignee = str(issue.get("assigned_to") or "").strip()
    if not assignee:
        return False, "no_assignee"
    if assignee in CONTROL_LANES:
        return False, f"control_lane_{assignee}"
    if assignee != CODE_LANE and assignee not in AGENT_LANES:
        return False, f"unsupported_assignee_{assignee}"
    return True, "eligible"


# ---------- Dispatchers ----------


def build_agent_brief(issue: dict) -> str:
    title = str(issue.get("title") or "").strip()
    description = str(issue.get("description") or "").strip()
    acceptance = str(issue.get("acceptance_criteria") or "").strip()
    issue_id = issue.get("id")
    lines = [
        f"Task Manager issue #{issue_id}: {title}",
        "",
        "You are responding to a deterministic queue-rail handoff. Read the description carefully,",
        "do your lane's analysis or work, and reply with the concrete result. Be specific, cite",
        "evidence, and end with the next concrete action. Your reply will be captured verbatim",
        "as a Task Manager comment by Dwight on your behalf.",
        "",
        "--- Issue description ---",
        description,
    ]
    if acceptance:
        lines += ["", "--- Acceptance criteria ---", acceptance]
    lines += [
        "",
        "--- Required reply shape ---",
        "1. Findings / what you did (concrete, evidence-backed).",
        "2. Open questions or blockers, if any.",
        "3. Next concrete action and who should own it (an existing trading lane).",
    ]
    return "\n".join(lines)


def dispatch_agent_lane(issue: dict, lane_label: str, agent_id: str, dry_run: bool) -> dict:
    """Invoke `openclaw agent --agent <id>` and capture the textual reply."""
    issue_id = int(issue["id"])
    brief = build_agent_brief(issue)
    if dry_run:
        return {"issue_id": issue_id, "lane": lane_label, "agent_id": agent_id, "mode": "dry-run"}
    if not OPENCLAW_BIN or not os.path.isfile(OPENCLAW_BIN):
        raise RuntimeError(f"openclaw CLI not found at {OPENCLAW_BIN}; set OPENCLAW_BIN explicitly.")
    cmd = [
        OPENCLAW_BIN, "agent",
        "--agent", agent_id,
        "--message", brief,
        "--timeout", str(AGENT_TIMEOUT_SEC),
        "--json",
    ]
    print(
        f"[lane-bridge] start lane={lane_label} issue_id={issue_id} agent={agent_id} "
        f"timeout_s={AGENT_TIMEOUT_SEC + 60}",
        file=sys.stderr,
        flush=True,
    )
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=AGENT_TIMEOUT_SEC + 60)
    except subprocess.TimeoutExpired as exc:
        print(
            f"[lane-bridge] timeout lane={lane_label} issue_id={issue_id} agent={agent_id}",
            file=sys.stderr,
            flush=True,
        )
        comment = (
            f"[LANE-BRIDGE] dispatched=False lane={lane_label} agent={agent_id} status=timeout\n"
            f"openclaw agent timed out after {AGENT_TIMEOUT_SEC + 60}s.\n"
            f"stderr (tail): {(exc.stderr or '')[-400:]}"
        )
        post_comment(issue_id, comment)
        return {"issue_id": issue_id, "lane": lane_label, "status": "timeout"}
    print(
        f"[lane-bridge] done lane={lane_label} issue_id={issue_id} agent={agent_id} "
        f"rc={proc.returncode}",
        file=sys.stderr,
        flush=True,
    )
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    reply_text = ""
    try:
        parsed = json.loads(stdout)
        for key in ("finalAssistantVisibleText", "finalAssistantRawText", "text", "message", "reply"):
            value = parsed.get(key) if isinstance(parsed, dict) else None
            if isinstance(value, str) and value.strip():
                reply_text = value.strip()
                break
        if not reply_text and isinstance(parsed, dict):
            reply_text = json.dumps(parsed, indent=2)[:6000]
    except json.JSONDecodeError:
        reply_text = stdout.strip()
    if not reply_text:
        reply_text = "(no assistant reply captured; see stderr below)"

    response_comment = (
        f"[LANE-RESPONSE] lane={lane_label} agent={agent_id} rc={proc.returncode}\n\n"
        f"{reply_text}"
    )
    post_comment(issue_id, response_comment[:9000])

    bridge_comment = (
        f"[LANE-BRIDGE] dispatched={lane_label.lower()} agent={agent_id} "
        f"rc={proc.returncode} at={_now_iso()}\n"
        f"Captured agent reply as the preceding [LANE-RESPONSE] comment.\n"
        f"Stderr tail: {stderr[-200:] if stderr else '(empty)'}"
    )
    post_comment(issue_id, bridge_comment)
    # Move to in_progress only on a clean exit, so a failure stays visible.
    if proc.returncode == 0:
        try:
            patch_issue(issue_id, {"status": "in_progress", "updated_by": "Dwight"})
        except Exception as exc:
            print(f"WARNING: could not update status for #{issue_id}: {exc}", file=sys.stderr)
    return {
        "issue_id": issue_id, "lane": lane_label, "agent_id": agent_id,
        "rc": proc.returncode, "reply_chars": len(reply_text),
    }


def dispatch_developer_lane(issue: dict, dry_run: bool) -> dict:
    """Hand off to the canonical Dwight coding launcher."""
    issue_id = int(issue["id"])
    if dry_run:
        return {"issue_id": issue_id, "lane": CODE_LANE, "mode": "dry-run"}
    cmd = [
        "python3", LAUNCH_FROM_ISSUE,
        "--issue-id", str(issue_id),
        "--repo", DEFAULT_REPO,
        "--owner-agent", "developer",
        "--execute",
    ]
    print(
        f"[lane-bridge] start lane=Developer issue_id={issue_id} launcher=dwight-launch-from-issue.py "
        f"timeout_s={AGENT_TIMEOUT_SEC + 120}",
        file=sys.stderr,
        flush=True,
    )
    # Coding launches can be long; give them their own generous budget.
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=AGENT_TIMEOUT_SEC + 120)
    except subprocess.TimeoutExpired as exc:
        print(
            f"[lane-bridge] timeout lane=Developer issue_id={issue_id}",
            file=sys.stderr,
            flush=True,
        )
        comment = (
            f"[LANE-BRIDGE] dispatched=False lane=Developer status=timeout repo={DEFAULT_REPO}\n"
            f"Launcher timed out after {AGENT_TIMEOUT_SEC + 120}s.\n"
            f"stderr (tail): {(exc.stderr or '')[-400:]}"
        )
        post_comment(issue_id, comment)
        return {"issue_id": issue_id, "lane": CODE_LANE, "status": "timeout"}
    print(
        f"[lane-bridge] done lane=Developer issue_id={issue_id} rc={proc.returncode}",
        file=sys.stderr,
        flush=True,
    )
    bridge_comment = (
        f"[LANE-BRIDGE] dispatched=developer launcher=dwight-launch-from-issue.py "
        f"rc={proc.returncode} repo={DEFAULT_REPO} at={_now_iso()}\n"
        f"stdout tail: {(proc.stdout or '')[-400:]}\n"
        f"stderr tail: {(proc.stderr or '')[-400:]}"
    )
    post_comment(issue_id, bridge_comment[:9000])
    # Move to in_progress only on a clean launch, mirroring the agent lanes, so
    # a successful Developer dispatch is visible in TM status (not just comments).
    if proc.returncode == 0 and str(issue.get("status") or "").strip().lower() == "to_do":
        try:
            patch_issue(issue_id, {"status": "in_progress", "updated_by": "Dwight"})
        except Exception as exc:
            print(f"WARNING: could not update status for #{issue_id}: {exc}", file=sys.stderr)
    return {"issue_id": issue_id, "lane": CODE_LANE, "rc": proc.returncode}


# ---------- Main ----------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Print eligibility decisions without dispatching.")
    p.add_argument("--issue-id", type=int, default=None, help="Restrict to a single issue id.")
    p.add_argument("--max-launches", type=int, default=MAX_LAUNCHES_DEFAULT,
                   help="Maximum dispatches per run (default: env DWIGHT_LANE_MAX_LAUNCHES or 3).")
    args = p.parse_args()

    try:
        issues = list_issues()
    except Exception as exc:
        print(f"ERROR: could not list TM issues: {exc}", file=sys.stderr)
        return 1

    if args.issue_id is not None:
        issues = [i for i in issues if i.get("id") == args.issue_id]

    skipped: list[dict] = []
    dispatched: list[dict] = []
    failed: list[dict] = []

    for issue in issues:
        eligible, reason = issue_eligible(issue)
        if not eligible:
            skipped.append({"id": issue.get("id"), "reason": reason})
            continue
        if len(dispatched) + len(failed) >= args.max_launches:
            skipped.append({"id": issue.get("id"), "reason": "max_launches_reached"})
            continue
        assignee = str(issue.get("assigned_to") or "").strip()
        try:
            if assignee == CODE_LANE:
                result = dispatch_developer_lane(issue, args.dry_run)
            else:
                agent_id = AGENT_LANES[assignee]
                result = dispatch_agent_lane(issue, assignee, agent_id, args.dry_run)
            dispatched.append(result)
        except Exception as exc:
            failed.append({"id": issue.get("id"), "lane": assignee, "error": str(exc)[:300]})
            print(f"ERROR dispatching #{issue.get('id')}: {exc}", file=sys.stderr)

    summary = {
        "bridged_at": _now_iso(),
        "dry_run": args.dry_run,
        "dispatched": dispatched,
        "skipped_count": len(skipped),
        "failed": failed,
    }
    # Skip detail is verbose; print full list only when filtering or dry-run.
    if args.dry_run or args.issue_id is not None:
        summary["skipped"] = skipped
    print(json.dumps(summary, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
