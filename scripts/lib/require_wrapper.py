#!/usr/bin/env python3
"""Enforce wrapper-only execution for Python operational scripts."""

from __future__ import annotations

import os
import sys


def require_wrapper() -> None:
    if os.environ.get("OPENCLAW_RUN_WITH_TRACE") != "1":
        print("This script must be run via ~/.openclaw/scripts/run-with-trace.sh", file=sys.stderr)
        raise SystemExit(126)
