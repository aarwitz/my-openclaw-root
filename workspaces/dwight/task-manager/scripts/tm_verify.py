#!/usr/bin/env python3
import argparse
import json
import sqlite3
import subprocess
import sys
import urllib.request
from urllib.parse import urlparse
from pathlib import Path


def get_json(base_url: str, path: str):
    with urllib.request.urlopen(f"{base_url}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def canonical_db_path() -> Path:
    # scripts/tm_verify.py -> scripts -> task-manager -> dwight workspace root
    return Path(__file__).resolve().parents[2] / "taskmanager.db"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Task Manager service and DB integrity")
    parser.add_argument("--base-url", default="https://tm.lidisolutions.ai")
    parser.add_argument("--db", default=str(canonical_db_path()))
    parser.add_argument("--expect-issues", type=int, default=None)
    parser.add_argument("--expect-sprints", type=int, default=None)
    parser.add_argument("--expect-comments", type=int, default=None)
    parser.add_argument("--expect-sprint5", default=None)
    args = parser.parse_args()

    failures = []

    try:
        with urllib.request.urlopen(f"{args.base_url}/", timeout=10) as resp:
            if resp.status != 200:
                failures.append(f"GET / returned status={resp.status}")
    except Exception as exc:
        failures.append(f"GET / failed: {exc}")

    try:
        issues = get_json(args.base_url, "/api/issues")
        sprints = get_json(args.base_url, "/api/sprints")
    except Exception as exc:
        failures.append(f"API calls failed: {exc}")
        issues, sprints = [], []

    if args.expect_issues is not None and len(issues) != args.expect_issues:
        failures.append(f"issues count mismatch: expected={args.expect_issues} actual={len(issues)}")
    if args.expect_sprints is not None and len(sprints) != args.expect_sprints:
        failures.append(f"sprints count mismatch: expected={args.expect_sprints} actual={len(sprints)}")

    ids = {item.get("id") for item in issues if isinstance(item, dict)}
    missing = [i for i in range(120, 126) if i not in ids]
    if missing:
        failures.append(f"missing issue IDs in 120-125: {missing}")

    db_path = Path(args.db).resolve()
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        comments_count = cur.execute("select count(*) from comments").fetchone()[0]
        sprint5 = cur.execute("select name from sprints where id=5").fetchone()
        conn.close()
    except Exception as exc:
        failures.append(f"DB checks failed for {db_path}: {exc}")
        comments_count = -1
        sprint5 = None

    if args.expect_comments is not None and comments_count != args.expect_comments:
        failures.append(
            f"comments count mismatch: expected={args.expect_comments} actual={comments_count}"
        )
    sprint5_name = sprint5[0] if sprint5 else None
    if args.expect_sprint5 is not None and sprint5_name != args.expect_sprint5:
        failures.append(
            f"sprint 5 name mismatch: expected={args.expect_sprint5} actual={sprint5_name}"
        )

    pid = ""
    parsed = urlparse(args.base_url)
    if (parsed.hostname or "").lower() in {"127.0.0.1", "localhost"}:
        listen_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            pid = subprocess.check_output(
                f"ss -ltnp | awk '/:{listen_port}\\b/ && /python3/ {{print $NF}}' | sed -E 's/.*pid=([0-9]+).*/\\\\1/' | head -n1",
                shell=True,
                text=True,
            ).strip()
        except Exception:
            pass

    print(f"base_url={args.base_url}")
    print(f"db_path={db_path}")
    if pid:
        print(f"pid={pid}")
    print(f"issues={len(issues)}")
    print(f"sprints={len(sprints)}")
    print(f"comments={comments_count}")
    print(f"sprint_5={sprint5_name}")

    if failures:
        print("verification=FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("verification=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
