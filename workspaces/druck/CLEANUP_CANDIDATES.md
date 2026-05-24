# Cleanup Candidates

Generated: 2026-05-09

## Keep (active authority or active runtime data)
- `PHASE_II_PLAN.md` — authoritative Phase II rules, explicitly referenced by `AGENTS.md` and `druck-research` skill
- `AUTONOMOUS_PAPER_TRADING_POLICY.md` — active paper-trading override, referenced by Phase II plan
- `MONDAY_OPEN_RUNBOOK.md` — active operational runbook for Monday paper loop
- `AGENTS.md`, `IDENTITY.md`, `SOUL.md`, `USER.md`, `MEMORY.md`, `HEARTBEAT.md` — agent behavior/context
- `alpaca_paper_ledger.json` — active local execution log
- `schwab_account_raw.json`, `schwab_positions.json` — active reference snapshots

## Likely stale / redundant
- `trading_bot_phase_ii_development.txt`
  - appears to be a pre-final design brief
  - overlaps heavily with `PHASE_II_PLAN.md`
  - contains historical "what went wrong / what to build" material rather than current operating rules
  - likely safe to archive or delete after quick human review

## Do not delete right now
- `PHASE_II_PLAN.md`
  - not stale
  - currently the core authority for research scoring and classification
  - deleting it would break prompt/skill alignment

## Recommended cleanup action
1. Archive or delete `trading_bot_phase_ii_development.txt`
2. Keep `PHASE_II_PLAN.md`
3. Optionally rename `PHASE_II_PLAN.md` later only if we also update all references in:
   - `AGENTS.md`
   - `druck-research` skill
   - `financialmodeling-prep-api` skill

## Drive cleanup
Not inspected yet in this pass. Before deleting anything in Drive, verify exact file ids and check for duplicates first.