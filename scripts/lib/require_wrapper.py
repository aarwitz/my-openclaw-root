#!/usr/bin/env python3
"""Enforce wrapper-only execution for Python operational scripts."""

from __future__ import annotations

import os
from pathlib import Path
import sys


def require_wrapper() -> None:
    if os.environ.get("OPENCLAW_RUN_WITH_TRACE") != "1":
        if os.environ.get("OPENCLAW_REQUIRE_WRAPPER_NO_AUTORUN") != "1":
            runner = Path("/home/aaron/.openclaw/scripts/run-with-trace.sh")
            script = Path(sys.argv[0]).resolve()
            if runner.exists() and str(script) != str(runner):
                os.execv(str(runner), [str(runner), "--tag", "auto", str(script), *sys.argv[1:]])
        print("This script must be run via ~/.openclaw/scripts/run-with-trace.sh", file=sys.stderr)
        raise SystemExit(126)
