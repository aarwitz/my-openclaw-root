# Raw Cases (Internal Detailed Source)

Use this folder for high-fidelity internal cases with real company names, tickers, dates, and numbers.
These files are not model-facing validation files. They are source material for building `cases/*.json`.

## Why this exists

- You can author realistic cases quickly without over-anonymizing on first draft.
- A converter script can generate model-facing masked cases in one step.
- You retain a detailed audit trail while still protecting evaluation quality.

## Contract

Raw files should follow `raw_case.schema.json` at the parent folder.

Minimal required fields:
- `id`
- `case_class`
- `fake_date_variant`
- `raw_world_change`
- `sector_or_theme`
- `structural_features`
- `primary_source_class`
- `model_decision_json`
- `resolved_outcome_json`
- `created_at`
- `experiment_id`

Optional but recommended:
- `entities` with known names/tickers to scrub
- `masked_world_change_override` when you want manual control of final wording

## Build masked cases

From workspace root:

```bash
python3 workspaces/trading-intel/reference/validation_corpus/build_masked_from_raw.py
```

Then validate:

```bash
python3 workspaces/trading-intel/reference/validation_corpus/validate_corpus.py
```

## Notes

- The converter lowercases model-facing prose to satisfy current leakage heuristics.
- It masks obvious identifiers, but you still do final human review before acceptance.
