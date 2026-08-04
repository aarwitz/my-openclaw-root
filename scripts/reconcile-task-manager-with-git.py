#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()



import argparse
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path("/home/aaron/.openclaw")
PROJECTS = ROOT / "projects.json"
CANONICAL_TM_BASE = "https://tm.lidisolutions.ai"


def enforce_hosted_tm_base(raw_base: Optional[str], env_name: str = "TASK_MANAGER_URL") -> str:
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


DEFAULT_TM_BASE = enforce_hosted_tm_base(os.environ.get("TASK_MANAGER_URL"))


def run_cmd(args: List[str], cwd: Optional[str] = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or f"exit {completed.returncode}"
        raise RuntimeError(f"Command failed: {' '.join(args)}: {detail}")
    return completed


TM_USER_AGENT = os.environ.get("TM_USER_AGENT", "Mozilla/5.0 (compatible; LIDI-Agent/1.0)")


def http_json(url: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Any:
    body = None
    headers = {"Accept": "application/json", "User-Agent": TM_USER_AGENT}
    token_path = ROOT / "credentials/task-manager-agent.json"
    try:
        token = str(json.loads(token_path.read_text()).get("session_token") or "").strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        token = ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"HTTP {exc.code} for {method} {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {method} {url}: {exc.reason}") from exc


def fetch_open_issues(
    tm_base: str, assignee: Optional[str], sprint_id: int,
) -> List[Dict[str, Any]]:
    issues = http_json(f"{tm_base}/api/issues?sprint_id={sprint_id}")
    filtered: List[Dict[str, Any]] = []
    for issue in issues:
        if issue.get("status") == "done":
            continue
        branch = (issue.get("branch") or "").strip()
        if not branch:
            continue
        if assignee and issue.get("assigned_to") != assignee:
            continue
        filtered.append(issue)
    return filtered


def project_scope(project: str) -> tuple[int, list[dict]]:
    data = json.loads(PROJECTS.read_text())
    wanted = project.strip().lower()
    matches = [
        row for row in data.get("products", [])
        if str(row.get("id") or "").lower() == wanted
        or str(row.get("name") or "").lower() == wanted
    ]
    if len(matches) != 1:
        raise RuntimeError(f"projects.json must contain exactly one project matching {project!r}")
    row = matches[0]
    if row.get("status") != "active":
        raise RuntimeError(f"project {project!r} is not active")
    sprint_id = row.get("tm_sprint_id") or row.get("sprint_id")
    repos = [repo for repo in row.get("repos", []) if repo.get("path")]
    if not sprint_id or not repos:
        raise RuntimeError(f"project {project!r} lacks sprint/repository scope")
    return int(sprint_id), repos


def fetch_remote_state(repo: str) -> None:
    run_cmd([
        "git",
        "-C",
        repo,
        "fetch",
        "--quiet",
        "origin",
        "+refs/heads/*:refs/remotes/origin/*",
        "--prune",
    ])


def remote_branch_exists(repo: str, branch: str) -> bool:
    result = run_cmd(
        ["git", "-C", repo, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
        check=False,
    )
    return result.returncode == 0


def default_branch(repo: str) -> str:
    symbolic = run_cmd(
        ["git", "-C", repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        check=False,
    ).stdout.strip()
    if symbolic.startswith("origin/"):
        return symbolic.split("/", 1)[1]
    for candidate in ("master", "main"):
        if run_cmd(
            ["git", "-C", repo, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{candidate}"],
            check=False,
        ).returncode == 0:
            return candidate
    raise RuntimeError(f"cannot resolve origin default branch for {repo}")


def git_detect_merged(repo: str, branch: str, base: str) -> Tuple[bool, Optional[str]]:
    if not remote_branch_exists(repo, branch):
        return False, None

    result = run_cmd(
        [
            "git",
            "-C",
            repo,
            "merge-base",
            "--is-ancestor",
            f"refs/remotes/origin/{branch}",
            f"refs/remotes/origin/{base}",
        ],
        check=False,
    )
    if result.returncode == 0:
        return True, f"git: origin/{branch} is already contained in origin/{base}"
    if result.returncode == 1:
        return False, None
    stderr = result.stderr.strip()
    raise RuntimeError(stderr or f"git merge-base failed for {branch}")


def gh_is_available() -> bool:
    return shutil.which("gh") is not None


def gh_detect_merged(repo: str, branch: str, base: str) -> Tuple[bool, Optional[str]]:
    if not gh_is_available():
        return False, None

    result = run_cmd(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "merged",
            "--head",
            branch,
            "--base",
            base,
            "--json",
            "number,title,url,mergedAt,headRefName,baseRefName",
        ],
        cwd=repo,
        check=False,
    )
    if result.returncode != 0:
        return False, None

    try:
        prs = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return False, None

    if not prs:
        return False, None

    pr = prs[0]
    return True, f"github: PR #{pr['number']} merged into {pr['baseRefName']} ({pr['url']})"


def reconcile_issue(tm_base: str, issue: Dict[str, Any], apply: bool, add_comment: bool, reason: str) -> None:
    issue_id = issue["id"]
    if apply:
        http_json(f"{tm_base}/api/issues/{issue_id}", method="PATCH", payload={"status": "done"})
        if add_comment:
            http_json(
                f"{tm_base}/api/issues/{issue_id}/comments",
                method="POST",
                payload={
                    "content": (
                        f"Auto-reconciled with git: linked branch `{issue['branch']}` is already merged to `main`. "
                        f"Marked done to keep Task Manager and git aligned.\n\nReason: {reason}"
                    ),
                    "username": "Jerry",
                },
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile one active project's Task Manager sprint against its registered git repositories.")
    parser.add_argument("--project", default="AutoTrade", help="Active projects.json id or name")
    parser.add_argument("--tm-base", default=DEFAULT_TM_BASE, help="Task Manager base URL")
    parser.add_argument("--assignee", default=None, help="Only reconcile issues assigned to this person")
    parser.add_argument("--apply", action="store_true", help="Patch Task Manager issues to done when their linked branch is merged")
    parser.add_argument("--comment", action="store_true", help="Add a Task Manager comment when an issue is auto-marked done")
    args = parser.parse_args()

    try:
        sprint_id, registered_repos = project_scope(args.project)
        repos = []
        for spec in registered_repos:
            repo = str(spec["path"])
            if not Path(repo).is_dir():
                raise RuntimeError(f"registered repository is missing: {repo}")
            fetch_remote_state(repo)
            repos.append({**spec, "base": default_branch(repo)})
        issues = fetch_open_issues(args.tm_base, args.assignee, sprint_id)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not issues:
        print("No open issues with linked branches matched the filter.")
        return 0

    merged_count = 0
    unresolved_count = 0

    for issue in issues:
        branch = issue["branch"].strip()
        reason = None

        try:
            merged = False
            repo_slug = str(issue.get("repo_slug") or "").strip()
            candidates = [
                spec for spec in repos
                if not repo_slug or str(spec.get("slug") or "") == repo_slug
            ]
            if not candidates:
                print(
                    f"OPEN issue #{issue['id']} [{issue['status']}] branch={branch} :: "
                    f"repo_slug {repo_slug!r} is outside project registry"
                )
                unresolved_count += 1
                continue
            for spec in candidates:
                repo = str(spec["path"])
                base = str(spec["base"])
                merged, reason = git_detect_merged(repo, branch, base)
                if not merged:
                    merged, reason = gh_detect_merged(repo, branch, base)
                if merged:
                    break
        except RuntimeError as exc:
            print(f"ERROR issue #{issue['id']} {branch}: {exc}", file=sys.stderr)
            return 1

        if merged and reason:
            merged_count += 1
            action = "PATCHED" if args.apply else "WOULD_PATCH"
            print(f"{action} issue #{issue['id']} [{issue['status']}] branch={branch} :: {reason}")
            try:
                reconcile_issue(args.tm_base, issue, args.apply, args.comment, reason)
            except RuntimeError as exc:
                print(f"ERROR updating issue #{issue['id']}: {exc}", file=sys.stderr)
                return 1
        else:
            unresolved_count += 1
            print(f"OPEN issue #{issue['id']} [{issue['status']}] branch={branch} :: not merged to main")

    mode = "apply" if args.apply else "dry-run"
    print(f"Summary: mode={mode} scanned={len(issues)} merged={merged_count} unresolved={unresolved_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
