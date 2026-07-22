#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()

"""Fail-closed Task Manager preflight for sprint-scoped mutations."""

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

CANONICAL_TM_BASE = "https://tm.lidisolutions.ai"
TM_USER_AGENT = os.environ.get("TM_USER_AGENT", "Mozilla/5.0 (compatible; LIDI-Agent/1.0)")


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


def tm_bearer_token() -> str:
    token = (os.environ.get("TASK_MANAGER_BEARER_TOKEN") or os.environ.get("TM_BEARER_TOKEN") or "").strip()
    if token:
        return token
    cred_path = os.path.expanduser("~/.openclaw/credentials/task-manager-agent.json")
    with open(cred_path, "r", encoding="utf-8") as fh:
        return str(json.load(fh).get("session_token") or "").strip()


def http_json(url: str) -> Any:
    token = tm_bearer_token()
    headers = {"Accept": "application/json", "User-Agent": TM_USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"HTTP {exc.code} for GET {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for GET {url}: {exc.reason}") from exc


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def fetch_sprints(tm_base: str) -> list[dict[str, Any]]:
    body = http_json(f"{tm_base}/api/sprints")
    if not isinstance(body, list):
        raise RuntimeError(f"GET /api/sprints returned unexpected payload: {type(body).__name__}")
    return body


def make_result(args: argparse.Namespace, matches: list[dict[str, Any]], decision: str, ok: bool, reason: str) -> dict[str, Any]:
    active_matches = [m for m in matches if bool(m.get("is_active"))]
    preferred = active_matches[0] if active_matches else (matches[0] if matches else None)
    return {
        "ok": ok,
        "intent": args.intent,
        "project": args.project,
        "sprint_name_queried": args.sprint_name or args.project,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "live_tm",
        "decision": decision,
        "reason": reason,
        "created_new_sprint": decision == "allow_create_sprint",
        "matching_sprints": [
            {
                "id": sprint.get("id"),
                "name": sprint.get("name"),
                "is_active": bool(sprint.get("is_active")),
                "started_at": sprint.get("started_at"),
            }
            for sprint in matches
        ],
        "sprint_id": preferred.get("id") if preferred else None,
        "sprint_name": preferred.get("name") if preferred else None,
    }


def decide(args: argparse.Namespace, sprints: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    target = normalize_name(args.sprint_name or args.project)
    matches = [s for s in sprints if normalize_name(str(s.get("name") or "")) == target]
    active_matches = [m for m in matches if bool(m.get("is_active"))]

    if args.intent == "create_sprint":
        if active_matches:
            return make_result(
                args,
                active_matches,
                "reject_duplicate_active_sprint",
                False,
                "Live TM already has an active sprint with this normalized name.",
            ), 2
        if matches:
            return make_result(
                args,
                matches,
                "review_existing_inactive_sprint",
                False,
                "A sprint with this normalized name already exists in TM but is not active.",
            ), 2
        return make_result(args, [], "allow_create_sprint", True, "No matching sprint exists in live TM."), 0

    if args.intent in {"create_issue", "create_issue_in_existing_sprint", "update_issue"}:
        if active_matches:
            return make_result(
                args,
                active_matches,
                "use_existing_active_sprint",
                True,
                "Live TM active sprint found; write may proceed against this sprint only.",
            ), 0
        if matches:
            return make_result(
                args,
                matches,
                "reject_inactive_sprint_for_write",
                False,
                "Matching sprint exists but is not active; do not infer routing from local registry/docs.",
            ), 3
        return make_result(
            args,
            [],
            "reject_missing_live_sprint",
            False,
            "No matching sprint found in live TM; do not create/write based on local state alone.",
        ), 3

    raise RuntimeError(f"Unsupported intent: {args.intent}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail-closed live Task Manager preflight for sprint-scoped mutations.")
    parser.add_argument("--project", required=True, help="Project or sprint name to check, e.g. MONTRA")
    parser.add_argument(
        "--intent",
        required=True,
        choices=["create_sprint", "create_issue", "create_issue_in_existing_sprint", "update_issue"],
        help="Intended TM mutation type.",
    )
    parser.add_argument("--sprint-name", help="Explicit sprint name if different from --project")
    parser.add_argument("--tm-base", default=os.environ.get("TASK_MANAGER_URL"), help="TM base URL; must stay canonical.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    tm_base = enforce_hosted_tm_base(args.tm_base)
    sprints = fetch_sprints(tm_base)
    result, exit_code = decide(args, sprints)
    print(json.dumps(result, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
