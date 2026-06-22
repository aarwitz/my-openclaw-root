#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()

"""Deterministic system health sweep for the OpenClaw gateway + AutoTrade desk.

Read-only. Produces structured JSON findings (one per check) that an agent
(Jerry's morning job) narrates over Telegram, escalating only warn/crit items.

Container-safe by design: runs as an agent INSIDE the gateway container, which
has no docker.sock. So it relies on files, the `openclaw` CLI, sqlite, and HTTP
to the task manager — never `docker`. Honors TASK_MANAGER_URL.

Checks: gateway health, telegram channels (spool backlog = wedge signal),
cron sanity (enabled-count + interrupted-restart artifacts), model/token auth,
task manager reachability, disk/log growth, AutoTrade pipeline freshness.
"""

import glob
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = "/home/aaron/.openclaw"
SEV = {"ok": 0, "warn": 1, "crit": 2}
NOW = time.time()


def finding(check, severity, detail, **extra):
    f = {"check": check, "severity": severity, "detail": detail}
    f.update(extra)
    return f


def _load_json(path):
    with open(path) as fh:
        return json.load(fh)


def _resolve_openclaw():
    p = shutil.which("openclaw")
    if p:
        return p
    try:
        out = subprocess.run(
            [f"{ROOT}/scripts/resolve-openclaw-bin.sh"],
            capture_output=True, text=True, timeout=10,
        )
        cand = out.stdout.strip()
        if cand and os.path.exists(cand):
            return cand
    except Exception:
        pass
    return None


def _run(args, timeout=25):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 127, f"exec error: {e}"


# --- checks -----------------------------------------------------------------

def check_gateway():
    ocl = _resolve_openclaw()
    if not ocl:
        return finding("gateway", "warn", "openclaw CLI not found; cannot probe gateway health")
    rc, out = _run([ocl, "health"], timeout=25)
    if rc != 0:
        return finding("gateway", "crit", f"`openclaw health` exit={rc}: {out.strip()[:160]}")
    lag = ""
    for line in out.splitlines():
        if "event loop" in line.lower():
            lag = " (" + line.strip() + ")"
            break
    return finding("gateway", "ok", "gateway healthy" + lag)


def check_telegram():
    try:
        cfg = _load_json(f"{ROOT}/openclaw.json")
        accts = list(cfg["channels"]["telegram"]["accounts"].keys())
    except Exception as e:
        return finding("telegram", "warn", f"could not read telegram accounts: {e}")
    details = []
    problems = []
    worst = "ok"
    for a in accts:
        files = glob.glob(f"{ROOT}/telegram/ingress-spool-{a}/*.json")
        if not files:
            continue
        oldest_min = (NOW - min(os.path.getmtime(f) for f in files)) / 60.0
        if oldest_min > 15:
            sev = "crit"
        elif oldest_min > 2:
            sev = "warn"
        else:
            sev = "ok"  # just-arrived, will drain
        if sev != "ok":
            problems.append(a)
            details.append(f"{a}: {len(files)} spooled, oldest {oldest_min:.0f}m (channel likely wedged)")
            if SEV[sev] > SEV[worst]:
                worst = sev
    if worst == "ok":
        return finding("telegram", "ok", f"all {len(accts)} bot channels draining ({', '.join(accts)})")
    return finding("telegram", worst, "; ".join(details), accounts=problems)


def check_cron():
    path = f"{ROOT}/cron/jobs.json"
    try:
        d = _load_json(path)
    except Exception as e:
        return finding("cron", "crit", f"cron/jobs.json invalid/unreadable: {e}")
    jobs = d.get("jobs", [])
    enabled = sum(1 for j in jobs if j.get("enabled"))
    notes = []
    sev = "ok"
    if enabled == 0:
        sev = "crit"
        notes.append("0 jobs enabled (whole desk disabled — possible interrupted safe-restart)")
    leftovers = [
        p for p in (f"{path}.pre-restart-enabled-ids", f"{path}.pre-restart-bak")
        if os.path.exists(p)
    ]
    if leftovers:
        if sev == "ok":
            sev = "warn"
        notes.append("interrupted-restart artifact present: " + ", ".join(os.path.basename(p) for p in leftovers))
    detail = f"{len(jobs)} jobs, {enabled} enabled" + (" — " + "; ".join(notes) if notes else "")
    return finding("cron", sev, detail)


def check_tokens():
    ocl = _resolve_openclaw()
    if not ocl:
        return finding("tokens", "warn", "openclaw CLI not found; cannot check model auth")
    rc, out = _run([ocl, "models", "status", "--check"], timeout=40)
    low = out.lower()
    if rc == 0:
        return finding("tokens", "ok", "model auth usable")
    if "usable" in low and not any(x in low for x in ("missing", "invalid", "unusable")):
        return finding("tokens", "warn", "model auth usable but flagged (likely expiring soon)")
    return finding("tokens", "crit", f"model auth check failed (exit={rc}): {out.strip()[:160]}")


def check_taskmanager():
    url = os.environ.get("TASK_MANAGER_URL", "http://127.0.0.1:8000").rstrip("/")
    for path in ("/health", "/"):
        try:
            req = urllib.request.Request(url + path, method="GET")
            with urllib.request.urlopen(req, timeout=6) as r:
                return finding("taskmanager", "ok", f"reachable at {url} (HTTP {r.status})")
        except urllib.error.HTTPError as e:
            # server answered → it's up
            return finding("taskmanager", "ok", f"reachable at {url} (HTTP {e.code})")
        except Exception:
            continue
    return finding("taskmanager", "crit", f"task manager unreachable at {url}")


def check_disk():
    try:
        total, used, free = shutil.disk_usage(ROOT)
    except Exception as e:
        return finding("disk", "warn", f"disk_usage failed: {e}")
    free_gb = free / 1e9
    logs_bytes = 0
    for dirpath, _, names in os.walk(f"{ROOT}/logs"):
        for n in names:
            try:
                logs_bytes += os.path.getsize(os.path.join(dirpath, n))
            except OSError:
                pass
    logs_mb = logs_bytes / 1e6
    sev = "ok"
    if free_gb < 0.5:
        sev = "crit"
    elif free_gb < 2 or logs_mb > 2000:
        sev = "warn"
    return finding("disk", sev, f"{free_gb:.1f} GB free, logs/ = {logs_mb:.0f} MB")


def check_pipeline():
    db = f"{ROOT}/state/trading-intel.sqlite"
    if not os.path.exists(db):
        return finding("pipeline", "warn", "trading-intel.sqlite not found")
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
        last = c.execute("SELECT MAX(timestamp) FROM audits").fetchone()[0]
        since = datetime.fromtimestamp(NOW - 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        n24 = c.execute("SELECT COUNT(*) FROM audits WHERE timestamp >= ?", (since,)).fetchone()[0]
        c.close()
    except Exception as e:
        return finding("pipeline", "warn", f"audits query failed: {e}")
    if not last:
        return finding("pipeline", "warn", "no audit rows found")
    try:
        dt = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age_h = (NOW - dt.timestamp()) / 3600.0
    except Exception:
        return finding("pipeline", "ok", f"last audit {last} ({n24} in 24h)")
    # tolerant of weekends/holidays (no market passes)
    if age_h > 120:
        sev = "crit"
    elif age_h > 48:
        sev = "warn"
    else:
        sev = "ok"
    return finding("pipeline", sev, f"last audit {age_h:.0f}h ago ({last}), {n24} in 24h")


CHECKS = [
    check_gateway, check_telegram, check_cron, check_tokens,
    check_taskmanager, check_disk, check_pipeline,
]


def main() -> int:
    findings = []
    for fn in CHECKS:
        try:
            findings.append(fn())
        except Exception as e:
            findings.append(finding(fn.__name__.replace("check_", ""), "warn", f"check raised: {e}"))
    overall = max((f["severity"] for f in findings), key=lambda s: SEV[s], default="ok")
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("ok", "warn", "crit")}
    result = {
        "generated_at": datetime.fromtimestamp(NOW, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overall": overall,
        "counts": counts,
        "summary": f"{counts['ok']} ok / {counts['warn']} warn / {counts['crit']} crit",
        "findings": findings,
        "escalate": [f for f in findings if f["severity"] != "ok"],
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
