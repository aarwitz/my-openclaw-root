#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()

"""Enforce one-message Telegram narration contracts on enabled agent crons.

The useful Telegram message is sent explicitly by the job. Cron delivery must
therefore remain ``none`` and the agent's final response must be silent; without
both controls, operators receive a second "pass completed / note sent" receipt.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(os.path.expanduser("~/.openclaw"))
JOBS_PATH = ROOT / "cron/jobs.json"
SENTINEL = "FINAL OUTPUT CONTRACT (no duplicate receipts):"
CONTRACT = (
    "\n\nFINAL OUTPUT CONTRACT (no duplicate receipts): If this job sent its one "
    "useful Telegram message, return exactly SILENT_SUCCESS. If the pass required "
    "no Telegram message, also return exactly SILENT_SUCCESS. Never send or narrate "
    "a second completion receipt such as 'pass completed', 'note sent', or "
    "'Telegram narration was sent'. Cron delivery must remain mode=none."
)
DRIVE_MARKER = "[OVERSEER-DRIVE-V2]"
STAGE_START = "Step 3 (MANDATORY work, in strict order — execute each that applies, do NOT skip any):"
STAGE_END = "\n\nStep 4:"
VALUE_START = "Step 5b (valuation-first duty, D59):"
VALUE_END = "\n\nStep 6 (priority queue):"
CATALYST_JOB = "catalyst-research-0830-et"
LEGACY_TRADING_DESK_TARGET = "target topic 641 in group -1003237263898 OR DM 6043080629"
CANONICAL_TRADING_DESK_TARGET = "target topic 641 in group -1003846579956"


def _replace_section(message: str, start_token: str, end_token: str, replacement: str) -> str:
    start = message.find(start_token)
    if start < 0:
        return message
    end = message.find(end_token, start)
    if end < 0:
        raise ValueError(f"prompt section starting {start_token!r} has no {end_token!r}")
    return message[:start] + replacement.rstrip() + message[end:]


def _normalize_drive_prompt(name: str, message: str) -> str:
    if DRIVE_MARKER not in message:
        return message
    message = message.replace(
        "[OVERSEER-DRIVE-V2] You are AutoTrade (agent id `overseer`). Your job this pass is to MOVE THE PIPELINE FORWARD by at least one tangible step. You are NOT allowed to conclude 'no work needed' unless every check in step 4 has fired and produced concrete output.",
        "[OVERSEER-DRIVE-V3] You are AutoTrade (agent id `overseer`) running one truthful desk checkpoint. A quiet pass is valid. Never manufacture research, proposals, or trades merely to create activity.",
    )
    if name == CATALYST_JOB:
        stage = """Step 3 (deduplicated daily research, the only routine research spawn):
  Spawn `researcher` exactly once. Before proposing a name, query every existing hypothesis for that ticker. States raw/scored/challenged/ready/active are the one canonical LIVE thesis. If one exists, UPDATE that thesis and append genuinely new primary-source evidence; do not INSERT another hypothesis. Dormant/resolved/retired rows are history and may inform the work. Author at most 5 net-new, previously unrepresented tickers, and fewer (including zero) is correct when evidence does not clear the bar. Read the macro calendar, rotation snapshot, valuation gaps, and relevant past episodes. Every new row needs primary-source provenance, a falsifier, and a clear horizon. The database rejects same-ticker live duplicates; never work around that guard. Wait for completion."""
        value = """Step 5b (valuation research):
  This dedicated research pass may investigate one valuation gap, but cheapness is a question, not evidence. Reconcile share count, per-share units, and primary filings before updating or authoring; do nothing when the valuation inputs are suspect."""
    else:
        stage = """Step 3 (routine-pass boundary):
  Do not spawn researcher, quant, critic, trader, executor, or archivist from this routine pass. The deterministic core owns the normal score→review→predict→risk→execute→reconcile chain. Dedicated research and post-close learning jobs own their lanes. A quiet result is complete."""
        value = """Step 5b (valuation boundary):
  Do not originate valuation theses from a routine pass. The deterministic value scan and the dedicated daily research job own that work."""
    message = _replace_section(message, STAGE_START, STAGE_END, stage)
    message = _replace_section(message, VALUE_START, VALUE_END, value)
    return message


def normalize(doc: dict) -> list[str]:
    changed: list[str] = []
    # This free-form proposer was a p-hacking lane and its V2 prompt also
    # contains the retired mandatory-activity contract. Remove the job instead
    # of leaving a dormant regression switch in the live configuration.
    jobs = doc.get("jobs", [])
    if any(str(job.get("name") or "") == "mechanism-proposer-daily" for job in jobs):
        doc["jobs"] = [
            job for job in jobs
            if str(job.get("name") or "") != "mechanism-proposer-daily"
        ]
        changed.append("mechanism-proposer-daily")
    for job in doc.get("jobs", []):
        payload = job.get("payload") or {}
        message = str(payload.get("message") or "")
        name = str(job.get("name") or job.get("id") or "")
        if not job.get("enabled") or "telegram" not in message.lower():
            continue
        delivery = job.get("delivery") or {}
        if delivery.get("mode") != "none":
            raise ValueError(
                f"{job.get('name')}: Telegram-authoring job must use delivery.mode=none"
            )
        replacement = _normalize_drive_prompt(name, message).replace(
            " — the cron delivery handles routing",
            " — send it explicitly once via the messaging tool",
        )
        replacement = replacement.replace(
            LEGACY_TRADING_DESK_TARGET,
            CANONICAL_TRADING_DESK_TARGET,
        )
        if SENTINEL not in replacement:
            replacement = replacement.rstrip() + CONTRACT
        if replacement != message:
            payload["message"] = replacement
            changed.append(name)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    doc = json.loads(JOBS_PATH.read_text())
    changed = normalize(doc)
    if changed and args.apply:
        fd, tmp_name = tempfile.mkstemp(
            prefix="jobs.", suffix=".json", dir=str(JOBS_PATH.parent)
        )
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(doc, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, JOBS_PATH.stat().st_mode)
            os.replace(tmp_name, JOBS_PATH)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
    print(json.dumps({"changed": changed, "applied": bool(changed and args.apply)}))
    return 1 if changed and not args.apply else 0


if __name__ == "__main__":
    raise SystemExit(main())
