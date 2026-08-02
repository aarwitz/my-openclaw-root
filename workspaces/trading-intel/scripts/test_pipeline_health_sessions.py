#!/usr/bin/env python3
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")

from integrity_check import completed_sessions_since


class PipelineHealthSessionFreshnessTests(unittest.TestCase):
    def test_friday_artifact_is_fresh_sunday_and_monday_before_close(self) -> None:
        friday = "2026-07-31T20:00:00Z"
        sunday = datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc)
        monday_preclose = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
        monday_postclose = datetime(2026, 8, 3, 21, 0, tzinfo=timezone.utc)
        self.assertEqual(completed_sessions_since(friday, sunday), 0)
        self.assertEqual(completed_sessions_since(friday, monday_preclose), 0)
        self.assertEqual(completed_sessions_since(friday, monday_postclose), 1)

    def test_pipeline_health_uses_session_freshness_for_runtime_artifacts(self) -> None:
        source = (
            ROOT / "workspaces/developer/scripts/audit_pipeline_health.py"
        ).read_text()
        self.assertIn("completed_sessions_since(rg", source)
        self.assertIn("completed_sessions_since(selection", source)
        self.assertNotIn("REGIME_FRESH_HOURS", source)


if __name__ == "__main__":
    unittest.main()
