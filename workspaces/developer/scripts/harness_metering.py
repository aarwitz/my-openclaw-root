#!/usr/bin/env python3
"""Harness metering — aggregate Codex token usage per agent from the rollouts.

The desk runs on a flat-rate Codex/ChatGPT OAuth subscription (no API keys, no
per-token billing), so there was no reason to meter *dollars* — but there was also
NO visibility into LLM usage at all: which agents are token-heavy, how much context
each pass burns, or how close the desk is to the Codex rate limits. The data exists
(every assistant turn in `agents/<id>/sessions/*.trajectory.jsonl` carries a
`usage` object: {input, output, cacheRead, total}); it was just never aggregated.

This walks the rollout trajectories modified within a lookback window and rolls
usage up per agent + total: turns, input, output, cacheRead, and peak single-turn
context. Read-only; pure stdlib. Run ad hoc or as an observability stage.

    python3 harness_metering.py [--days 7] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

AGENTS_DIR = Path(os.path.expanduser("~/.openclaw/agents"))


def _iter_usage(path: Path):
    """Yield every usage dict found in a trajectory jsonl (one per assistant turn)."""
    try:
        for line in path.read_text(errors="replace").splitlines():
            if '"usage"' not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            stack = [obj]
            while stack:
                cur = stack.pop()
                if isinstance(cur, dict):
                    u = cur.get("usage")
                    if isinstance(u, dict) and ("total" in u or "input" in u):
                        yield u
                    stack.extend(cur.values())
                elif isinstance(cur, list):
                    stack.extend(cur)
    except OSError:
        return


def collect(days: float) -> dict:
    cutoff = time.time() - days * 86400
    per_agent: dict[str, dict] = {}
    for agent_dir in sorted(AGENTS_DIR.glob("*")):
        sess = agent_dir / "sessions"
        if not sess.is_dir():
            continue
        agg = {"turns": 0, "input": 0, "output": 0, "cacheRead": 0, "total": 0,
               "peak_context": 0, "trajectories": 0}
        for traj in sess.glob("*.trajectory.jsonl"):
            if traj.stat().st_mtime < cutoff:
                continue
            agg["trajectories"] += 1
            for u in _iter_usage(traj):
                agg["turns"] += 1
                agg["input"] += int(u.get("input", 0) or 0)
                agg["output"] += int(u.get("output", 0) or 0)
                agg["cacheRead"] += int(u.get("cacheRead", 0) or 0)
                agg["total"] += int(u.get("total", 0) or 0)
                ctx = int(u.get("input", 0) or 0) + int(u.get("cacheRead", 0) or 0)
                agg["peak_context"] = max(agg["peak_context"], ctx)
        if agg["turns"]:
            per_agent[agent_dir.name] = agg
    grand = {k: sum(a[k] for a in per_agent.values())
             for k in ("turns", "input", "output", "cacheRead", "total")}
    grand["agents"] = len(per_agent)
    return {"window_days": days, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "grand_total": grand, "per_agent": per_agent}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=7.0, help="lookback window (trajectory mtime)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()
    rep = collect(args.days)

    if args.json:
        print(json.dumps(rep, indent=2))
        return 0

    g = rep["grand_total"]
    print(f"Harness token metering · last {args.days:g}d · {rep['generated_at']}")
    print(f"  agents active: {g['agents']}   turns: {g['turns']:,}")
    print(f"  generation (input+output): {g['input'] + g['output']:,}   "
          f"cacheRead: {g['cacheRead']:,}   total processed: {g['total']:,}")
    print()
    print(f"  {'agent':12} {'turns':>6} {'input':>10} {'output':>10} {'cacheRead':>12} {'peakCtx':>9}")
    for name, a in sorted(rep["per_agent"].items(), key=lambda kv: -kv[1]["total"]):
        print(f"  {name:12} {a['turns']:>6} {a['input']:>10,} {a['output']:>10,} "
              f"{a['cacheRead']:>12,} {a['peak_context']:>9,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
