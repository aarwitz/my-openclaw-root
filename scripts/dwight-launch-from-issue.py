#!/usr/bin/env python3

import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


DEFAULT_TM_BASE = "http://127.0.0.1:8000"
DEFAULT_LAUNCHER = os.path.expanduser("~/.openclaw/scripts/dwight-assign-coding-task.sh")


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"HTTP {exc.code} for GET {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for GET {url}: {exc.reason}") from exc


def get_first_nonempty(issue: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        value = issue.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_owner(owner: str) -> str:
    raw = owner.strip().lower()
    aliases = {
        "jerry": "main",
        "main": "main",
        "resi": "resi",
        "druck": "druck",
        "dwight": "dwight",
    }
    return aliases.get(raw, raw)


def infer_owner(issue: Dict[str, Any], override: str) -> str:
    if override:
        return normalize_owner(override)
    assigned = get_first_nonempty(issue, ["assigned_to", "assignee", "owner", "agent", "agent_id"])
    if not assigned:
        return "main"
    return normalize_owner(assigned)


def infer_goal(issue: Dict[str, Any], override: str) -> str:
    if override:
        return override.strip()
    goal = get_first_nonempty(
        issue,
        [
            "title",
            "summary",
            "name",
            "goal",
            "objective",
            "description",
        ],
    )
    if goal:
        return goal
    raise RuntimeError("Could not infer goal from issue; pass --goal explicitly.")


def infer_acceptance(issue: Dict[str, Any], override: str) -> str:
    if override:
        return override.strip()
    return get_first_nonempty(
        issue,
        [
            "acceptance_criteria",
            "acceptanceCriteria",
            "definition_of_done",
            "done_definition",
            "acceptance",
        ],
    )


def infer_repo(issue: Dict[str, Any], override: str) -> str:
    candidates: List[str] = []

    def add_repo_candidate(value: str) -> None:
        cleaned = value.strip()
        if not cleaned:
            return
        candidates.append(cleaned)
        if os.path.isabs(cleaned):
            return

        # Task Manager issues often store GitHub-style repo slugs such as
        # owner/repo, while local checkouts live directly under ~/repos/<repo>.
        repo_name = cleaned.rstrip("/").split("/")[-1]
        if repo_name and repo_name != cleaned:
            candidates.append(repo_name)

    if override:
        repo = override
    else:
        repo = get_first_nonempty(
            issue,
            [
                "repo_path",
                "repository_path",
                "repo",
                "repository",
                "workspace",
            ],
        )
        if repo:
            add_repo_candidate(repo)

        repo_slug = get_first_nonempty(issue, ["repo_slug", "repoSlug", "project_slug"])
        if repo_slug:
            add_repo_candidate(repo_slug)

        expanded_candidates: List[str] = []
        for candidate in candidates:
            if os.path.isabs(candidate):
                expanded_candidates.append(candidate)
                continue
            expanded_candidates.append(os.path.join("/home/aaron/repos", candidate))
            expanded_candidates.append(os.path.join(os.path.expanduser("~/.openclaw/workspace"), candidate))
        candidates = expanded_candidates

        env_default = os.environ.get("TM_DEFAULT_REPO", "")
        if env_default:
            candidates.append(env_default)

        repo = ""
        for candidate in candidates:
            if os.path.isabs(candidate) and os.path.isdir(candidate):
                repo = candidate
                break
        if not repo and candidates:
            repo = candidates[0]

    if not repo:
        raise RuntimeError(
            "Could not infer repo path from issue. Provide --repo, or set issue repo_path/repository_path/repo_slug, or set TM_DEFAULT_REPO."
        )
    if not os.path.isabs(repo):
        raise RuntimeError(f"Repo path must be absolute: {repo}")
    if not os.path.isdir(repo):
        raise RuntimeError(f"Repo path not found: {repo}")
    return repo


def infer_task_id(issue: Dict[str, Any], issue_id: str, override: str) -> str:
    if override:
        return override.strip()
    tm_id = issue.get("id")
    if tm_id is None:
        tm_id = issue_id
    return f"TM-{tm_id}"


def infer_heavy_tag(issue: Dict[str, Any], override: Optional[str]) -> str:
    if override is not None:
        return override
    tags = issue.get("tags")
    labels = issue.get("labels")
    text = ""
    if isinstance(tags, list):
        text += " ".join(str(x).lower() for x in tags)
    elif isinstance(tags, str):
        text += tags.lower()
    if isinstance(labels, list):
        text += " " + " ".join(str(x).lower() for x in labels)
    elif isinstance(labels, str):
        text += " " + labels.lower()
    return "true" if "heavy-coding" in text or "heavy" in text else "false"


def run(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Launch Dwight coding task directly from Task Manager issue fields.")
    parser.add_argument("--issue-id", required=True, help="Task Manager issue id")
    parser.add_argument("--tm-base", default=DEFAULT_TM_BASE, help="Task Manager base URL")
    parser.add_argument("--launcher", default=DEFAULT_LAUNCHER, help="Path to dwight assignment launcher")
    parser.add_argument("--task-id", default="", help="Override task id")
    parser.add_argument("--repo", default="", help="Override repo path")
    parser.add_argument("--owner-agent", default="", help="Override owner agent id")
    parser.add_argument("--goal", default="", help="Override goal text")
    parser.add_argument("--acceptance", default="", help="Override acceptance text")
    parser.add_argument("--scope", default="medium", choices=["low", "medium", "high"], help="Routing scope")
    parser.add_argument("--expected-files", default="1", help="Routing expected files")
    parser.add_argument("--risk", default="low", choices=["low", "medium", "high"], help="Routing risk")
    parser.add_argument(
        "--tag-heavy",
        default=None,
        choices=["true", "false"],
        help="Override heavy-coding tag detection",
    )
    parser.add_argument(
        "--acp-available",
        default="false",
        choices=["true", "false"],
        help="Whether external ACP is currently available",
    )
    parser.add_argument("--acp-agent", default="", help="Optional ACP harness override")
    parser.add_argument("--agent-timeout", default="120", help="openclaw agent timeout seconds")
    parser.add_argument("--execute", action="store_true", help="Actually execute (default dry-run)")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.launcher):
        raise RuntimeError(f"Launcher not found: {args.launcher}")

    issue_url = f"{args.tm_base.rstrip('/')}/api/issues/{args.issue_id}"
    issue = http_get_json(issue_url)
    if not isinstance(issue, dict):
        raise RuntimeError(f"Unexpected issue payload from {issue_url}")

    task_id = infer_task_id(issue, args.issue_id, args.task_id)
    owner_agent = infer_owner(issue, args.owner_agent)
    goal = infer_goal(issue, args.goal)
    acceptance = infer_acceptance(issue, args.acceptance)
    repo = infer_repo(issue, args.repo)
    heavy_tag = infer_heavy_tag(issue, args.tag_heavy)

    cmd = [
        args.launcher,
        "--owner-agent",
        owner_agent,
        "--task-id",
        task_id,
        "--repo",
        repo,
        "--goal",
        goal,
        "--scope",
        args.scope,
        "--expected-files",
        args.expected_files,
        "--risk",
        args.risk,
        "--tag-heavy",
        heavy_tag,
        "--acp-available",
        args.acp_available,
        "--agent-timeout",
        args.agent_timeout,
    ]

    if acceptance:
        cmd.extend(["--acceptance", acceptance])
    if args.acp_agent:
        cmd.extend(["--acp-agent", args.acp_agent])
    if args.execute:
        cmd.append("--execute")

    print("Resolved issue fields:")
    print(f"  issueId={args.issue_id}")
    print(f"  taskId={task_id}")
    print(f"  ownerAgent={owner_agent}")
    print(f"  repo={repo}")
    print(f"  heavyTag={heavy_tag}")
    print(f"  goal={goal}")
    if acceptance:
        print(f"  acceptance={acceptance}")
    else:
        print("  acceptance=<none>")

    print("Launcher command:")
    print("  " + shlex.join(cmd))

    completed = subprocess.run(cmd, check=False)
    return completed.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(run(sys.argv[1:]))
    except KeyboardInterrupt:
        print("Cancelled by user.", file=sys.stderr)
        raise SystemExit(130)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
