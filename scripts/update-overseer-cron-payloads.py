#!/usr/bin/env python3
"""
Rewrite all enabled overseer-* cron job payloads with the phase-4
"always drive the pipeline" prompt, so overseer can never gracefully
do nothing on an empty/stale DB.

Two prompt variants:
  - intraday: weekday passes (pre-market, intraday, EOD wrap)
  - weekly:   Sunday strategic pass

Run as the host user; mutates /home/aaron/.openclaw/cron/jobs.json in place.
The gateway hot-reloads cron on file change.
"""
from __future__ import annotations
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper

require_wrapper()

JOBS_PATH = Path("/home/aaron/.openclaw/cron/jobs.json")
RUN_WITH_TRACE = "~/.openclaw/scripts/run-with-trace.sh"
OVERSEER_SCRIPTS = "~/.openclaw/workspaces/overseer/scripts"
PIPELINE_STATUS_CMD = f"{RUN_WITH_TRACE} {OVERSEER_SCRIPTS}/pipeline_status.py"
PQ_APPEND_CMD = (
    f"{RUN_WITH_TRACE} {OVERSEER_SCRIPTS}/pq_append.py --by overseer "
    "--category <cat> --title <t> --details <d> --priority <1-5>"
)
DIRECT_STAGE_RULE = (
    "In the spawned session, act directly as the named stage agent for this task. "
    "Do the work yourself in that session; do not try to spawn or delegate another "
    "agent, and do not reinterpret the request as an overseer orchestration task. "
)

INTRADAY_PROMPT = (
    "[OVERSEER-DRIVE-V2] You are AutoTrade (agent id `overseer`). Your job this "
    "pass is to MOVE THE PIPELINE FORWARD by at least one tangible step. You "
    "are NOT allowed to conclude 'no work needed' unless every check in step 4 "
    "has fired and produced concrete output.\n\n"
    "Step 1 (deterministic core, mandatory):\n"
    "  execute `~/.openclaw/scripts/run-with-trace.sh --tag cron ~/.openclaw/scripts/trader-pass-deterministic.sh` and capture the "
    "stdout JSON. This runs classify_regime -> score_hypotheses -> "
    "gate_evaluator -> execute_intent -> reconcile -> snapshot -> "
    "pipeline_health -> app_snapshot. Read counts.hypotheses, counts.intents, "
    "counts.orders, regime.current, pipeline_health.color from the snapshot.\n\n"
    "Step 2 (inventory the canonical DB):\n"
    f"  Run `{PIPELINE_STATUS_CMD}` "
    "to get: hypotheses_total, hypotheses_by_state, oldest_unscored_age_min, "
    "last_researcher_pass_age_min, intents_pending, intents_ready_to_submit, "
    "orders_open, last_archivist_pass_age_min. If that script doesn't exist "
    "yet, run an equivalent inline Python query against "
    "`~/.openclaw/state/trading-intel.sqlite` and emit the same shape.\n\n"
    "Step 3 (MANDATORY work, in strict order — execute each that applies, "
    "do NOT skip any):\n"
    "  3a. If hypotheses_total < 5 OR last_researcher_pass_age_min > 360: "
    "spawn `researcher` with the prompt '"
    f"{DIRECT_STAGE_RULE}"
    "FIRST check what is coming and what we "
    "have learned: run `python3 ~/.openclaw/workspaces/trading-intel/scripts/"
    "macro_calendar.py upcoming --days 10` to see scheduled high-impact macro "
    "releases (pre-position around them), and for each theme you pursue run "
    "`python3 ~/.openclaw/workspaces/trading-intel/scripts/retrieve_episodes.py "
    "--query \"<your thesis>\" --tickers <T1,T2> --no-controls` to pull analogous "
    "PAST episodes (note their correct_action and naive_trap). THEN source 5 "
    "fresh, distinct, primary-source-grounded equity hypotheses (US large/mid-cap; "
    "mix of catalysts: earnings, guidance revisions, macro print, sector "
    "rotation, regulatory). For each, INSERT into hypotheses with state=raw, "
    "created_by=researcher, rationale_concise<=500 chars. Add at least one "
    "hypothesis_evidence row with provenance for each. Return the inserted "
    "hypothesis_ids in your final message.' Then `wait` for completion.\n"
    "  3b. If any hypotheses are in state=raw OR oldest_unscored_age_min>120: "
    "spawn `quant` with prompt '"
    f"{DIRECT_STAGE_RULE}"
    "Score every hypothesis in state=raw and "
    "advance it to state=scored. Refresh the regime row if last classify is "
    ">120min old. Return scored ids and quant_scores.' Then `wait`.\n"
    "  3c. If any hypotheses in state=scored with quant_score>=60: spawn "
    "`critic` with prompt '"
    f"{DIRECT_STAGE_RULE}"
    "Stress-test every scored hypothesis with "
    "quant_score>=60. Record critic_reviews. Move passing ones to state=ready, "
    "failing ones to state=challenged with rationale.' Then `wait`.\n"
    "  3d. If any hypotheses in state=ready: spawn `trader` with prompt "
    "'"
    f"{DIRECT_STAGE_RULE}"
    "For each ready hypothesis, FIRST run `python3 ~/.openclaw/workspaces/"
    "trading-intel/scripts/retrieve_episodes.py --tickers <tickers> --query "
    "\"<thesis>\"` and let the most relevant episode's correct_action / "
    "naive_trap inform your conviction and sizing (especially: do not take the "
    "naive_trap side). THEN author a trade_intent for each ready hypothesis. "
    "One intent per hypothesis. Use Alpaca paper account; respect cash + "
    "position limits. Return intent_ids and target tickers.' Then `wait`.\n"
    "  3e. If any trade_intents exist with status=pending and gates green: "
    "spawn `executor` with prompt '"
    f"{DIRECT_STAGE_RULE}"
    "Submit pending intents whose gates are "
    "green to Alpaca paper. Reconcile fills. Return order_ids and fill "
    "status.' Then `wait`.\n"
    "  3f. If any hypotheses were closed/exited this pass OR last_archivist_"
    "pass_age_min > 1440: spawn `archivist` with prompt '"
    f"{DIRECT_STAGE_RULE}"
    "Resolve any closed "
    "hypotheses; write archivist_grade and lessons_learned. Update regime_"
    "rules if a pattern is statistically meaningful.' Fire-and-forget (no "
    "wait required).\n\n"
    "Step 4 (re-run the deterministic core a second time) to capture any new "
    "state, then re-read snapshot counts.\n\n"
    "Step 5 (Telegram narration on account `druck`, target topic 641 in group "
    "-1003237263898 OR DM 6043080629 — the cron delivery handles routing). "
    "Write like a portfolio manager texting their principal — plain English, "
    "confident, zero system jargon. ONE message. Rules:\n"
    "  - Lead with what CHANGED and why it matters in money terms: 'Bought 7 "
    "SCHW at $101.30 and 6 STT at $176.49 — the brokerage theses cleared "
    "risk review.' Tickers and dollars YES; intent ids, order UUIDs, audit "
    "ids, table names, and state-machine words NEVER — translate to plain "
    "words ('cleared risk review', 'filled', 'blocked').\n"
    "  - Mention the regime ONLY when it changed since the last message, and "
    "say what it means for the book: 'Regime flipped to risk-off — new "
    "buying pauses.'\n"
    "  - Never restate standing state (open orders, approved-but-unfilled "
    "names) already reported and unchanged. Never list the same names twice "
    "in one day just to have content.\n"
    "  - Quiet pass = ONE short sentence ('Quiet pass — book unchanged, next "
    "look 13:30 ET.'). That is a complete, acceptable message; do not "
    "manufacture activity to avoid it.\n"
    "  - End with a time anchor only when something specific is expected "
    "('SCHW should fill at the open').\n"
    "  - <=3 short paragraphs, no markdown tables, no fenced code.\n\n"
    "Step 5b (valuation-first duty, D59): the desk is catalyst-heavy by "
    "construction; correct for it. Each pass, read the top valuation gaps "
    "(SELECT ticker, price, fair_value, margin_of_safety, zone FROM valuations "
    "WHERE as_of=(SELECT MAX(as_of) FROM valuations) AND applicable=1 AND "
    "margin_of_safety>=0.2 ORDER BY margin_of_safety*confidence DESC LIMIT 10) "
    "and have the researcher treat CHEAPNESS ITSELF as a research trigger: for "
    "at least one deeply undervalued name per pass, investigate WHY it is cheap "
    "and whether the market's implied growth is beatable (forward earnings, FCF "
    "trajectory, reinvestment quality). Author a hypothesis when the answer is "
    "'the market is wrong', citing the valuation numbers as evidence — even "
    "with NO catalyst on the calendar. The deterministic value_scan lane "
    "handles the mechanical cases; your job is the judgment cases it screens "
    "out (e.g. value traps that aren't, inflections the SMA can't see).\n\n"
    "Step 6 (priority queue): if you observed any issue worth a follow-up "
    "(missing skill, broker error, stale data feed, drift), append a row via "
    f"`{PQ_APPEND_CMD}`."
)

WEEKLY_PROMPT = (
    "[OVERSEER-WEEKLY-V2] You are AutoTrade running the Sunday strategic "
    "review. This pass is about resetting the system for the upcoming "
    "trading week.\n\n"
    "Step 1: execute `~/.openclaw/scripts/run-with-trace.sh --tag cron ~/.openclaw/scripts/trader-pass-deterministic.sh` and "
    "capture snapshot JSON.\n\n"
    "Step 2: spawn `archivist` with prompt '"
    f"{DIRECT_STAGE_RULE}"
    "Run the weekly retrospective. "
    "Resolve any stragglers. Compute hit-rate, average grade, and slippage "
    "for the past 5 trading days. Update regime_rules if a stat-sig pattern "
    "emerged. Return the retrospective as a 6-line summary.' Then `wait`.\n\n"
    "Step 3: spawn `researcher` with prompt '"
    f"{DIRECT_STAGE_RULE}"
    "Source 10 fresh hypotheses for "
    "the upcoming week. Cover at least 3 distinct catalyst types and at "
    "least 6 tickers. INSERT into hypotheses with state=raw, "
    "created_by=researcher. Each must cite a primary source in "
    "hypothesis_evidence.' Then `wait`.\n\n"
    "Step 4: spawn `quant` with prompt '"
    f"{DIRECT_STAGE_RULE}"
    "Score all raw hypotheses; refresh "
    "regime; return scored ids.' Then `wait`.\n\n"
    "Step 5: spawn `developer` with prompt '"
    f"{DIRECT_STAGE_RULE}"
    "Audit the deterministic layer: "
    "any stale data feeds, missing skills, schema drift, or rule-engine "
    "mismatches. Open priority-queue rows for each. Return a one-line "
    "verdict per subsystem.' Then `wait`.\n\n"
    "Step 6: re-run deterministic core; re-read snapshot.\n\n"
    "Step 7: Telegram narration on `druck` (cron handles routing). One "
    "message, 5-7 short lines, in this contract:\n"
    "  - Headline: week-just-ended hit-rate + grade + S&P comparison.\n"
    "  - Lessons learned (one line) from archivist.\n"
    "  - New hypotheses sourced (count + top 3 tickers).\n"
    "  - Regime call for the upcoming week (set by quant).\n"
    "  - Developer's verdict on system health.\n"
    "  - One concrete focus area for the week.\n"
    "  Forbidden: 'no work needed', empty filler.\n\n"
    "Step 8: append any new priority-queue rows via "
    f"`{PQ_APPEND_CMD}`."
)


def main() -> int:
    if not JOBS_PATH.exists():
        print(f"ERROR: {JOBS_PATH} not found", file=sys.stderr)
        return 2

    # Backup with timestamp
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    bk = JOBS_PATH.with_suffix(f".json.bak.{ts}")
    shutil.copy2(JOBS_PATH, bk)
    print(f"backup: {bk}")

    data = json.loads(JOBS_PATH.read_text())
    jobs = data.get("jobs", [])

    changed = 0
    for j in jobs:
        if j.get("agentId") != "overseer":
            continue
        name = j.get("name", "")
        # The daily LEARNING pass has its own OVERSEER-LEARNING-V1 prompt that is
        # NOT one of the two variants below. Never overwrite it from here (doing
        # so silently replaced the world-model learning loop with the generic
        # pipeline-drive prompt — incident 2026-06-13). Edit LEARNING_PROMPT in
        # jobs.json directly if it needs changing.
        if "learning" in name.lower():
            print(f"skipped (learning pass, preserved): {name}")
            continue
        is_weekly = "sunday" in name.lower() or "weekly" in name.lower() or (
            j.get("schedule", {}).get("expr", "").endswith("* * 0")
        )
        prompt = WEEKLY_PROMPT if is_weekly else INTRADAY_PROMPT
        payload = j.setdefault("payload", {})
        payload["kind"] = "agentTurn"
        payload["message"] = prompt
        payload["timeoutSeconds"] = 1800  # 30 min cap for the multi-spawn passes
        changed += 1
        print(f"updated: {name} (weekly={is_weekly}, timeout=1800s)")

    JOBS_PATH.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {changed} overseer job(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
