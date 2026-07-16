#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()



import argparse
import json
import os
from pathlib import Path
import ipaddress
import re
import shlex
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


CANONICAL_TM_BASE = "https://tm.lidisolutions.ai"
RETIRED_TM_BASE_HOSTS = {"localhost", "rsl", "taskmanager"}


def canonicalize_tm_base(raw_base: Optional[str]) -> str:
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


def enforce_hosted_tm_base(raw_base: Optional[str], env_name: str = "TASK_MANAGER_URL") -> str:
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


DEFAULT_TM_BASE = enforce_hosted_tm_base(os.environ.get("TASK_MANAGER_URL"))
DEFAULT_LAUNCHER = os.path.expanduser("~/.openclaw/scripts/dwight-assign-coding-task.sh")
TM_HTTP_TIMEOUT = float(os.environ.get("TM_HTTP_TIMEOUT", "20"))
def _tm_bearer_token() -> str:
    """Env first; fall back to the shared credential file so token rotation
    propagates to every host client (same pattern as jerry-host-poll)."""
    tok = (os.environ.get("TASK_MANAGER_BEARER_TOKEN") or os.environ.get("TM_BEARER_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        with open(os.path.expanduser("~/.openclaw/credentials/task-manager-agent.json")) as fh:
            return str(json.load(fh).get("session_token") or "").strip()
    except Exception:
        return ""


TM_BEARER_TOKEN = _tm_bearer_token()


class TMEndpointMissing(RuntimeError):
    """Raised when a TM endpoint returns 404; lets callers degrade gracefully."""


TM_USER_AGENT = os.environ.get("TM_USER_AGENT", "Mozilla/5.0 (compatible; LIDI-Agent/1.0)")


def tm_headers(*, with_json: bool = False) -> Dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": TM_USER_AGENT}
    if with_json:
        headers["Content-Type"] = "application/json"
    if TM_BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {TM_BEARER_TOKEN}"
    return headers


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, method="GET", headers=tm_headers())
    try:
        with urllib.request.urlopen(req, timeout=TM_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        if exc.code == 404:
            raise TMEndpointMissing(f"HTTP 404 for GET {url}") from exc
        raise RuntimeError(f"HTTP {exc.code} for GET {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for GET {url}: {exc.reason}") from exc


def http_json_request(url: str, method: str, payload: Optional[Dict[str, Any]] = None) -> Any:
    data = None
    headers = tm_headers(with_json=payload is not None)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TM_HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        if exc.code == 404:
            raise TMEndpointMissing(f"HTTP 404 for {method} {url}") from exc
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
        "jerry": "jerry",
        "main": "jerry",
        "resi": "resi",
        "druck": "druck",
        "dwight": "dwight",
        # Trading-stack lanes route directly to their OpenClaw agent ids.
        "researcher": "researcher",
        "quant": "quant",
        "critic": "critic",
        "trader": "trader",
        "executor": "executor",
        "archivist": "archivist",
        "developer": "developer",
        "overseer": "overseer",
    }
    return aliases.get(raw, raw)


def infer_owner(issue: Dict[str, Any], override: str) -> str:
    if override:
        return normalize_owner(override)
    assigned = get_first_nonempty(issue, ["assigned_to", "assignee", "owner", "agent", "agent_id"])
    if not assigned:
        return "jerry"
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
            # Project registry first: ~/.openclaw/projects.json (renamed from
            # products.json 2026-07-14) maps slug -> local path authoritatively
            # (handles paths outside ~/repos, e.g. the robot at ~/CleaningRobot
            # and the openclaw tree itself). If the registry is missing or a
            # slug has no entry, that must surface in the launch log — a silent
            # miss here stranded issue TM-239.
            registry_path = os.path.expanduser("~/.openclaw/projects.json")
            try:
                reg = json.load(open(registry_path))
                matched = False
                for prod in reg.get("projects", reg.get("products", [])):
                    for r in prod.get("repos", []):
                        if r.get("slug", "").lower() == repo_slug.strip().lower():
                            matched = True
                            # gateway_path first: it's inside the container mount
                            # (~/.openclaw/mirrors); host-only paths don't exist there.
                            for key in ("gateway_path", "path"):
                                if r.get(key):
                                    candidates.insert(0, r[key])
                if not matched:
                    print(f"WARNING: slug '{repo_slug}' not found in {registry_path}", file=sys.stderr)
            except Exception as exc:
                print(f"WARNING: could not read registry {registry_path}: {exc}", file=sys.stderr)
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


def claim_issue_launch(tm_base: str, issue_id: str, claimant: str, source: str, expected_signature: str) -> Optional[str]:
    """Acquire a launch claim if TM supports it. Returns the claim token, or None if the endpoint is absent."""
    payload = {
        "claimant": claimant,
        "source": source,
        "expected_signature": expected_signature,
    }
    try:
        response = http_json_request(f"{tm_base.rstrip('/')}/api/issues/{issue_id}/launch-claim", "POST", payload)
    except TMEndpointMissing:
        print(
            "INFO: TM launch-claim endpoint not available; proceeding without a claim token (idempotency provided by [LANE-BRIDGE] marker).",
            file=sys.stderr,
        )
        return None
    if not isinstance(response, dict):
        raise RuntimeError("Unexpected launch claim response from Task Manager")
    claim_token = response.get("claim_token")
    if not isinstance(claim_token, str) or not claim_token.strip():
        raise RuntimeError("Task Manager launch claim response did not include a claim token")
    return claim_token.strip()


def release_issue_launch_claim(tm_base: str, issue_id: str, claim_token: str) -> None:
    try:
        http_json_request(
            f"{tm_base.rstrip('/')}/api/issues/{issue_id}/launch-claim?claim_token={urllib.parse.quote(claim_token, safe='')}",
            "DELETE",
        )
    except TMEndpointMissing:
        return


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


def open_pr_and_handoff(repo: str, branch: str, issue: Dict[str, Any], task_id: str) -> tuple[Optional[str], List[str]]:
    """Push the task branch and open a PR (deterministic completion step).

    The coding agent's job ends at committed work on a branch; the launcher —
    not the model — owns push + PR so completion is uniform. Returns
    (pr_url, notes). pr_url is None when the handoff could not complete;
    notes explain what happened either way.
    """
    notes: List[str] = []

    def _git(*argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", repo, *argv], capture_output=True, text=True, check=False)

    has_branch = _git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if has_branch.returncode != 0:
        notes.append(f"PR handoff skipped: local branch {branch} not found in {repo}")
        return None, notes

    base = _git("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if base.returncode == 0 and base.stdout.strip():
        base_branch = base.stdout.strip().split("/")[-1]
    else:
        # origin/HEAD is often unset in older clones (it is in ~/.openclaw),
        # and assuming "main" breaks the ahead-check on master-based repos.
        # Probe for the base branch that actually exists.
        base_branch = "main"
        for candidate in ("main", "master"):
            if _git("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{candidate}").returncode == 0:
                base_branch = candidate
                break
    ahead = _git("rev-list", "--count", f"origin/{base_branch}..{branch}")
    if ahead.returncode == 0 and ahead.stdout.strip() == "0":
        notes.append(f"PR handoff skipped: branch {branch} has no commits beyond origin/{base_branch}")
        return None, notes

    push = _git("push", "-u", "origin", branch)
    if push.returncode != 0:
        notes.append(f"PR handoff failed at push: {(push.stderr or '').strip()[:300]}")
        return None, notes
    notes.append(f"Pushed {branch} to origin")

    title = f"{task_id}: {str(issue.get('title') or '').strip()[:120]}"
    body = (
        f"Automated coding-lane completion for Task Manager issue #{issue.get('id')}.\n\n"
        f"{str(issue.get('description') or '').strip()[:1000]}\n\n"
        f"Acceptance criteria:\n{str(issue.get('acceptance_criteria') or '(none recorded)').strip()[:800]}\n\n"
        f"Issue: {CANONICAL_TM_BASE}/issue?id={issue.get('id')}\n"
        "Merge is human-gated — review before merging.\n\n"
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    )
    # Route gh through the account router: it injects the rsl-bot token via
    # GH_TOKEN/GITHUB_TOKEN, which matters because the gateway container's
    # baked-in GITHUB_TOKEN env has gone stale before (TM-238: gh pr create
    # failed on auth with no visible trace).
    router = os.path.expanduser("~/.openclaw/scripts/gh-account-router.sh")
    gh_prefix = [router, "--agent", "dwight"] if os.path.exists(router) else ["gh"]
    pr = subprocess.run(
        [*gh_prefix, "pr", "create", "--head", branch, "--title", title, "--body", body],
        capture_output=True, text=True, check=False, cwd=repo,
    )
    if pr.returncode != 0:
        err = (pr.stderr or pr.stdout or "").strip()
        existing = re.search(r"https://github\.com/\S+/pull/\d+", err)
        if existing:
            notes.append(f"PR already exists: {existing.group(0)}")
            return existing.group(0), notes
        notes.append(f"PR handoff failed at gh pr create: {err[:300]}")
        return None, notes

    url_match = re.search(r"https://github\.com/\S+/pull/\d+", pr.stdout or "")
    if not url_match:
        notes.append(f"gh pr create succeeded but no PR URL found in output: {(pr.stdout or '')[:200]}")
        return None, notes
    notes.append(f"Opened PR {url_match.group(0)}")
    return url_match.group(0), notes


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
        pr = contract.get("pr") if isinstance(contract.get("pr"), dict) else {}
        pr_url = str(pr.get("url") or "").strip()
        branch = str(contract.get("branch") or "").strip()
        detail_lines = "\n".join(f"- evidence: {line}" for line in evidence_lines)
        if pr_url:
            headline = f"✅ {task_id} complete — PR awaiting your review: {pr_url}"
            next_step = "- next step: Aaron reviews and merges the PR (merge is human-gated)."
        else:
            headline = f"✅ {task_id} work complete on branch `{branch or '?'}` — but NO PR was opened (see evidence for why)."
            next_step = "- next step: resolve the PR blocker, then push the branch and open the PR."
        comment = (
            f"{headline}\n\n"
            f"- evidence: lane=codex-subagent task_id={task_id} meta={meta_path or '<none>'}\n"
        )
        if detail_lines:
            comment += detail_lines + "\n"
        comment += next_step
        return "launched", comment, None

    if contract and contract.get("status") == "failed":
        evidence_lines = summarize_evidence_items(contract.get("evidence", []))
        first_reason = next(
            (line for line in evidence_lines if any(w in line.lower() for w in ("because", "failed", "blocked", "missing", "not available"))),
            evidence_lines[0] if evidence_lines else "no reason recorded",
        )
        detail_lines = "\n".join(f"- evidence: {line}" for line in evidence_lines)
        comment = (
            f"❌ {task_id} run failed — {first_reason[:200]}\n\n"
            f"- evidence: lane=codex-subagent task_id={task_id} meta={meta_path or '<none>'}\n"
            f"- evidence: {summarize_pr_evidence(contract)}\n"
        )
        if detail_lines:
            comment += detail_lines + "\n"
        comment += "- next step: inspect the failure evidence, fix the blocking problem, then retrigger by editing the issue."
        return "failed", comment, "Agent returned structured failure result"

    snippet = " ".join(output.split())
    snippet = snippet[:800]
    if completed.returncode == 0:
        comment = (
            f"❌ {task_id} run produced no structured result — outcome unknown.\n\n"
            f"- evidence: task_id={task_id} meta={meta_path or '<none>'} return_code=0\n"
            f"- evidence: output_snippet={snippet}\n"
            "- next step: inspect launcher/runtime output because evidence postback was incomplete."
        )
        return "failed", comment, "Structured codex result missing from launcher output"

    comment = (
        f"❌ {task_id} launcher crashed (exit {completed.returncode}) before any result was recorded.\n\n"
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
    try:
        http_json_request(f"{tm_base.rstrip('/')}/api/issues/{issue_id}/launch-result", "POST", payload)
        return
    except TMEndpointMissing:
        # TM doesn't expose the structured launch-result endpoint; fall back to a plain comment.
        prefix = f"[LANE-EXEC] state={launch_state}"
        if launch_error:
            prefix += f" error={launch_error[:200]}"
        fallback = f"{prefix}\n\n{comment_content}"
        http_json_request(
            f"{tm_base.rstrip('/')}/api/issues/{issue_id}/comments",
            "POST",
            {"content": fallback, "username": "Dwight"},
        )


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
    parser.add_argument(
        "--detach",
        action="store_true",
        help="Re-exec in a detached session and return immediately. Required when the caller "
        "runs inside an agent tool-call window (~100s kill), which SIGINTs a synchronous "
        "coding-lane run mid-flight and strands the issue in launch_state=queued.",
    )
    args = parser.parse_args(argv)
    args.tm_base = canonicalize_tm_base(args.tm_base)

    if args.detach:
        if not args.execute:
            raise RuntimeError("--detach requires --execute (dry-runs are fast; run them inline)")
        log_dir = os.path.expanduser("~/.openclaw/logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"launch-from-issue-{args.issue_id}-detached.log")
        child_argv = [a for a in (argv if argv is not None else sys.argv[1:]) if a != "--detach"]
        cmd = [
            os.path.expanduser("~/.openclaw/scripts/run-with-trace.sh"),
            "--tag",
            "cron",
            os.path.abspath(__file__),
            *child_argv,
        ]
        with open(log_path, "ab") as log_fh:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=log_fh,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        print(f"Detached launch started: issueId={args.issue_id} pid={proc.pid} log={log_path}")
        print("The detached run claims the launch, executes the coding lane, and posts the TM comment itself.")
        return 0

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

    # Invoke the shell launcher via `bash` rather than executing the file path
    # directly. The exec bit is unreliable across git checkouts and container
    # volume mounts; `bash <script>` only needs the file to be readable. This
    # mirrors how dwight-assign-coding-task.sh itself calls launch-coding-task.sh
    # (`bash "$LAUNCHER"`). The OPENCLAW_RUN_WITH_TRACE=1 env propagates to this
    # child, so the require-wrapper guard still passes.
    cmd = [
        "bash",
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

    def git_current_branch(repo_path: str) -> Optional[str]:
        try:
            result = subprocess.run(
                ["git", "-C", repo_path, "branch", "--show-current"],
                capture_output=True, text=True, check=False,
            )
            return result.stdout.strip() or None
        except Exception:
            return None

    original_branch = git_current_branch(repo) if args.execute else None

    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)

    # The coding agent works on a task branch inside the LIVE checkout; never
    # leave the repo sitting on that branch afterwards (TM-213 left
    # lidi-solutions on its work branch — data-push crons then write against
    # the wrong branch and the operator finds a surprise checkout).
    if args.execute and original_branch:
        now_branch = git_current_branch(repo)
        if now_branch and now_branch != original_branch:
            restore = subprocess.run(
                ["git", "-C", repo, "checkout", original_branch],
                capture_output=True, text=True, check=False,
            )
            if restore.returncode == 0:
                print(f"Restored {repo} to branch {original_branch} (work preserved on {now_branch})")
            else:
                print(
                    f"WARNING: could not restore {repo} to {original_branch}: "
                    f"{(restore.stderr or '').strip()[:200]}",
                    file=sys.stderr,
                )

    if args.execute:
        meta_path = extract_meta_path(output)
        meta = load_json_file(meta_path)
        execution = meta.get("execution") if isinstance(meta, dict) and isinstance(meta.get("execution"), dict) else None
        contract = None
        if isinstance(execution, dict) and isinstance(execution.get("contract"), dict):
            contract = execution["contract"]
        if contract is None:
            contract = extract_contract_result(output, task_id)

        # Deterministic completion: on agent success, the LAUNCHER pushes the
        # branch, opens the PR, and flips the issue to in_review — the visible
        # handoff to the human. The agent's job ends at committed work.
        if contract and contract.get("status") == "succeeded":
            contract_branch = str(contract.get("branch") or "").strip()
            contract_pr = contract.get("pr") if isinstance(contract.get("pr"), dict) else {}
            if contract_branch and contract_branch not in ("main", "master") and not str(contract_pr.get("url") or "").strip():
                pr_url, handoff_notes = open_pr_and_handoff(repo, contract_branch, issue, task_id)
                for note in handoff_notes:
                    print(f"PR handoff: {note}")
                contract.setdefault("evidence", [])
                # Front-insert: the TM comment caps evidence at 5 items, so
                # tail-appended handoff notes were silently truncated — TM-238's
                # failed push/PR left no trace in the log OR the comment.
                contract["evidence"] = handoff_notes + list(contract["evidence"])
                if pr_url:
                    contract["pr"] = {"status": "opened", "url": pr_url}
            final_pr_url = str((contract.get("pr") or {}).get("url") or "").strip() if isinstance(contract.get("pr"), dict) else ""
            if final_pr_url:
                try:
                    http_json_request(
                        f"{args.tm_base.rstrip('/')}/api/issues/{args.issue_id}",
                        "PATCH",
                        {"status": "in_review", "pr_url": final_pr_url, "actor": "Dwight"},
                    )
                    print(f"Issue {args.issue_id} moved to in_review with PR {final_pr_url}")
                except RuntimeError as exc:
                    print(f"WARNING: could not move issue to in_review: {exc}", file=sys.stderr)

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
