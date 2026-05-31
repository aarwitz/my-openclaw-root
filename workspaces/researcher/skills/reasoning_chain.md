# Researcher Reasoning Chain

Status: active skill. Loaded by `researcher` for every hypothesis creation and every
material evidence update.

- `skill_id`: `researcher.reasoning_chain`
- `version`: `live`
- `experiment_id`: `researcher_reasoning_live`
- Effective: 2026-05-29.
- Authority: subordinate to `workspaces/trading-intel/docs/01_OPERATING_AUTHORITY.md` and
  `docs/02_ARCHITECTURE.md`. If this skill ever conflicts with the canonical docs, the docs win.

## Purpose

This chain is the single reasoning procedure researcher follows to convert a primary
source delta into a falsifiable hypothesis. The chain output is structured and machine-readable
so quant, critic, and archivist can consume it deterministically. Long-form prose belongs in the
journal, not in this chain's output.

## When to invoke

- On any new primary-source delta researcher decides may be material.
- On any material evidence update that could change the thesis state, falsifier state, or horizon.
- Never invoke this chain for routine ingestion that does not alter thesis posture.

## Output contract

The chain emits one JSON object that is attached to the audit and used to update `hypotheses`,
`hypothesis_evidence`, and `falsifier_signals`. All eight fields are required.

```json
{
  "experiment_id": "researcher_reasoning_live",
  "q1_world_change": "...",
  "q2_mechanism_to_cash_flows": "...",
  "q3_who_is_affected": { "winners": [], "losers": [] },
  "q4_market_implied_view": "...",
  "q5_edge_source": "...",
  "q6_horizon_and_path": { "horizon": "weeks|months|quarters", "path": "..." },
  "q7_falsifiers": [ { "name": "...", "trip_condition": "..." } ],
  "q8_disconfirming_evidence_sought": [ "..." ]
}
```

A chain output is invalid if any field is empty, if `q7_falsifiers` has fewer than 2 entries, or
if `q8_disconfirming_evidence_sought` has fewer than 2 entries.

## The chain (8 questions)

### Q1. What concrete world change has occurred?

State the change in one sentence using primary-source language. Cite the source row id from
`hypothesis_evidence`. Forbidden words: "could", "may", "rumor". If the change is not directly
observable in a primary source, stop and do not create a hypothesis.

### Q2. What is the mechanism from this change to corporate cash flows?

Describe the causal path in 1–3 steps: world change → operational impact → revenue/cost/margin
impact → cash flow impact. Each step must be physically or contractually plausible. If you cannot
state the mechanism without speculation, downgrade the hypothesis to `monitoring` only.

### Q3. Who is affected, and how concentrated is the exposure?

List winners and losers as structured entities (company, sector, ETF, sovereign). For each, note
the approximate exposure share (qualitative bucket: dominant, material, marginal). Mark any name
where exposure share is not estimable; that uncertainty is itself an input to quant sizing.

### Q4. What does the current market price already imply?

In one sentence, state the implied prior consensus view the price action assumes. The hypothesis
only has edge if the proposed view differs from the implied consensus. If the price already
reflects the change, mark the hypothesis as `no_edge` and stop.

### Q5. What is the source of the edge?

Pick one or more: `information_asymmetry` (we read the primary source first),
`processing_asymmetry` (we connect two public facts), `time_asymmetry` (slow diffusion through
analyst coverage), or `behavioral_asymmetry` (known overreaction/underreaction pattern). If you
cannot name the asymmetry, there is no edge; stop.

### Q6. What is the horizon, and what does the path look like?

Pick a horizon bucket. Describe the expected trajectory milestones (e.g., next earnings, next
regulatory filing, next macro print, next trial readout). Path milestones become check-in dates
the archivist will grade.

### Q7. What are the falsifiers?

List at least two specific, observable conditions that would refute the thesis. Each falsifier
must be a `trip_condition` evaluable from primary sources (e.g., "BAMLH0A0HYM2 closes above 550
bps for 3 consecutive sessions" or "next 10-Q segment revenue declines QoQ"). Vague falsifiers
are rejected by critic.

### Q8. What disconfirming evidence will you actively seek?

List at least two concrete sources or queries researcher will check on each refresh to look for
disconfirming evidence (e.g., "scan SEC EDGAR for 8-K filings from competitor X", "check FERC
filings for capacity additions in region Y"). This is the anti-confirmation-bias hook.

## Coupling to other agents

- `quant` consumes Q3, Q5, Q6 for scoring and ranking; Q6 horizon sets default tranche pacing.
- `critic` enforces structure: validates Q1 primary-source citation, demands ≥2 falsifiers in Q7,
  ≥2 disconfirming-evidence sources in Q8, and rejects any field that reads as boilerplate.
- `archivist` uses Q2 mechanism + Q7 falsifiers + Q6 milestones to grade outcomes and to extract
  patterns. A postmortem must reference the `experiment_id` of the chain run used.

## Change control

- Any structural change to question count, ordering, or output schema requires a
  `DECISION_LOG.md` entry.
- Keep this file as the single live chain definition; do not fork parallel chain files.
- The chain output JSON always carries `experiment_id`. Downstream rows inherit it via
  `audits.experiment_id` and `hypotheses.experiment_id`.
- Refresh `experiment_id` when a structural change is approved so outcomes remain attributable.
