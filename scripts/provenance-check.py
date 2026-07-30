#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()

"""provenance-check.py — deterministic answers to "what code is running, from where?"

Born 2026-07-29 after a week of provenance failures:
  * ~/.openclaw ran a coding-lane issue branch for 22h (fleet on non-master code);
  * ~/repos/lidi-task-manager sat stranded on a work branch while serving live TM;
  * lidi-solutions was left on a work branch by TM-213;
  * the operator session ssh'd into the host it was ALREADY ON ("RSL"), concluded
    the TM deploy source was unreachable, and mis-grepped a template-built string
    into "the live code isn't in this repo at all".

Never again by assertion, not memory. Three check families:
  identity  — this host IS RSL; the deploy sources are LOCAL paths on it.
  repos     — every registered live checkout is ON its expected branch, has no
              tracked or untracked changes, and is not diverged from origin.
  deployed  — live endpoints report the git sha they were built from, and that
              sha exists in (and matches) the local repo. Endpoints that don't
              expose a sha yet report as UNVERIFIABLE (warn), which is the
              honest state — not silence.

Exit: 0 all pass (unverifiable tolerated with --allow-unverifiable, default);
      2 any FAIL. --json for machine use (system-health-sweep consumes this).
"""

import argparse
import json
import subprocess
import socket
import urllib.request

REGISTRY = {
    "host": {"expected_hostname": "RSL"},
    "repos": [
        {"path": "/home/aaron/.openclaw", "branch": "master",
         "role": "live fleet tree (every cron/agent executes from it)"},
        {"path": "/home/aaron/repos/lidi-solutions", "branch": "main",
         "role": "lidisolutions.ai + trader-intel app source"},
        {"path": "/home/aaron/repos/lidi-task-manager", "branch": "main",
         "role": "tm.lidisolutions.ai worker source (deploys from THIS machine)"},
    ],
    "deployed": [
        {"name": "trader-intel-pages", "repo": "/home/aaron/repos/lidi-solutions",
         "url": "https://lidisolutions.ai/version.json", "sha_key": "sha"},
        {"name": "tm-worker", "repo": "/home/aaron/repos/lidi-task-manager",
         "url": "https://tm.lidisolutions.ai/api/version", "sha_key": "sha"},
    ],
}


def sh(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def check_host(out):
    hn = socket.gethostname()
    want = REGISTRY["host"]["expected_hostname"]
    out.append({"check": "host_identity", "status": "PASS" if hn == want else "FAIL",
                "detail": f"hostname={hn} (expected {want}) — deploy sources are LOCAL; never ssh to '{want}'"})


def check_repo(spec, out):
    p = spec["path"]
    rc, branch, _ = sh(["git", "-C", p, "branch", "--show-current"])
    if rc != 0:
        out.append({"check": f"repo:{p}", "status": "FAIL", "detail": "not a readable git repo"})
        return
    ok, problems = True, []
    if branch != spec["branch"]:
        ok = False
        problems.append(f"ON '{branch or 'detached HEAD'}' (expected {spec['branch']}) — {spec['role']}")
    _, dirty, _ = sh(["git", "-C", p, "status", "--porcelain"])
    # porcelain lines are "XY path"; strip the 2 status cols + separator robustly
    paths = [ln[2:].strip() for ln in dirty.splitlines() if ln.strip()]
    real_dirt = paths
    if real_dirt:
        ok = False
        problems.append(f"{len(real_dirt)} uncommitted or untracked change(s): "
                        + ", ".join(real_dirt[:4]))
    sh(["git", "-C", p, "fetch", "origin", "--quiet"])
    _, local, _ = sh(["git", "-C", p, "rev-parse", spec["branch"]])
    rc2, remote, _ = sh(["git", "-C", p, "rev-parse", f"origin/{spec['branch']}"])
    if rc2 == 0 and local and remote and local != remote:
        _, ahead, _ = sh(["git", "-C", p, "rev-list", "--count", f"origin/{spec['branch']}..{spec['branch']}"])
        _, behind, _ = sh(["git", "-C", p, "rev-list", "--count", f"{spec['branch']}..origin/{spec['branch']}"])
        ok = False
        problems.append(f"diverged from origin/{spec['branch']} (ahead {ahead}, behind {behind})")
    out.append({"check": f"repo:{p}", "status": "PASS" if ok else "FAIL",
                "detail": "; ".join(problems) or f"on {spec['branch']}, clean, synced with origin"})


def check_deployed(spec, out):
    try:
        req = urllib.request.Request(spec["url"], headers={"User-Agent": "provenance-check/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
        sha = str(body.get(spec["sha_key"]) or "")
    except Exception as exc:
        out.append({"check": f"deployed:{spec['name']}", "status": "UNVERIFIABLE",
                    "detail": f"{spec['url']} unreadable ({type(exc).__name__}) — deploy has no version "
                              "stamp yet; next deploy via the stamped path activates this check"})
        return
    if not sha:
        out.append({"check": f"deployed:{spec['name']}", "status": "UNVERIFIABLE",
                    "detail": f"{spec['url']} returned no '{spec['sha_key']}'"})
        return
    rc, _, _ = sh(["git", "-C", spec["repo"], "cat-file", "-e", f"{sha}^{{commit}}"])
    if rc != 0:
        out.append({"check": f"deployed:{spec['name']}", "status": "FAIL",
                    "detail": f"live sha {sha[:12]} does NOT exist in {spec['repo']} — deployed from "
                              "somewhere else or from unpushed/lost state"})
        return
    _, head, _ = sh(["git", "-C", spec["repo"], "rev-parse", "origin/main"])
    same = sha == head
    rc3, _, _ = sh(["git", "-C", spec["repo"], "merge-base", "--is-ancestor", sha, "origin/main"])
    out.append({"check": f"deployed:{spec['name']}", "status": "PASS" if (same or rc3 == 0) else "FAIL",
                "detail": (f"live sha {sha[:12]} == origin/main" if same else
                           f"live sha {sha[:12]} is an ancestor of origin/main (older deploy, known commit)"
                           if rc3 == 0 else
                           f"live sha {sha[:12]} exists locally but is NOT on origin/main — deployed from a side branch")})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="UNVERIFIABLE counts as failure (use once all deploys are stamped)")
    args = ap.parse_args()

    out = []
    check_host(out)
    for spec in REGISTRY["repos"]:
        check_repo(spec, out)
    for spec in REGISTRY["deployed"]:
        check_deployed(spec, out)

    fails = [c for c in out if c["status"] == "FAIL"]
    unver = [c for c in out if c["status"] == "UNVERIFIABLE"]
    verdict = "FAIL" if fails or (args.strict and unver) else "PASS"
    report = {"verdict": verdict, "fails": len(fails), "unverifiable": len(unver), "checks": out}
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        for c in out:
            mark = {"PASS": "ok  ", "FAIL": "FAIL", "UNVERIFIABLE": "warn"}[c["status"]]
            print(f"[{mark}] {c['check']}: {c['detail']}")
        print(f"VERDICT: {verdict} ({len(fails)} fail, {len(unver)} unverifiable)")
    return 2 if verdict == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
