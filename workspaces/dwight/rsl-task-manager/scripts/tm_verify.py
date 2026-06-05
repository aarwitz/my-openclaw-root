#!/usr/bin/env python3
import argparse
import json
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path


def get_json(base_url: str, path: str):
    with urllib.request.urlopen(f"{base_url}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Task Manager service and DB integrity")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--db", default="/home/aaron/.openclaw/workspaces/dwight/taskmanager.db")
    parser.add_argument("--expect-issues", type=int, default=143)
    parser.add_argument("--expect-sprints", type=int, default=10)
    parser.add_argument("--expect-comments", type=int, default=236)
    parser.add_argument("--expect-sprint5", default="ATS v6 Trading Intel")
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

    if len(issues) != args.expect_issues:
        failures.append(f"issues count mismatch: expected={args.expect_issues} actual={len(issues)}")
    if len(sprints) != args.expect_sprints:
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

    if comments_count != args.expect_comments:
        failures.append(
            f"comments count mismatch: expected={args.expect_comments} actual={comments_count}"
        )
    sprint5_name = sprint5[0] if sprint5 else None
    if sprint5_name != args.expect_sprint5:
        failures.append(
            f"sprint 5 name mismatch: expected={args.expect_sprint5} actual={sprint5_name}"
        )

    pid = ""
    try:
        pid = subprocess.check_output(
            "ss -ltnp | awk '/:8000/ && /python3/ {print $NF}' | sed -E 's/.*pid=([0-9]+).*/\\1/' | head -n1",
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
