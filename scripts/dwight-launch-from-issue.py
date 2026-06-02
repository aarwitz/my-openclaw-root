#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()



import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import urllib.error
import urllib.parse
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


def http_json_request(url: str, method: str, payload: Optional[Dict[str, Any]] = None) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"HTTP {exc.code} for {method} {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {method} {url}: {exc.reason}") from exc


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
            expanded_candidates.append(os.path.join(os.path.expanduser("~/.openclaw/workspaces"), candidate))
            if os.path.basename(candidate.rstrip("/")) == "openclaw":
                expanded_candidates.append(os.path.expanduser("~/.openclaw"))
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


def build_issue_launch_signature(issue: Dict[str, Any]) -> str:
    parts = [
        str(issue.get("status") or "").strip(),
        str(issue.get("assigned_to") or "").strip(),
        str(issue.get("branch") or "").strip(),
        str(issue.get("repo_slug") or "").strip(),
        str(issue.get("acceptance_criteria") or "").strip(),
        str(issue.get("title") or "").strip(),
        str(issue.get("description") or "").strip(),
        "1" if bool(issue.get("auto_launch_enabled")) else "0",
    ]
    return "|".join(parts)


def claim_issue_launch(tm_base: str, issue_id: str, claimant: str, source: str, expected_signature: str) -> str:
    payload = {
        "claimant": claimant,
        "source": source,
        "expected_signature": expected_signature,
    }
    response = http_json_request(f"{tm_base.rstrip('/')}/api/issues/{issue_id}/launch-claim", "POST", payload)
    if not isinstance(response, dict):
        raise RuntimeError("Unexpected launch claim response from Task Manager")
    claim_token = response.get("claim_token")
    if not isinstance(claim_token, str) or not claim_token.strip():
        raise RuntimeError("Task Manager launch claim response did not include a claim token")
    return claim_token.strip()


def release_issue_launch_claim(tm_base: str, issue_id: str, claim_token: str) -> None:
    http_json_request(
        f"{tm_base.rstrip('/')}/api/issues/{issue_id}/launch-claim?claim_token={urllib.parse.quote(claim_token, safe='')}",
        "DELETE",
    )


def extract_meta_path(output: str) -> str:
    match = re.search(r"Metadata written:\s+(\S+)", output)
    return match.group(1) if match else ""


def load_json_file(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def extract_contract_result(output: str, task_id: str) -> Optional[Dict[str, Any]]:
    allowed_pr_statuses = {"opened", "not-opened", "not-needed", "unknown"}

    def validate_obj(obj: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(obj, dict):
            return None
        if obj.get("lane") != "codex-subagent":
            return None
        if obj.get("taskId") != task_id:
            return None
        if obj.get("spawnAgentUsed") is not True:
            return None
        if obj.get("status") not in {"succeeded", "failed"}:
            return None
        branch = obj.get("branch")
        if not isinstance(branch, str) or not branch.strip():
            return None
        pr = obj.get("pr")
        if not isinstance(pr, dict):
            return None
        pr_status = pr.get("status")
        if pr_status not in allowed_pr_statuses:
            return None
        pr_url = pr.get("url")
        if pr_url is not None and not isinstance(pr_url, str):
            return None
        if pr_status == "opened" and not (isinstance(pr_url, str) and pr_url.strip()):
            return None
        if not isinstance(obj.get("evidence"), list):
            return None
        return obj

    def visit(value: Any) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict):
            parsed = validate_obj(value)
            if parsed:
                return parsed
            for nested in value.values():
                found = visit(nested)
                if found:
                    return found
            return None
        if isinstance(value, list):
            for nested in value:
                found = visit(nested)
                if found:
                    return found
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped.startswith("{") and not stripped.startswith("["):
                return None
            try:
                return visit(json.loads(stripped))
            except json.JSONDecodeError:
                return None
        return None

    for candidate in [output, *output.splitlines()]:
        stripped = candidate.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        found = visit(parsed)
        if found:
            return found

    for key in ("finalAssistantVisibleText", "finalAssistantRawText", "text"):
        pattern = re.compile(r'"' + re.escape(key) + r'"\s*:\s*"((?:\\.|[^"])*)"')
        for match in pattern.finditer(output):
            try:
                candidate = json.loads('"' + match.group(1) + '"')
            except json.JSONDecodeError:
                continue
            found = visit(candidate)
            if found:
                return found
    return None


def summarize_evidence_items(evidence: List[Any], limit: int = 5) -> List[str]:
    items: List[str] = []
    for raw in evidence:
        if not isinstance(raw, str):
            continue
        cleaned = raw.strip()
        if not cleaned:
            continue
        items.append(cleaned)
        if len(items) >= limit:
            break
    return items


def summarize_pr_evidence(contract: Dict[str, Any]) -> str:
    branch = str(contract.get("branch", "")).strip() or "<unknown>"
    pr = contract.get("pr") if isinstance(contract.get("pr"), dict) else {}
    pr_status = str(pr.get("status", "unknown")).strip() or "unknown"
    pr_url = pr.get("url")
    if isinstance(pr_url, str) and pr_url.strip():
        return f"branch={branch} pr_status={pr_status} pr_url={pr_url.strip()}"
    return f"branch={branch} pr_status={pr_status}"


def build_launch_comment(
    task_id: str,
    meta_path: str,
    execution: Optional[Dict[str, Any]],
    contract: Optional[Dict[str, Any]],
    completed: subprocess.CompletedProcess[str],
    output: str,
) -> tuple[str, str, Optional[str]]:
    if contract and contract.get("status") == "succeeded":
        evidence_lines = summarize_evidence_items(contract.get("evidence", []))
        pr_summary = summarize_pr_evidence(contract)
        detail_lines = "\n".join(f"- evidence: {line}" for line in evidence_lines)
        comment = (
            "- changed: autonomous launcher received a structured success result from the assigned agent.\n"
            f"- evidence: lane=codex-subagent task_id={task_id} meta={meta_path or '<none>'}\n"
            f"- evidence: {pr_summary}\n"
        )
        if detail_lines:
            comment += detail_lines + "\n"
        comment += "- next step: continue on the active branch and update issue status only when the underlying work is actually complete."
        return "launched", comment, None

    if contract and contract.get("status") == "failed":
        evidence_lines = summarize_evidence_items(contract.get("evidence", []))
        pr_summary = summarize_pr_evidence(contract)
        detail_lines = "\n".join(f"- evidence: {line}" for line in evidence_lines)
        comment = (
            "- changed: autonomous launcher received a structured failure result from the assigned agent.\n"
            f"- evidence: lane=codex-subagent task_id={task_id} meta={meta_path or '<none>'}\n"
            f"- evidence: {pr_summary}\n"
        )
        if detail_lines:
            comment += detail_lines + "\n"
        comment += "- next step: inspect the failure evidence, fix the blocking problem, then retrigger by editing the issue."
        return "failed", comment, "Agent returned structured failure result"

    snippet = " ".join(output.split())
    snippet = snippet[:800]
    if completed.returncode == 0:
        comment = (
            "- changed: autonomous launcher completed without a structured Codex result contract.\n"
            f"- evidence: task_id={task_id} meta={meta_path or '<none>'} return_code=0\n"
            f"- evidence: output_snippet={snippet}\n"
            "- next step: inspect launcher/runtime output because evidence postback was incomplete."
        )
        return "failed", comment, "Structured codex result missing from launcher output"

    comment = (
        "- changed: autonomous launcher run failed before a clean structured result was recorded.\n"
        f"- evidence: task_id={task_id} meta={meta_path or '<none>'} return_code={completed.returncode}\n"
        f"- evidence: output_snippet={snippet}\n"
        "- next step: inspect the launcher/runtime error, fix it, then retrigger by editing the issue."
    )
    return "failed", comment, snippet or f"Launcher exited with code {completed.returncode}"


def post_launch_result(
    tm_base: str,
    issue_id: str,
    launch_state: str,
    launch_error: Optional[str],
    comment_content: str,
    claim_token: Optional[str],
) -> None:
    payload = {
        "launch_state": launch_state,
        "launch_error": launch_error,
        "comment_content": comment_content,
        "username": "Dwight",
        "claim_token": claim_token,
    }
    http_json_request(f"{tm_base.rstrip('/')}/api/issues/{issue_id}/launch-result", "POST", payload)


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
        help="Compatibility flag only; ACP is disabled by policy",
    )
    parser.add_argument("--acp-agent", default="", help="Compatibility flag only; ignored (ACP disabled)")
    parser.add_argument("--agent-timeout", default="300", help="openclaw agent timeout seconds")
    parser.add_argument("--claim-token", default="", help="Pre-acquired Task Manager launch claim token")
    parser.add_argument("--claim-source", default="manual", help="Launch claim source label")
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
    claim_token = args.claim_token.strip()

    if args.acp_available == "true" or args.acp_agent:
        print("ACP is disabled by policy; ignoring --acp-available/--acp-agent overrides.", file=sys.stderr)

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
        "false",
        "--agent-timeout",
        args.agent_timeout,
    ]

    if acceptance:
        cmd.extend(["--acceptance", acceptance])
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

    if args.execute and not claim_token:
        claim_token = claim_issue_launch(
            args.tm_base,
            args.issue_id,
            "Dwight",
            args.claim_source,
            build_issue_launch_signature(issue),
        )

    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)

    if args.execute:
        meta_path = extract_meta_path(output)
        meta = load_json_file(meta_path)
        execution = meta.get("execution") if isinstance(meta, dict) and isinstance(meta.get("execution"), dict) else None
        contract = None
        if isinstance(execution, dict) and isinstance(execution.get("contract"), dict):
            contract = execution["contract"]
        if contract is None:
            contract = extract_contract_result(output, task_id)
        launch_state, comment_content, launch_error = build_launch_comment(
            task_id=task_id,
            meta_path=meta_path,
            execution=execution,
            contract=contract,
            completed=completed,
            output=output,
        )
        try:
            post_launch_result(args.tm_base, args.issue_id, launch_state, launch_error, comment_content, claim_token)
        except RuntimeError as exc:
            print(f"WARNING: could not post launch result back to Task Manager: {exc}", file=sys.stderr)
            if claim_token:
                try:
                    release_issue_launch_claim(args.tm_base, args.issue_id, claim_token)
                except RuntimeError as release_exc:
                    print(f"WARNING: could not release launch claim: {release_exc}", file=sys.stderr)

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
