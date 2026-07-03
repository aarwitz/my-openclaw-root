#!/usr/bin/env python3
"""Jerry Host Maintainer — Task Manager -> host remediation.

Jerry is the host-resident maintainer. After the gateway-fleet containerization
cutover, the fleet runs inside the `openclaw-gateway` container WITHOUT access to
`docker.sock`, so containerized agents cannot perform Docker/host-root actions.
Jerry runs on the host with an isolated auth profile and owns those actions.

This poller scans Task Manager for host-ops work and hands each eligible issue to
Jerry on the host, then records the result back as a Task Manager comment.

Eligibility:
  - status in {to_do, in_progress}
  - assigned_to == "Jerry"  OR  a `host-ops` marker in the title/description
  - not already handled (idempotent via a `[JERRY-HOST]` marker comment; rc=0 is
    terminal, failed attempts are retried up to JERRY_HOST_MAX_ATTEMPTS).

Invocation of Jerry is the host gateway by default. To target Jerry's dedicated
host runtime after cutover, set JERRY_GATEWAY_URL (+ JERRY_GATEWAY_TOKEN); they
are appended as `--url`/`--token`.

Usage:
    jerry-host-poll.py [--dry-run] [--max-launches N] [--once]
"""
from __future__ import annotations

import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper

require_wrapper()

import argparse
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone

CANONICAL_TM_BASE = "https://tm.lidisolutions.ai"


def enforce_hosted_tm_base(raw_base: str | None, env_name: str = "TASK_MANAGER_URL") -> str:
    base = (raw_base or CANONICAL_TM_BASE).strip().rstrip("/")
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

OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN") or shutil.which("openclaw") or "/usr/local/bin/openclaw"
JERRY_AGENT_ID = os.environ.get("JERRY_AGENT_ID", "jerry")
JERRY_USERNAME = os.environ.get("JERRY_USERNAME", "Jerry")
AGENT_TIMEOUT_SEC = int(os.environ.get("JERRY_HOST_AGENT_TIMEOUT", "900"))
MAX_ATTEMPTS = int(os.environ.get("JERRY_HOST_MAX_ATTEMPTS", "3"))
DEFAULT_MAX_LAUNCHES = int(os.environ.get("JERRY_HOST_MAX_LAUNCHES", "1"))
MAX_COMMENT_REPLY_CHARS = int(os.environ.get("JERRY_HOST_MAX_COMMENT_REPLY_CHARS", "8000"))

# Optional: target Jerry's dedicated host runtime (post-cutover).
JERRY_GATEWAY_URL = os.environ.get("JERRY_GATEWAY_URL", "").strip()
JERRY_GATEWAY_TOKEN = os.environ.get("JERRY_GATEWAY_TOKEN", "").strip()
_AGENT_URL_FLAGS_SUPPORTED: bool | None = None

MARKER = "[JERRY-HOST]"
HOST_OPS_RE = re.compile(r"\bhost-ops\b", re.IGNORECASE)
RC_RE = re.compile(r"\brc=(\d+)\b")
FAILURE_HINT_RE = re.compile(r"status=(timeout|error)|state=(failed|error)", re.IGNORECASE)
ACTIVE_STATUSES = {"to_do", "in_progress"}


# ---------- HTTP layer ----------


TM_USER_AGENT = os.environ.get("TM_USER_AGENT", "Mozilla/5.0 (compatible; LIDI-Agent/1.0)")


def _tm_bearer_token() -> str:
    """Hosted TM requires auth. Prefer env; fall back to the shared credential
    file so a token rotation there propagates to every host client."""
    tok = (os.environ.get("TASK_MANAGER_BEARER_TOKEN") or os.environ.get("TM_BEARER_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        with open(os.path.expanduser("~/.openclaw/credentials/task-manager-agent.json")) as fh:
            return str(json.load(fh).get("session_token") or "").strip()
    except Exception:
        return ""


TM_BEARER_TOKEN = _tm_bearer_token()


def _http(method: str, path: str, payload: object | None = None) -> tuple[int, object]:
    url = f"{TM_BASE}{path}"
    headers = {"Accept": "application/json", "User-Agent": TM_USER_AGENT}
    if TM_BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {TM_BEARER_TOKEN}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    # One retry on network timeout/error for idempotent reads: the hosted TM is a
    # CF Worker whose cold start + large /api/issues payload can brush the timeout,
    # and a single blip shouldn't fail a whole 10-minute poll cycle.
    attempts = 2 if method.upper() == "GET" and payload is None else 1
    last_exc: Exception | None = None
    for _ in range(attempts):
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
        except Exception as exc:  # timeout / connection reset — retry once for GETs
            last_exc = exc
    raise last_exc if last_exc else RuntimeError("unreachable")


def tm_request(method: str, path: str, payload: object | None = None) -> tuple[int, object]:
    return _http(method, path, payload)


def list_issues() -> list[dict]:
    status, body = tm_request("GET", "/api/issues")
    if status >= 400 or not isinstance(body, list):
        raise RuntimeError(f"GET /api/issues failed: status={status} body={str(body)[:200]}")
    return body


def post_comment(issue_id: int, content: str) -> dict:
    status, body = tm_request(
        "POST", f"/api/issues/{issue_id}/comments",
        {"content": content, "username": JERRY_USERNAME},
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


def marker_state(issue: dict) -> tuple[bool, int]:
    """Return (has_success, failed_attempts) from Jerry host markers."""
    has_success = False
    failed_attempts = 0
    for comment in issue.get("comments") or []:
        if str(comment.get("username") or "").strip().lower() != JERRY_USERNAME.lower():
            continue
        content = str(comment.get("content") or "")
        if MARKER not in content:
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
        has_success = True
    return has_success, failed_attempts


def is_host_ops(issue: dict) -> bool:
    assignee = str(issue.get("assigned_to") or "").strip().lower()
    if assignee == JERRY_USERNAME.lower():
        return True
    blob = f"{issue.get('title') or ''}\n{issue.get('description') or ''}"
    return bool(HOST_OPS_RE.search(blob))


def issue_eligible(issue: dict) -> tuple[bool, str]:
    status = str(issue.get("status") or "").strip().lower()
    if status not in ACTIVE_STATUSES:
        return False, f"status_{status or 'missing'}"
    if not is_host_ops(issue):
        return False, "not_host_ops"
    has_success, failed_attempts = marker_state(issue)
    if has_success:
        return False, "already_handled"
    if failed_attempts >= MAX_ATTEMPTS:
        return False, f"retry_exhausted_{failed_attempts}"
    return True, "eligible"


# ---------- Dispatch ----------


def build_brief(issue: dict) -> str:
    title = str(issue.get("title") or "").strip()
    description = str(issue.get("description") or "").strip()
    acceptance = str(issue.get("acceptance_criteria") or "").strip()
    issue_id = issue.get("id")
    lines = [
        f"Task Manager host-ops issue #{issue_id}: {title}",
        "",
        "You are Jerry, the host-resident maintainer. This issue needs host-side or",
        "Docker remediation that containerized agents cannot perform. Follow the",
        "host-fix playbook at ~/.openclaw/reference/host-fix-playbook.md.",
        "",
        "Hard rules:",
        "- NEVER `systemctl restart` the gateway. Use ~/.openclaw/scripts/safe-restart.sh",
        "  only if a restart is truly required (it backs up + validates tokens).",
        "- Prefer hot reload for config changes.",
        "- Back up tokens before any auth/restart action (scripts/token-backup.sh).",
        "",
        "--- Issue description ---",
        description,
    ]
    if acceptance:
        lines += ["", "--- Acceptance criteria ---", acceptance]
    lines += [
        "",
        "--- Required reply shape ---",
        "1. Diagnosis (root cause, where observed).",
        "2. Exact remediation you performed on the host (commands run + results).",
        "3. Verification that the fix worked.",
        "4. Residual risk or follow-up, if any.",
        "Your reply is captured verbatim as a Task Manager comment.",
    ]
    return "\n".join(lines)


def invoke_jerry(brief: str) -> subprocess.CompletedProcess:
    global _AGENT_URL_FLAGS_SUPPORTED
    cmd = [
        OPENCLAW_BIN, "agent",
        "--agent", JERRY_AGENT_ID,
        "--message", brief,
        "--timeout", str(AGENT_TIMEOUT_SEC),
        "--json",
    ]
    if _AGENT_URL_FLAGS_SUPPORTED is None:
        try:
            help_proc = subprocess.run(
                [OPENCLAW_BIN, "agent", "--help"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            help_text = (help_proc.stdout or "") + "\n" + (help_proc.stderr or "")
            _AGENT_URL_FLAGS_SUPPORTED = ("--url" in help_text and "--token" in help_text)
        except Exception:  # noqa: BLE001 - capability probe best-effort
            _AGENT_URL_FLAGS_SUPPORTED = False
    if JERRY_GATEWAY_URL and _AGENT_URL_FLAGS_SUPPORTED:
        cmd += ["--url", JERRY_GATEWAY_URL]
    elif JERRY_GATEWAY_URL and not _AGENT_URL_FLAGS_SUPPORTED:
        print(
            "[jerry-host] warning: JERRY_GATEWAY_URL is set but this openclaw CLI "
            "does not support --url/--token for `agent`; using default gateway endpoint.",
            file=sys.stderr,
            flush=True,
        )
    if JERRY_GATEWAY_TOKEN and _AGENT_URL_FLAGS_SUPPORTED:
        cmd += ["--token", JERRY_GATEWAY_TOKEN]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=AGENT_TIMEOUT_SEC + 60)


def extract_reply(stdout: str) -> str:
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout.strip()
    if isinstance(parsed, dict):
        for key in ("finalAssistantVisibleText", "finalAssistantRawText", "text", "message", "reply"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(parsed, indent=2)[:6000]
    return stdout.strip()


def dispatch(issue: dict, dry_run: bool) -> dict:
    issue_id = int(issue["id"])
    lane = str(issue.get("assigned_to") or "host-ops")
    if dry_run:
        return {"issue_id": issue_id, "mode": "dry-run", "assignee": lane}
    if not OPENCLAW_BIN or not os.path.isfile(OPENCLAW_BIN):
        raise RuntimeError(f"openclaw CLI not found at {OPENCLAW_BIN}; set OPENCLAW_BIN explicitly.")
    brief = build_brief(issue)
    print(
        f"[jerry-host] start issue_id={issue_id} agent={JERRY_AGENT_ID} timeout_s={AGENT_TIMEOUT_SEC + 60}",
        file=sys.stderr, flush=True,
    )
    try:
        proc = invoke_jerry(brief)
    except subprocess.TimeoutExpired as exc:
        print(f"[jerry-host] timeout issue_id={issue_id}", file=sys.stderr, flush=True)
        post_comment(
            issue_id,
            f"{MARKER} handled=False status=timeout\n"
            f"openclaw agent timed out after {AGENT_TIMEOUT_SEC + 60}s.\n"
            f"stderr (tail): {(exc.stderr or '')[-400:]}",
        )
        return {"issue_id": issue_id, "status": "timeout"}
    print(
        f"[jerry-host] done issue_id={issue_id} rc={proc.returncode}",
        file=sys.stderr, flush=True,
    )
    reply = extract_reply(proc.stdout or "") or "(no assistant reply captured)"
    marker_line = f"{MARKER} handled={proc.returncode == 0} rc={proc.returncode}"
    post_comment(issue_id, f"{marker_line}\n\n{reply[:MAX_COMMENT_REPLY_CHARS]}")
    if proc.returncode == 0 and str(issue.get("status") or "").strip().lower() == "to_do":
        try:
            patch_issue(issue_id, {"status": "in_progress", "updated_by": JERRY_USERNAME})
        except Exception as exc:  # noqa: BLE001 - status patch is best-effort
            print(f"[jerry-host] status patch failed issue_id={issue_id}: {exc}", file=sys.stderr, flush=True)
    return {"issue_id": issue_id, "rc": proc.returncode, "status": "dispatched"}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="report eligible issues without invoking Jerry")
    p.add_argument("--max-launches", type=int, default=DEFAULT_MAX_LAUNCHES,
                   help="max issues to dispatch this run (real launches only)")
    p.add_argument("--once", action="store_true", help="(default) single pass; reserved for future loop mode")
    a = p.parse_args()

    try:
        issues = list_issues()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: failed to list Task Manager issues: {exc}", file=sys.stderr)
        return 1

    eligible: list[dict] = []
    skipped: list[dict] = []
    for issue in issues:
        ok, reason = issue_eligible(issue)
        (eligible if ok else skipped).append({"id": issue.get("id"), "reason": reason})

    dispatched: list[dict] = []
    errors: list[dict] = []
    launches = 0
    eligible_ids = {e["id"] for e in eligible}
    for issue in issues:
        if issue.get("id") not in eligible_ids:
            continue
        if not a.dry_run and launches >= a.max_launches:
            break
        try:
            dispatched.append(dispatch(issue, dry_run=a.dry_run))
            if not a.dry_run:
                launches += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({"id": issue.get("id"), "error": str(exc)})

    out = {
        "polled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "eligible": len(eligible),
        "dispatched": len([d for d in dispatched if d.get("status") in {"dispatched", "dry-run"}]),
        "errors": len(errors),
        "eligible_issues": eligible,
        "dispatched_detail": dispatched,
        "error_detail": errors,
    }
    print(json.dumps(out, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
