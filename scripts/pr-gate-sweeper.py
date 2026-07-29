#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()

"""pr-gate-sweeper.py — no PR ever sits stranded.

Self-healing delivery (operator decision 2026-07-28). Twice the launcher's
post-PR gate step died silently and completed work sat invisible for days
(TM-222: 10 days; PR #48: 22h with the LIVE TREE left on the work branch).
The fix is idempotent re-drive, not more launcher code: every half hour, list
open PRs on the lane repos and push each through the auto-merge gate. The gate
itself decides merge vs hold-and-page (scripts/merge-policy.json); this script
only guarantees the gate RUNS.

Skips PRs younger than MIN_AGE_MIN so the launcher's own normal path goes
first. A PR the gate holds stays held (the gate re-page is its own reminder);
re-running the gate on a held PR is harmless and picks up new pushes to the
branch (fix-forward: agent pushes a fix commit, sweeper re-gates, merge).
"""

import json
import re
import subprocess
from datetime import datetime, timezone

REPOS = [
    {"slug": "aarwitz/my-openclaw-root", "path": "/home/aaron/.openclaw", "live_branch": "master"},
]
# Branch self-heal covers every LIVE checkout (each has been found stranded on a
# work branch at least once: openclaw 07-27, lidi-solutions TM-213,
# lidi-task-manager 07-29). PR gating stays openclaw-only — the merge policy's
# protected paths are openclaw-shaped.
HEAL_ONLY = [
    {"path": "/home/aaron/repos/lidi-solutions", "live_branch": "main"},
    {"path": "/home/aaron/repos/lidi-task-manager", "live_branch": "main"},
]
MIN_AGE_MIN = 10
GATE = "/home/aaron/.openclaw/scripts/auto-merge-pr.py"
TRACE = "/home/aaron/.openclaw/scripts/run-with-trace.sh"


def sh(cmd, timeout=900):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def heal_live_tree(path: str, live_branch: str = "master") -> dict | None:
    """Self-heal a live tree left on a work branch (2026-07-27: fleet ran an
    issue branch for 22h). Guards: tree clean, no in-flight git op, last commit
    quiet >60min (a lane run is never that quiet), work branch pushed to origin
    BEFORE switching so nothing is lost. Then checkout the live branch + ff-pull."""
    import os
    branch = sh(["git", "-C", path, "branch", "--show-current"]).stdout.strip()
    if branch in (live_branch, ""):
        return None
    if sh(["git", "-C", path, "status", "--porcelain"]).stdout.strip():
        return {"tree": path, "branch": branch, "skipped": "dirty tree — human decision"}
    if os.path.exists(os.path.join(path, ".git", "index.lock")):
        return {"tree": path, "branch": branch, "skipped": "git op in flight"}
    age = sh(["git", "-C", path, "log", "-1", "--format=%ct"]).stdout.strip()
    quiet_min = (datetime.now(timezone.utc).timestamp() - float(age or 0)) / 60.0
    if quiet_min < 60:
        return {"tree": path, "branch": branch, "skipped": f"recent commits ({quiet_min:.0f}m) — lane may be live"}
    sh(["git", "-C", path, "push", "origin", branch])            # preserve work first
    co = sh(["git", "-C", path, "checkout", live_branch])
    if co.returncode != 0:
        return {"tree": path, "branch": branch, "error": co.stderr.strip()[:200]}
    sh(["git", "-C", path, "pull", "--ff-only", "origin", live_branch])
    sh(["/home/aaron/.openclaw/scripts/run-with-trace.sh", "--tag", "cron",
        "/home/aaron/.openclaw/scripts/page-operator.sh", "live-tree-self-healed",
        f"{path} was left on branch '{branch}' — pushed it to origin and restored {live_branch} (work preserved)."])
    return {"tree": path, "branch": branch, "healed": True}


def main() -> int:
    results = []
    for repo in REPOS:
        h = heal_live_tree(repo["path"], repo.get("live_branch", "master"))
        if h:
            results.append(h)
    for tree in HEAL_ONLY:
        h = heal_live_tree(tree["path"], tree["live_branch"])
        if h:
            results.append(h)
    for repo in REPOS:
        r = sh(["gh", "pr", "list", "--repo", repo["slug"], "--state", "open",
                "--json", "number,url,headRefName,createdAt"])
        if r.returncode != 0:
            results.append({"repo": repo["slug"], "error": r.stderr.strip()[:200]})
            continue
        for pr in json.loads(r.stdout or "[]"):
            age_min = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00"))
                       ).total_seconds() / 60.0
            if age_min < MIN_AGE_MIN:
                results.append({"pr": pr["url"], "skipped": f"young ({age_min:.0f}m)"})
                continue
            m = re.search(r"(?:issue|tm)-(\d+)", pr["headRefName"], re.I)
            cmd = [TRACE, "--tag", "cron", GATE,
                   "--repo", repo["path"], "--pr-url", pr["url"],
                   "--branch", pr["headRefName"]]
            if m:
                cmd += ["--issue-id", m.group(1)]
            try:
                g = sh(cmd, timeout=1200)
                tail = (g.stdout or "").strip().splitlines()
                decision = next((l for l in reversed(tail) if "decision" in l), "")
                results.append({"pr": pr["url"], "gate_rc": g.returncode,
                                "decision": decision.strip().strip(",")})
            except subprocess.TimeoutExpired:
                results.append({"pr": pr["url"], "error": "gate timeout"})
    print(json.dumps({"swept_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "results": results}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
