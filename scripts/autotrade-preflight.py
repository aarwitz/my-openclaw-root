#!/usr/bin/env python3
"""Offline AutoTrade release gate.

Runs the deterministic contracts that do not require a live market, provider,
gateway, or Task Manager.  This is the answer to "do we have to discover the
next regression in Telegram?": merge and nightly paths run the same suite first.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from require_wrapper import require_wrapper
from config_contract import (
    validate_bootstrap_policy,
    validate_model_policy,
    validate_operator_policy,
    validate_reference_policy,
)

require_wrapper()

ROOT = Path(__file__).resolve().parents[1]


def _first_json(output: str):
    start = output.find("{")
    if start < 0:
        raise ValueError("no JSON object in output")
    value, _end = json.JSONDecoder().raw_decode(output[start:])
    return value


def _run(name: str, command: list[str], timeout: int = 240, json_ok: bool = False) -> dict:
    started = time.monotonic()
    proc = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "OPENCLAW_RUN_WITH_TRACE": "1"},
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0
    parsed = None
    if json_ok and ok:
        try:
            parsed = _first_json(output)
            ok = bool(parsed.get("ok"))
        except (ValueError, TypeError, AttributeError):
            ok = False
    return {
        "name": name,
        "ok": ok,
        "exit_code": proc.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "detail": "ok" if ok else output[-4000:],
        "parsed": parsed,
    }


def _config_contract() -> dict:
    started = time.monotonic()
    errors = []
    try:
        live = json.loads((ROOT / "openclaw.json").read_text())
        last_good = json.loads((ROOT / "openclaw.json.last-good").read_text())
        if live != last_good:
            errors.append("openclaw.json differs from .last-good")
        errors.extend(validate_model_policy(live, ROOT))
        errors.extend(validate_bootstrap_policy(live))
        errors.extend(validate_operator_policy(live))
        errors.extend(validate_reference_policy(live, ROOT))
        jobs = json.loads((ROOT / "cron/jobs.json").read_text())
        if not isinstance(jobs.get("jobs"), list):
            errors.append("cron/jobs.json has no jobs list")
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
    return {
        "name": "config-json-contract",
        "ok": not errors,
        "exit_code": 0 if not errors else 1,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "detail": "ok" if not errors else "; ".join(errors),
        "parsed": None,
    }


def _test_discovery_contract() -> dict:
    """Reject test_*.py files that unittest discovery would silently ignore."""
    started = time.monotonic()
    errors = []
    for directory in (
        ROOT / "workspaces/trading-intel/scripts",
        ROOT / "workspaces/quant/scripts",
    ):
        for path in sorted(directory.glob("test_*.py")):
            try:
                tree = ast.parse(path.read_text(), filename=str(path))
            except (OSError, SyntaxError) as exc:
                errors.append(f"{path.relative_to(ROOT)}: {exc}")
                continue
            discoverable = any(
                isinstance(node, ast.ClassDef)
                and any(
                    (isinstance(base, ast.Attribute) and base.attr == "TestCase")
                    or (isinstance(base, ast.Name) and base.id == "TestCase")
                    for base in node.bases
                )
                for node in tree.body
            )
            if not discoverable:
                errors.append(
                    f"{path.relative_to(ROOT)} has no unittest.TestCase; "
                    "unittest discover would run zero assertions"
                )
    return {
        "name": "test-discovery-contract",
        "ok": not errors,
        "exit_code": 0 if not errors else 1,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "detail": "ok" if not errors else "; ".join(errors),
        "parsed": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="run the high-value guardrail subset instead of all trading unit tests",
    )
    parser.add_argument("--json", action="store_true", help="emit only the final JSON report")
    args = parser.parse_args()

    py = sys.executable
    shell_files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "scripts").glob("*.sh"))
    checks = [
        _config_contract(),
        _test_discovery_contract(),
        _run(
            "python-syntax",
            [py, "-m", "compileall", "-q", "scripts", "workspaces/trading-intel/scripts",
             "workspaces/quant/scripts", "workspaces/trader/scripts", "workspaces/risk/scripts",
             "workspaces/executor/scripts", "workspaces/archivist/scripts"],
        ),
        _run("shell-syntax", ["bash", "-n", *shell_files]),
        _run("doc-contract", [py, "scripts/doc-lint.py"], json_ok=True),
        _run("internal-paper-only", [py, "scripts/check-internal-paper-only.py"], json_ok=True),
    ]

    if args.quick:
        checks.append(_run(
            "trading-guardrails",
            [py, "workspaces/trading-intel/scripts/test_preproduction_guardrails.py"],
        ))
    else:
        checks.append(_run(
            "trading-unit-suite",
            [py, "-m", "unittest", "discover", "-s", "workspaces/trading-intel/scripts",
             "-p", "test_*.py"],
        ))
    checks.extend([
        _run(
            "trading-day-scenarios",
            [py, "scripts/autotrade-scenario-replay.py"],
            timeout=120,
            json_ok=True,
        ),
        _run(
            "quant-unit-suite",
            [py, "-m", "unittest", "discover", "-s", "workspaces/quant/scripts",
             "-p", "test_*.py"],
        ),
        _run("money-path", [py, "scripts/money-path-tests.py"]),
    ])

    failures = [check for check in checks if not check["ok"]]
    report = {
        "ok": not failures,
        "mode": "quick" if args.quick else "full",
        "checks": checks,
        "summary": f"{len(checks) - len(failures)}/{len(checks)} checks passed",
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for check in checks:
            mark = "ok" if check["ok"] else "FAIL"
            print(f"[{mark:4}] {check['name']}: {check['elapsed_seconds']:.3f}s")
            if not check["ok"]:
                print(check["detail"])
        print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
