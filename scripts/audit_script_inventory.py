#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()


"""Inventory scripts and report stale/unused candidates.

Data sources:
- scripts directory file list
- ~/.openclaw/logs/script-runs.jsonl (from run-with-trace.sh)

This is a reporting tool only; it does not delete files.
"""


import argparse
from pathlib import Path
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

ROOT = Path.home() / ".openclaw"
SCRIPTS_DIR = ROOT / "scripts"
RUN_LOG = ROOT / "logs" / "script-runs.jsonl"
POLICY_FILE = SCRIPTS_DIR / "policy.json"


def _expand(p: str) -> Path:
    return Path(p.replace("~", str(Path.home()), 1)) if p.startswith("~") else Path(p)


def load_governed_dirs() -> list[tuple[Path, list[str], list[str]]]:
    """Return [(dir, ignore_patterns, exempt_script_basenames), ...] from policy.json.

    Falls back to the single canonical scripts dir if policy is missing.
    """
    if not POLICY_FILE.exists():
        return [(SCRIPTS_DIR, ["lib/", "__pycache__/", "run-with-trace.sh"], [])]
    policy = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    exempt = {str(_expand(e["path"])) for e in policy.get("exemptScripts", [])}
    out: list[tuple[Path, list[str], list[str]]] = []
    for d in policy.get("governedDirs", []):
        dpath = _expand(d["path"])
        if not dpath.is_dir():
            continue
        ignore = list(d.get("ignore", []))
        out.append((dpath, ignore, sorted(exempt)))
    return out


def _ignored(rel: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/"):
            if rel == pat.rstrip("/") or rel.startswith(pat):
                return True
        elif rel == pat:
            return True
    return False


@dataclass
class ScriptInfo:
    path: Path
    ext: str
    mtime: datetime
    last_run: Optional[datetime]
    git_last_commit: Optional[str]
    ref_count: int


def parse_iso(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def load_last_runs() -> Dict[str, datetime]:
    out: Dict[str, datetime] = {}
    if not RUN_LOG.exists():
        return out
    for line in RUN_LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        script = row.get("script")
        ended = parse_iso(str(row.get("ended_at", "")))
        if not script or not ended:
            continue
        prev = out.get(script)
        if not prev or ended > prev:
            out[script] = ended
    return out


def git_last_commit_date(path: Path) -> Optional[str]:
    try:
        cp = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        s = cp.stdout.strip()
        return s or None
    except Exception:
        return None


def reference_count(path: Path) -> int:
    """Count references to this script name outside the scripts directory.

    This is a heuristic signal only. It helps identify likely dead scripts,
    but a low count is not sufficient for deletion.
    """
    name = path.name
    try:
        cp = subprocess.run(
            [
                "rg",
                "-n",
                "--fixed-strings",
                "--glob",
                "!scripts/**",
                name,
                str(ROOT),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if cp.returncode not in (0, 1):
            return 0
        return len([ln for ln in cp.stdout.splitlines() if ln.strip()])
    except Exception:
        return 0


def collect() -> list[ScriptInfo]:
    last_runs = load_last_runs()
    out: list[ScriptInfo] = []
    seen: set[Path] = set()
    for dpath, ignore, exempt in load_governed_dirs():
        for p in sorted(dpath.rglob("*")):
            if not p.is_file():
                continue
            if p.suffix not in (".sh", ".py"):
                continue
            if str(p) in exempt:
                continue
            rel = str(p.relative_to(dpath))
            if _ignored(rel, ignore):
                continue
            if p in seen:
                continue
            seen.add(p)
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            last_run = last_runs.get(str(p))
            out.append(
                ScriptInfo(
                    path=p,
                    ext=p.suffix.lstrip("."),
                    mtime=mtime,
                    last_run=last_run,
                    git_last_commit=git_last_commit_date(p),
                    ref_count=reference_count(p),
                )
            )
    return out


def fmt_dt(d: Optional[datetime]) -> str:
    return d.strftime("%Y-%m-%d") if d else "never"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale-days", type=int, default=45, help="Flag scripts not run in this many days")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of table")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    scripts = collect()

    rows = []
    for s in scripts:
        age_days = None
        if s.last_run:
            age_days = int((now - s.last_run).total_seconds() // 86400)
        stale = s.last_run is None or (age_days is not None and age_days >= args.stale_days)
        rows.append(
            {
                "script": str(s.path),
                "type": s.ext,
                "last_run": s.last_run.isoformat().replace("+00:00", "Z") if s.last_run else None,
                "last_run_age_days": age_days,
                "mtime": s.mtime.isoformat().replace("+00:00", "Z"),
                "git_last_commit": s.git_last_commit,
                "reference_count": s.ref_count,
                "stale_candidate": stale,
                "deletion_candidate": stale and s.ref_count == 0,
            }
        )

    if args.json:
        print(json.dumps({"stale_days": args.stale_days, "scripts": rows}, indent=2))
        return 0

    print(f"Script inventory: {len(rows)} files")
    print(f"Stale threshold: {args.stale_days} days")
    print("")
    print(f"{'script':70} {'type':4} {'last_run':10} {'age':5} {'refs':4} {'stale':5} {'drop?':5} {'git_last_commit':15}")
    print("-" * 120)
    for r in rows:
        script = r["script"]
        if len(script) > 70:
            script = "..." + script[-67:]
        age = "-" if r["last_run_age_days"] is None else str(r["last_run_age_days"])
        stale = "yes" if r["stale_candidate"] else "no"
        drop = "yes" if r["deletion_candidate"] else "no"
        print(f"{script:70} {r['type']:4} {fmt_dt(parse_iso(r['last_run'] or '')):10} {age:5} {r['reference_count']:4} {stale:5} {drop:5} {(r['git_last_commit'] or '-'):15}")

    stale_count = sum(1 for r in rows if r["stale_candidate"])
    never_count = sum(1 for r in rows if r["last_run"] is None)
    drop_count = sum(1 for r in rows if r["deletion_candidate"])
    print("")
    print(f"Summary: stale_candidates={stale_count}, never_traced={never_count}, deletion_candidates={drop_count}")
    print("Next: review deletion candidates manually before deleting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
