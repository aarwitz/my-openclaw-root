#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()

"""tm-git-reconcile.py — make Task Manager reflect git, never the reverse.

Operator decision 2026-07-20 ("demote TM"): git is execution truth; TM is the
intent/evidence ledger. Ghost states — issues stuck in `in_review` after their
PR already merged (or was closed) — caused daily false "Needs Aaron" nags, so
this sweep closes them deterministically:

  For every issue in status `in_review` with a `pr_url`:
    - PR MERGED  -> PATCH status=done (+ merge sha) and comment the evidence.
    - PR CLOSED  -> comment loudly (needs a human/agent decision); status kept.
    - PR OPEN    -> leave alone (the auto-merge gate owns live PRs).
  Issues without a pr_url, or in any other status, are never touched — e.g. an
  intentionally-open issue whose branch merged partial work (TM-243 pattern)
  stays open.

Runs nightly from host cron (18:10 ET weekdays); safe to re-run any time.
Emits one JSON summary line on stdout. Exit 0 unless TM is unreachable.
"""

import json
import os
import re
import subprocess
import urllib.request

TM_BASE = os.environ.get("TASK_MANAGER_URL", "https://tm.lidisolutions.ai").rstrip("/")
TM_CRED = os.path.expanduser("~/.openclaw/credentials/task-manager-agent.json")
ROUTER = os.path.expanduser("~/.openclaw/scripts/gh-account-router.sh")


def tm_request(method, path, payload=None):
    with open(TM_CRED) as fh:
        token = json.load(fh)["session_token"]
    req = urllib.request.Request(
        f"{TM_BASE}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0 (compatible; LIDI-Agent/1.0)",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def pr_state(pr_url):
    """Return (state, merge_sha) via gh; state in MERGED/CLOSED/OPEN/UNKNOWN."""
    m = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", pr_url)
    if not m:
        return "UNKNOWN", ""
    slug, num = m.group(1), m.group(2)
    prefix = [ROUTER, "--agent", "dwight"] if os.path.exists(ROUTER) else ["gh"]
    run = subprocess.run(
        [*prefix, "pr", "view", num, "--repo", slug, "--json", "state,mergeCommit"],
        capture_output=True, text=True, check=False, timeout=60,
    )
    if run.returncode != 0:
        return "UNKNOWN", ""
    data = json.loads(run.stdout)
    sha = ((data.get("mergeCommit") or {}).get("oid") or "")[:12]
    return data.get("state", "UNKNOWN"), sha


def main() -> int:
    dry = "--dry-run" in sys.argv
    issues = tm_request("GET", "/api/issues")
    if isinstance(issues, dict):
        issues = issues.get("issues", [])
    summary = {"checked": 0, "closed_as_done": [], "pr_closed_unmerged": [], "left_open": []}
    for issue in issues:
        if issue.get("status") != "in_review" or not issue.get("pr_url"):
            continue
        summary["checked"] += 1
        iid = issue["id"]
        state, sha = pr_state(issue["pr_url"])
        if state == "MERGED":
            summary["closed_as_done"].append(iid)
            if not dry:
                tm_request("PATCH", f"/api/issues/{iid}",
                           {"status": "done", "actor": "Dwight"})
                tm_request("POST", f"/api/issues/{iid}/comments", {
                    "content": (f"Auto-reconciled from git: {issue['pr_url']} is MERGED"
                                + (f" (merge {sha})" if sha else "")
                                + ". git is execution truth; closing the ghost in_review state."),
                    "username": "Dwight",
                })
        elif state == "CLOSED":
            summary["pr_closed_unmerged"].append(iid)
            if not dry:
                tm_request("POST", f"/api/issues/{iid}/comments", {
                    "content": (f"⚠️ Reconcile check: {issue['pr_url']} was CLOSED without "
                                "merging but this issue is still in_review — needs a human or "
                                "agent decision (re-open the work or close the issue)."),
                    "username": "Dwight",
                })
        else:
            summary["left_open"].append(iid)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
