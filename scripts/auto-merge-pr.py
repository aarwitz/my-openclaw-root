#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()

"""auto-merge-pr.py — deterministic merge gate for coding-lane PRs.

Operator decision 2026-07-17: human PR review is no longer the default gate.
This script IS the gate. Given a just-opened PR, it:

  1. Computes the diff (files + line counts) against the base branch.
  2. Classifies it against scripts/merge-policy.json:
       - any protected path touched  -> HOLD for human review
       - over size caps              -> HOLD for human review
  3. Runs deterministic verification gates:
       - python3 -m py_compile on every changed *.py
       - runs every changed test_*.py (must exit 0)
       - per-repo extra checks from policy repo_checks (must exit 0)
       - any gate failure            -> HOLD (never merge red)
  4. Routine + green -> merges the PR (gh via gh-account-router.sh),
     fast-forwards the local checkout if it sits on the base branch,
     flips the TM issue to done, and Telegrams Aaron a behavior digest
     (files, headline, revert command) via page-operator.sh.
  5. HOLD -> leaves the issue in in_review and pages Aaron with the
     specific reasons, so held PRs are loud, not silent.

Exit codes: 0 = merged, 3 = held (not an error), >0 other = gate/infra failure.
Emits one JSON result object on the last stdout line for callers.
"""

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request

POLICY_PATH = os.path.expanduser("~/.openclaw/scripts/merge-policy.json")
ROUTER = os.path.expanduser("~/.openclaw/scripts/gh-account-router.sh")
PAGER = os.path.expanduser("~/.openclaw/scripts/page-operator.sh")
TM_BASE = os.environ.get("TASK_MANAGER_URL", "https://tm.lidisolutions.ai").rstrip("/")
TM_CRED = os.path.expanduser("~/.openclaw/credentials/task-manager-agent.json")


def sh(argv, cwd=None, timeout=900, env=None):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          check=False, timeout=timeout, env=env)


def git(repo, *argv):
    return sh(["git", "-C", repo, *argv])


def gh(repo, *argv):
    prefix = [ROUTER, "--agent", "dwight"] if os.path.exists(ROUTER) else ["gh"]
    return sh([*prefix, *argv], cwd=repo)


def load_policy():
    with open(POLICY_PATH) as fh:
        return json.load(fh)


def base_branch_of(repo):
    for candidate in ("main", "master"):
        if git(repo, "rev-parse", "--verify", "--quiet",
               f"refs/remotes/origin/{candidate}").returncode == 0:
            return candidate
    return "main"


def diff_stats(repo, base_ref, head_ref):
    files_p = git(repo, "diff", "--name-only", f"{base_ref}...{head_ref}")
    files = [f for f in files_p.stdout.splitlines() if f.strip()]
    numstat = git(repo, "diff", "--numstat", f"{base_ref}...{head_ref}")
    total = 0
    for line in numstat.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            for n in parts[:2]:
                if n.isdigit():
                    total += int(n)
    return files, total


def classify(files, total_lines, policy):
    reasons = []
    protected = policy.get("protected_paths", [])
    for f in files:
        for pat in protected:
            if fnmatch.fnmatch(f, pat) or fnmatch.fnmatch(f, pat.rstrip("*").rstrip("/") + "/*"):
                reasons.append(f"protected path: {f} (matches {pat})")
                break
    max_files = int(policy.get("max_changed_files", 15))
    max_lines = int(policy.get("max_changed_lines", 1500))
    if len(files) > max_files:
        reasons.append(f"diff too wide: {len(files)} files > cap {max_files}")
    if total_lines > max_lines:
        reasons.append(f"diff too large: {total_lines} changed lines > cap {max_lines}")
    return reasons


def run_gates(repo, files, policy, checks=None):
    notes, ok = [], True
    py_files = [f for f in files if f.endswith(".py") and os.path.exists(os.path.join(repo, f))]
    if py_files:
        comp = sh(["python3", "-m", "py_compile", *[os.path.join(repo, f) for f in py_files]])
        if comp.returncode != 0:
            ok = False
            notes.append(f"gate FAIL py_compile: {(comp.stderr or '').strip()[:300]}")
        else:
            notes.append(f"gate pass: py_compile x{len(py_files)}")
    tests = [f for f in py_files if os.path.basename(f).startswith("test_")]
    for t in tests:
        run = sh(["python3", os.path.join(repo, t)], cwd=os.path.join(repo, os.path.dirname(t)))
        if run.returncode != 0:
            ok = False
            tail = ((run.stdout or "") + (run.stderr or "")).strip()[-300:]
            notes.append(f"gate FAIL {t}: {tail}")
        else:
            notes.append(f"gate pass: {t}")
    for cmd in (checks if checks is not None
                else policy.get("repo_checks", {}).get(os.path.realpath(repo), [])):
        run = sh(["bash", "-c", cmd], cwd=repo)
        if run.returncode != 0:
            ok = False
            tail = ((run.stdout or "") + (run.stderr or "")).strip()[-300:]
            notes.append(f"gate FAIL repo check `{cmd}`: {tail}")
        else:
            notes.append(f"gate pass: {cmd}")
    return ok, notes


def tm_patch(issue_id, payload):
    with open(TM_CRED) as fh:
        token = json.load(fh)["session_token"]
    req = urllib.request.Request(
        f"{TM_BASE}/api/issues/{issue_id}",
        data=json.dumps(payload).encode(),
        method="PATCH",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0 (compatible; LIDI-Agent/1.0)",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def page(key, msg):
    if not os.path.exists(PAGER):
        print(f"WARNING: pager missing, message dropped: {msg}", file=sys.stderr)
        return
    env = dict(os.environ, PAGE_FORCE="1")
    sh([PAGER, key, msg], env=env, timeout=60)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, help="local checkout path")
    ap.add_argument("--pr-url", required=True)
    ap.add_argument("--branch", required=True, help="PR head branch")
    ap.add_argument("--issue-id", type=int, default=None)
    ap.add_argument("--task-id", default="", help="e.g. TM-240 (for messages)")
    ap.add_argument("--evaluate-only", action="store_true",
                    help="classify + gates only; never merge, patch TM, or page")
    args = ap.parse_args()

    repo = os.path.realpath(os.path.expanduser(args.repo))
    m = re.search(r"/pull/(\d+)", args.pr_url)
    if not m:
        print(json.dumps({"decision": "error", "reasons": [f"bad PR url: {args.pr_url}"]}))
        return 2
    pr_num = m.group(1)
    task = args.task_id or f"PR#{pr_num}"
    policy = load_policy()

    git(repo, "fetch", "origin")
    base = base_branch_of(repo)
    files, total_lines = diff_stats(repo, f"origin/{base}", f"origin/{args.branch}")
    if not files:
        # branch may only exist locally (push failed upstream) — try local ref
        files, total_lines = diff_stats(repo, f"origin/{base}", args.branch)
    hold_reasons = classify(files, total_lines, policy)

    # Run gates against the PR's code, not the live checkout — the launcher
    # restores the base branch after each run, so on-disk files are NOT the
    # branch's. A detached temp worktree gives us the branch's tree without
    # touching the live checkout (which carries operator edits).
    head_ref = args.branch
    if git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{args.branch}").returncode == 0:
        head_ref = f"origin/{args.branch}"
    wt = tempfile.mkdtemp(prefix=f"automerge-pr{pr_num}-")
    try:
        added = git(repo, "worktree", "add", "--detach", wt, head_ref)
        if added.returncode != 0:
            gates_ok, gate_notes = False, [
                f"gate FAIL worktree setup: {(added.stderr or '').strip()[:200]}"]
        else:
            repo_checks = policy.get("repo_checks", {}).get(repo, [])
            gates_ok, gate_notes = run_gates(wt, files, policy, checks=repo_checks)
    finally:
        git(repo, "worktree", "remove", "--force", wt)
        shutil.rmtree(wt, ignore_errors=True)
    if not gates_ok:
        hold_reasons.append("verification gates failed (see gate notes)")

    result = {
        "task": task, "pr": args.pr_url, "files": files,
        "changed_lines": total_lines, "gate_notes": gate_notes,
        "hold_reasons": hold_reasons,
    }

    if args.evaluate_only:
        result["decision"] = "hold" if hold_reasons else "automerge"
        print(json.dumps(result, indent=1))
        return 0

    if hold_reasons:
        result["decision"] = "held"
        why = "; ".join(hold_reasons)[:400]
        page(f"pr-held-{pr_num}",
             f"⏸ {task} {args.pr_url} held for your review — {why}. "
             f"Merge it yourself or say the word and an agent can address the blockers.")
        print(json.dumps(result, indent=1))
        return 3

    merge = gh(repo, "pr", "merge", pr_num, "--merge", "--delete-branch")
    if merge.returncode != 0:
        err = (merge.stderr or merge.stdout or "").strip()[:300]
        result["decision"] = "merge_failed"
        result["error"] = err
        page(f"pr-merge-failed-{pr_num}", f"❌ {task} auto-merge FAILED at gh pr merge: {err}")
        print(json.dumps(result, indent=1))
        return 4

    git(repo, "fetch", "origin")
    merge_sha = git(repo, "rev-parse", f"origin/{base}").stdout.strip()[:12]
    deployed = False
    on_branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if on_branch == base:
        pull = git(repo, "pull", "--ff-only", "origin", base)
        deployed = pull.returncode == 0
        if not deployed:
            result["pull_error"] = (pull.stderr or "").strip()[:200]

    if args.issue_id:
        try:
            tm_patch(args.issue_id, {"status": "done", "pr_url": args.pr_url, "actor": "Dwight"})
            result["tm"] = "done"
        except Exception as exc:  # noqa: BLE001 — TM down must not undo the merge
            result["tm"] = f"patch failed: {exc}"

    file_list = ", ".join(files[:6]) + (f" (+{len(files) - 6} more)" if len(files) > 6 else "")
    live = "live checkout updated" if deployed else f"live checkout NOT updated (on {on_branch})"
    page(f"pr-merged-{pr_num}",
         f"🤖 auto-merged {task} {args.pr_url}\n"
         f"files ({len(files)}): {file_list}\n"
         f"{total_lines} lines changed · gates green · {live}\n"
         f"revert: git revert -m 1 {merge_sha} && git push")
    result["decision"] = "merged"
    result["merge_sha"] = merge_sha
    result["deployed"] = deployed
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
