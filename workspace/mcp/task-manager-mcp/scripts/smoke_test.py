#!/usr/bin/env python3
"""Read-only smoke test for task-manager-mcp dependencies.

Validates endpoint reachability and expected JSON response shape.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


TM_BASE_URL = os.environ.get("TM_BASE_URL", "https://tm.lidisolutions.ai").rstrip("/")


def get_json(path: str):
    url = f"{TM_BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; LIDI-Agent/1.0)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def assert_keys(name: str, payload: dict, keys: list[str]) -> None:
    missing = [k for k in keys if k not in payload]
    if missing:
        raise RuntimeError(f"{name} missing keys: {', '.join(missing)}")


def main() -> int:
    try:
        issues = get_json("/api/issues")
        if not isinstance(issues, list):
            raise RuntimeError("/api/issues did not return a list")

        active = get_json("/api/sprints/active")
        if not isinstance(active, dict):
            raise RuntimeError("/api/sprints/active did not return an object")

        if issues:
            issue = issues[0]
            if not isinstance(issue, dict):
                raise RuntimeError("first issue is not an object")
            assert_keys("issue", issue, ["id", "title", "status"])

        print("task-manager-mcp smoke test passed")
        return 0
    except (urllib.error.URLError, RuntimeError, ValueError) as exc:
        print(f"task-manager-mcp smoke test failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
