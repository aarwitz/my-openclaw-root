#!/usr/bin/env python3
"""Offline, no-write replay of AutoTrade's trading-day orchestration.

The component unit suite proves individual math and state transitions. This
runner proves the shell-level control plane: market-day branching, stage order,
critical-dependency circuit breaking, advisory failure behavior, post-failure
diagnostics, JSON output, and exit codes. It never invokes a stage command and
never mutates the trading database.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from require_wrapper import require_wrapper

require_wrapper()

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "trader-pass-deterministic.sh"
LEDGER = ROOT / "state" / "trading-intel.sqlite"


def _first_json(text: str) -> dict:
    start = text.find("{")
    if start < 0:
        raise ValueError("pipeline emitted no JSON object")
    value, _end = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(value, dict):
        raise ValueError("pipeline output was not a JSON object")
    return value


def _run(name: str, *, trading_day: bool, fail_steps: tuple[str, ...] = ()) -> dict:
    env = {
        **os.environ,
        "OPENCLAW_RUN_WITH_TRACE": "1",
        "AUTOTRADE_SCENARIO_TRADING_DAY": "1" if trading_day else "0",
        "AUTOTRADE_SCENARIO_FAIL_STEPS": ",".join(fail_steps),
    }
    started = time.monotonic()
    proc = subprocess.run(
        ["bash", str(PIPELINE), "--scenario", "--skip-snapshot"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    try:
        report = _first_json(combined)
        parse_error = None
    except ValueError as exc:
        report = {}
        parse_error = str(exc)
    return {
        "name": name,
        "exit_code": proc.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "report": report,
        "parse_error": parse_error,
        "output_tail": combined[-2000:],
    }


def _assert(case: dict, condition: bool, detail: str) -> None:
    if not condition:
        case.setdefault("failures", []).append(detail)


def _stage_order(report: dict, first: str, second: str) -> bool:
    keys = list(report)
    return first in keys and second in keys and keys.index(first) < keys.index(second)


def main() -> int:
    before = (LEDGER.stat().st_size, LEDGER.stat().st_mtime_ns) if LEDGER.exists() else None
    temporary_before = set(Path("/tmp").glob("autotrade-internal-paper-check.*.json"))
    cases = [
        _run("regular-session", trading_day=True),
        _run("exchange-closed", trading_day=False),
        _run("critical-upstream-failure", trading_day=True, fail_steps=("classify_regime",)),
        _run("ledger-divergence", trading_day=True, fail_steps=("reconcile_preflight",)),
        _run("advisory-stage-failure", trading_day=True, fail_steps=("ml_evidence_track",)),
    ]

    regular, closed, upstream, ledger, advisory = cases
    for case in cases:
        case["failures"] = []
        _assert(case, case["parse_error"] is None, case["parse_error"] or "JSON parse failed")
        _assert(case, bool(case["report"].get("scenario", {}).get("enabled")), "scenario marker missing")

    r = regular["report"]
    _assert(regular, regular["exit_code"] == 0, "regular session must exit 0")
    _assert(regular, r.get("market_today", {}).get("trading_day") is True, "regular session clock branch wrong")
    _assert(regular, not r.get("execute_intent", {}).get("skipped"), "healthy session did not arm executor")
    _assert(regular, _stage_order(r, "risk_gate", "sim_integrity_pre"), "risk must precede ledger preflight")
    _assert(regular, _stage_order(r, "sim_integrity_pre", "reconcile_preflight"), "ledger integrity must precede reconcile preflight")
    _assert(regular, _stage_order(r, "reconcile_preflight", "execute_intent"), "preflight must precede execution")
    _assert(regular, _stage_order(r, "execute_intent", "reconcile"), "post-fill reconcile must follow execution")

    r = closed["report"]
    _assert(closed, closed["exit_code"] == 0, "closed exchange must be a clean no-submit pass")
    _assert(closed, r.get("market_today", {}).get("trading_day") is False, "closed-session branch wrong")
    _assert(closed, r.get("enforce_falsifiers", {}).get("skipped") == "non-trading day", "closed day ran falsifier exits")
    _assert(closed, r.get("enforce_stops", {}).get("skipped") == "non-trading day", "closed day ran stop exits")
    _assert(closed, r.get("author_intents", {}).get("skipped") == "non-trading day", "closed day authored risk")
    _assert(closed, r.get("execute_intent", {}).get("reason") == "non-trading day", "closed day executor reason wrong")
    _assert(closed, "reconcile" in r and "sim_mark" in r, "closed day omitted safe ledger maintenance")

    for case, stage in ((upstream, "classify_regime"), (ledger, "reconcile_preflight")):
        r = case["report"]
        _assert(case, case["exit_code"] == 1, f"{stage} failure must fail the pass")
        blockers = r.get("execute_intent", {}).get("blockers", [])
        _assert(case, r.get("execute_intent", {}).get("skipped") is True, f"{stage} failure did not disarm executor")
        _assert(case, any(str(x).startswith(stage + ":") for x in blockers), f"{stage} missing from execution blockers")
        _assert(case, "reconcile" in r and "pipeline_health" in r, "diagnostics stopped after critical failure")

    r = advisory["report"]
    _assert(advisory, advisory["exit_code"] == 1, "advisory failure must remain visible in pass status")
    _assert(advisory, not r.get("execute_intent", {}).get("skipped"), "advisory-only failure incorrectly blocked execution")
    _assert(advisory, "ml_evidence_track:42" in r.get("pipeline_result", {}).get("failures", []), "advisory failure missing from final report")

    after = (LEDGER.stat().st_size, LEDGER.stat().st_mtime_ns) if LEDGER.exists() else None
    leaked_temporary = sorted(
        str(path)
        for path in set(Path("/tmp").glob("autotrade-internal-paper-check.*.json"))
        - temporary_before
    )
    no_write = before == after
    failures = [
        {"case": case["name"], "failures": case["failures"], "output_tail": case["output_tail"]}
        for case in cases if case["failures"]
    ]
    if not no_write:
        failures.append({"case": "no-write-contract", "failures": ["live ledger metadata changed during replay"]})
    if leaked_temporary:
        failures.append({
            "case": "temporary-artifact-contract",
            "failures": ["scenario replay leaked temporary report(s): " + ",".join(leaked_temporary)],
        })

    report = {
        "ok": not failures,
        "mode": "offline-no-write",
        "cases": [
            {
                "name": case["name"],
                "ok": not case["failures"],
                "exit_code": case["exit_code"],
                "elapsed_seconds": case["elapsed_seconds"],
                "failures": case["failures"],
            }
            for case in cases
        ],
        "ledger_unchanged": no_write,
        "temporary_artifacts_leaked": leaked_temporary,
        "failures": failures,
    }
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
