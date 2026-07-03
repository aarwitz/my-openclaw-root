#!/usr/bin/env python3
"""Fail-fast contract check for Task Manager API compatibility.

Run this whenever Task Manager backend routes change.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_openapi(tm_base_url: str) -> dict:
    url = f"{tm_base_url.rstrip('/')}/openapi.json"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; LIDI-Agent/1.0)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    contract_path = os.path.join(root, "task_manager_contract.json")
    tm_base_url = os.environ.get("TM_BASE_URL", "https://tm.lidisolutions.ai")

    contract = load_json(contract_path)
    try:
        openapi = fetch_openapi(tm_base_url)
    except urllib.error.URLError as exc:
        print(f"ERROR: cannot fetch OpenAPI from {tm_base_url}: {exc}", file=sys.stderr)
        return 1

    paths = openapi.get("paths", {})
    failures: list[str] = []

    for path, methods in contract.get("required_paths", {}).items():
        if path not in paths:
            failures.append(f"Missing path: {path}")
            continue
        available = {m.lower() for m in paths[path].keys()}
        for method in methods:
            if method.lower() not in available:
                failures.append(f"Missing method: {method.upper()} {path}")

    if failures:
        print("Task Manager API contract check FAILED:")
        for failure in failures:
            print(f"- {failure}")
        print("\nFix either Task Manager routes or task-manager-mcp contract.")
        return 2

    print("Task Manager API contract check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
