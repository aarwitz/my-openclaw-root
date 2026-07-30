#!/usr/bin/env python3
"""Enforce one-message Telegram narration contracts on enabled agent crons.

The useful Telegram message is sent explicitly by the job. Cron delivery must
therefore remain ``none`` and the agent's final response must be silent; without
both controls, operators receive a second "pass completed / note sent" receipt.
"""

from __future__ import annotations

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


def normalize(doc: dict) -> list[str]:
    changed: list[str] = []
    for job in doc.get("jobs", []):
        payload = job.get("payload") or {}
        message = str(payload.get("message") or "")
        if not job.get("enabled") or "telegram" not in message.lower():
            continue
        delivery = job.get("delivery") or {}
        if delivery.get("mode") != "none":
            raise ValueError(
                f"{job.get('name')}: Telegram-authoring job must use delivery.mode=none"
            )
        replacement = message.replace(
            " — the cron delivery handles routing",
            " — send it explicitly once via the messaging tool",
        )
        if SENTINEL not in replacement:
            replacement = replacement.rstrip() + CONTRACT
        if replacement != message:
            payload["message"] = replacement
            changed.append(str(job.get("name") or job.get("id")))
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
