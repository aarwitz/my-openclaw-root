#!/usr/bin/env python3
"""Fail closed if the retired external broker can re-enter AutoTrade.

Historical decision records and schema migrations retain provider names so old
ledgers remain explainable and migratable. Runtime code, configuration,
credentials, prompts, and GUI source must not contain them.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
GUI_ROOT = Path("/home/aaron/repos/lidi-solutions")
SELF = Path(__file__).resolve()
RETIRED_PROVIDER = "alpaca"

FORBIDDEN_PATHS = (
    ROOT / "credentials" / "alpaca-api.json",
    ROOT / "config" / "broker-backend",
    ROOT / "workspace" / "skills" / "alpaca",
    ROOT / "workspaces" / "trading-intel" / "scripts" / "connectors" / "alpaca.py",
)

HISTORICAL_ALLOWLIST = {
    ROOT / "workspaces" / "trading-intel" / "DECISION_LOG.md",
    ROOT / "workspaces" / "trading-intel" / "FINDINGS.md",
}

ROOT_SCAN_DIRS = (
    ROOT / "scripts",
    ROOT / "workspace",
    ROOT / "workspaces",
)
ROOT_SUFFIXES = {
    ".cjs", ".html", ".js", ".json", ".jsx", ".md", ".mjs", ".py",
    ".sh", ".sql", ".toml", ".ts", ".tsx", ".yaml", ".yml",
}
GUI_SCAN_DIRS = (
    GUI_ROOT / "functions",
    GUI_ROOT / "scripts",
    GUI_ROOT / "src",
    GUI_ROOT / "public" / "solutions" / "trader_intel" / "app",
)
GUI_SUFFIXES = {".cjs", ".html", ".js", ".jsx", ".md", ".mjs", ".ts", ".tsx"}
GUI_ALLOWLIST = {
    # Security-only signatures must continue redacting retired credentials from
    # archived text and accidental payloads.
    GUI_ROOT / "scripts" / "lib" / "redact.mjs",
}


def _historical(path: Path) -> bool:
    if path in HISTORICAL_ALLOWLIST:
        return True
    migrations = ROOT / "workspaces" / "trading-intel" / "sql" / "migrations"
    try:
        path.relative_to(migrations)
        return True
    except ValueError:
        return False


def _scan(directories: tuple[Path, ...], suffixes: set[str], allowlist: set[Path]) -> list[dict]:
    violations: list[dict] = []
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            resolved = path.resolve()
            if resolved == SELF or resolved in allowlist or _historical(resolved):
                continue
            if any(part in {".git", "__pycache__", "node_modules"} for part in path.parts):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                violations.append({"path": str(path), "line": 0, "detail": f"unreadable: {exc}"})
                continue
            for lineno, line in enumerate(lines, 1):
                if RETIRED_PROVIDER in line.lower():
                    violations.append({
                        "path": str(path),
                        "line": lineno,
                        "detail": "retired provider reference in active source",
                    })
    return violations


def check() -> dict:
    violations = [
        {"path": str(path), "line": 0, "detail": "forbidden legacy path exists"}
        for path in FORBIDDEN_PATHS
        if path.exists()
    ]
    violations.extend(_scan(ROOT_SCAN_DIRS, ROOT_SUFFIXES, set()))
    if GUI_ROOT.exists():
        violations.extend(_scan(GUI_SCAN_DIRS, GUI_SUFFIXES, GUI_ALLOWLIST))
    return {
        "ok": not violations,
        "architecture": "internal-paper-only",
        "execution_backend": "sim",
        "violations": violations,
    }


def main() -> int:
    report = check()
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
