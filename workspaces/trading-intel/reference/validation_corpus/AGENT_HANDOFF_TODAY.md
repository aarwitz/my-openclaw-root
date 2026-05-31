# Validation Corpus Handoff — Today

Objective: finish the initial corpus today so Phase 1 validation can start immediately.

Authority:
- workspaces/trading-intel/reference/validation_corpus/README.md
- workspaces/trading-intel/reference/validation_corpus/target_profile_v1.json
- workspaces/trading-intel/reference/validation_corpus/validation_case.schema.json
- workspaces/trading-intel/docs/05_IMPLEMENTATION_POLICY.md section 7

## Locked contract (do not change today)

- JSON shape is fixed by validation_case.schema.json.
- Target profile is fixed by target_profile_v1.json.
- File location is fixed: reference/validation_corpus/cases/*.json.
- Every post_cutoff substantive case must have a fake-date pair.

## Production-oriented target for today

Use today_target from target_profile_v1.json:
- 35 post_cutoff substantive
- 35 post_cutoff fake-date variants
- 70 negative_control
- 10 winner
- Total target files: 150

If time slips, the minimum acceptable go-live floor is:
- 30 post_cutoff substantive
- 30 post_cutoff fake-date variants
- 60 negative_control
- 10 winner

## Agent A (internet collector)

Mission: collect primary-source events from the last ~60 days and deliver source packets.

Prompt to Agent A:
"Collect 180 candidate events from the last 60 days using only primary sources (SEC filings, official regulator releases, trial registries, official macro releases). For each candidate return JSON with: packet_id, source_url, source_type, publication_timestamp_utc, event_summary_2_sentences, mechanism_to_cash_flow_1_sentence, expected_direction (long/short/none), confidence_low_medium_high, and why not consensus in 1 sentence. Exclude paywalled commentary and secondary news rewrites."

Output format from Agent A:
- NDJSON file: one JSON packet per line.
- Save as reference/validation_corpus/tmp/agent_a_packets.ndjson.

## Agent B (case builder)

Mission: convert source packets into masked case JSONs that match the locked contract.

Prompt to Agent B:
"Read agent_a_packets.ndjson and produce validation case JSON files that match validation_case.schema.json exactly. Remove all identifiers (ticker, company, dates, exact unique amounts, named executives). Preserve only structure/mechanism/source class. Assign case_class as winner, negative_control, or post_cutoff. For each post_cutoff substantive case, create a paired fake-date variant with id suffix _fakedate and set fake_date_variant. Write output files to reference/validation_corpus/cases/."

Hard instructions to Agent B:
- Keep ids unique and schema-compliant.
- Use rationale_hash placeholder beginning with sha256:.
- Do not invent non-resolvable outcomes.
- Do not overwrite existing files.

## Human reviewer (you)

Your 3 fast checks per case (reject if any fail):
1. Leakage check: can you infer ticker/company/date quickly from masked_case_json.world_change?
2. Resolution check: does external_mechanism_check point to an observable outcome?
3. Class check: does case_class make sense and does fake-date pairing exist for post_cutoff?

Use the profiler before final acceptance:

```bash
python3 workspaces/trading-intel/reference/validation_corpus/assess_case_quality.py
```

Accept only cases that are both legible and specific. A good case should read like a real event with the labels removed, not like abstract model text.

## Runbook (exact)

1. Ensure cases folder exists.
2. Run Agent A and save packets.
3. Run Agent B to generate case JSON files.
4. Run validator in minimum mode:
   python3 workspaces/trading-intel/reference/validation_corpus/validate_corpus.py
5. Fix all errors.
6. Run strict mode for today target:
   python3 workspaces/trading-intel/reference/validation_corpus/validate_corpus.py --strict-target
7. Confirm index output exists:
   workspaces/trading-intel/reference/validation_corpus/index.json

## Done definition for today

Done only when all are true:
- Validator passes in strict mode.
- index.json is written with zero errors.
- Case counts meet today_target.
- No leakage flags from your human review.

## Manual intervention required

Required today:
- You must perform final leakage and plausibility review before counting any case as accepted.
- You must decide pass=0/1 for ambiguous edge cases where model decision/outcome alignment is not obvious.

Not required today:
- No schema editing.
- No policy edits.
- No DB migration changes.
