#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

DEFAULT_TM_BASE = "https://tm.lidisolutions.ai"
DEFAULT_SPRINT_NAME = "EWAG I"
DEFAULT_ASSIGNEES = [
    "Dhuri, Purva Pravin",
    "Patel, Bhargav",
    "Wen, Yongqian",
    "Nguyen, Doan Duy Minh",
    "Aaron",
]


def api_request(tm_base: str, method: str, path: str, payload: Dict[str, Any] | None = None) -> Any:
    url = f"{tm_base.rstrip('/')}{path}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"HTTP {exc.code} {method} {path}: {detail}") from exc


def list_issues(tm_base: str) -> List[Dict[str, Any]]:
    data = api_request(tm_base, "GET", "/api/issues")
    if not isinstance(data, list):
        raise RuntimeError("Unexpected issues response shape")
    return data


def list_sprints(tm_base: str) -> List[Dict[str, Any]]:
    data = api_request(tm_base, "GET", "/api/sprints")
    if not isinstance(data, list):
        raise RuntimeError("Unexpected sprints response shape")
    return data


def delete_issue(tm_base: str, issue_id: int) -> None:
    api_request(tm_base, "DELETE", f"/api/issues/{issue_id}")


def delete_sprint(tm_base: str, sprint_id: int) -> None:
    path = f"/api/sprints/{sprint_id}?force=true"
    api_request(tm_base, "DELETE", path)


def create_sprint(tm_base: str, sprint_name: str) -> Dict[str, Any]:
    data = api_request(tm_base, "POST", "/api/sprints", {"name": sprint_name})
    if not isinstance(data, dict) or "id" not in data:
        raise RuntimeError("Unexpected create sprint response")
    return data


def start_sprint(tm_base: str, sprint_id: int) -> None:
    api_request(tm_base, "POST", f"/api/sprints/{sprint_id}/start")


def onboarding_description() -> str:
    return (
        "Onboarding goal:\n"
        "1. Visit https://www.elitewellnessamenitygroup.com and review EWAG's service model.\n"
        "2. Download and review the ResiLife app from the App Store.\n"
        "3. Propose one concrete product or workflow improvement.\n\n"
        "Workflow training:\n"
        "- Create a task manually or ask Lidi to draft one, then approve/edit it.\n"
        "- Set auto-launch when ready if you want an agent to pick it up.\n"
        "- Monitor agent activity, unblock when needed, and guide to a branch + PR.\n"
        "- Add reviewers once the implementation and evidence are ready."
    )


def create_onboarding_issue(tm_base: str, sprint_id: int, assignee: str) -> Dict[str, Any]:
    payload = {
        "title": f"EWAG I Onboarding - {assignee}",
        "description": onboarding_description(),
        "acceptance_criteria": (
            "Summarize one website insight, one ResiLife app insight, and propose one improvement "
            "with expected user/business impact."
        ),
        "created_by": "Aaron",
        "assigned_to": assignee,
        "sprint_id": sprint_id,
    }
    data = api_request(tm_base, "POST", "/api/issues", payload)
    if not isinstance(data, dict) or "id" not in data:
        raise RuntimeError("Unexpected create issue response")
    return data


def verify_state(tm_base: str, sprint_name: str, expected_assignees: List[str]) -> None:
    sprints = list_sprints(tm_base)
    issues = list_issues(tm_base)

    matching = [s for s in sprints if s.get("name") == sprint_name]
    if len(matching) != 1:
        raise RuntimeError(f"Expected exactly 1 sprint named '{sprint_name}', found {len(matching)}")

    sprint = matching[0]
    if not sprint.get("is_active"):
        raise RuntimeError(f"Sprint '{sprint_name}' exists but is not active")

    sprint_id = sprint.get("id")
    sprint_issues = [i for i in issues if i.get("sprint_id") == sprint_id]
    if len(sprint_issues) != len(expected_assignees):
        raise RuntimeError(
            f"Expected {len(expected_assignees)} issues in sprint '{sprint_name}', found {len(sprint_issues)}"
        )

    assignees = sorted(i.get("assigned_to") for i in sprint_issues)
    expected = sorted(expected_assignees)
    if assignees != expected:
        raise RuntimeError(f"Assignee mismatch. Expected {expected}, got {assignees}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset Lidi Task Manager and seed EWAG I onboarding sprint.")
    parser.add_argument("--tm-base", default=DEFAULT_TM_BASE)
    parser.add_argument("--sprint-name", default=DEFAULT_SPRINT_NAME)
    parser.add_argument("--execute", action="store_true", help="Perform destructive reset and seed changes.")
    args = parser.parse_args()

    tm_base = args.tm_base.strip().rstrip("/")
    if not tm_base:
        raise RuntimeError("--tm-base cannot be empty")

    issues = list_issues(tm_base)
    sprints = list_sprints(tm_base)

    print(f"Target TM: {tm_base}")
    print(f"Current state: {len(issues)} issue(s), {len(sprints)} sprint(s)")

    if not args.execute:
        print("Dry run only. Re-run with --execute to apply reset and seed EWAG I.")
        return 0

    for issue in issues:
        issue_id = issue.get("id")
        if isinstance(issue_id, int):
            delete_issue(tm_base, issue_id)

    for sprint in list_sprints(tm_base):
        sprint_id = sprint.get("id")
        if isinstance(sprint_id, int):
            delete_sprint(tm_base, sprint_id)

    sprint = create_sprint(tm_base, args.sprint_name)
    sprint_id = int(sprint["id"])
    start_sprint(tm_base, sprint_id)

    created_ids: List[int] = []
    for assignee in DEFAULT_ASSIGNEES:
        issue = create_onboarding_issue(tm_base, sprint_id, assignee)
        created_ids.append(int(issue["id"]))

    verify_state(tm_base, args.sprint_name, DEFAULT_ASSIGNEES)

    print(f"Reset complete. Active sprint: {args.sprint_name} (id={sprint_id})")
    print(f"Created onboarding issue ids: {created_ids}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
