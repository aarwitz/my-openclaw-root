# Validation Corpus

Status: active reference. The directory is the canonical staging area for cases that load into
`validation_cases` via the validation runner.

Authority: `docs/05_IMPLEMENTATION_POLICY.md` §7 and `sql/schema.sql` `validation_cases`.

## Layout

- `raw_cases/<case_id>.json` — internal detailed source cases with real names/numbers for fast authoring.
- `cases/<case_id>.json` — one case per file, matches the JSON contract below.
- `seeds/` — high-quality exemplar cases shipped as templates for hand-building the rest.
- `index.json` — generated manifest of all cases (id, class, fake_date_variant, passed, source_file).

Build path:

- `build_masked_from_raw.py` converts `raw_cases/*.json` into model-facing `cases/*.json`.
- `validate_corpus.py` validates shape/counts and emits `index.json`.

## JSON contract (one file per case)

```json
{
  "id": "vc_<class>_<short_slug>_<NNN>",
  "case_class": "winner | negative_control | post_cutoff",
  "fake_date_variant": null,
  "masked_case_json": {
    "world_change": "Anonymized prose. No ticker, no company name, no deal name, no date.",
    "sector_or_theme": "energy_midstream | small_biotech | regional_bank | ...",
    "structural_features": ["..."],
    "primary_source_class": "8-K | trial_update | macro_release | ..."
  },
  "model_decision_json": {
    "decision": "open | no_trade | block",
    "direction": "long | short | none",
    "confidence_bucket": "low | medium | high",
    "rationale_hash": "sha256:..."
  },
  "resolved_outcome_json": {
    "outcome": "thesis_confirmed | thesis_refuted | inconclusive",
    "horizon_days": 30,
    "external_mechanism_check": "what observable event resolved it"
  },
  "passed": 0,
  "created_at": "2026-05-29T00:00:00Z",
  "experiment_id": "validation_corpus_seed"
}
```

`passed = 1` iff the model decision matches the resolved outcome under the anonymized
representation AND, if a `fake_date_variant` exists for the same underlying case, the variant
does not flip the decision.

## Build targets (gating Phase 1)

Per `docs/05_IMPLEMENTATION_POLICY.md` §7 default thresholds:

- ≥ 30 `post_cutoff` cases with resolved outcomes.
- ≥ 60 `negative_control` cases (cases that should produce `no_trade` or `block`).
- `winner` cases as the in-distribution sanity baseline (no minimum, but build a few).
- For each substantive case, ship at least one `fake_date_variant` to test date sensitivity.

These cases must be built by hand. Do not auto-generate them; the value is in the careful
anonymization and the external mechanism check.

## Anonymization rules

- Strip: ticker, company name, deal name, executive name, country (when identifying), exact date,
  exact dollar amounts that uniquely identify a deal.
- Keep: sector, structural pattern, primary-source class, qualitative magnitude, qualitative timing
  (e.g., "during a Fed hiking cycle"), causal mechanism.
- The `rationale_hash` field exists so the model decision is committed before resolution is
  recorded; do not record the rationale in plaintext alongside the outcome to prevent post-hoc
  rationalization during corpus review.

## Runner contract

A validation runner (to be implemented in Phase 1) reads every `cases/*.json`, validates the JSON
shape, inserts one row per case into `validation_cases`, and writes a single batch row into
`audits` with `actor = 'system'`, `action = 'validation_batch'`, and `experiment_id` set from the
batch.

## Seed contents shipped now

See `seeds/` for one high-fidelity exemplar per class plus one paired `fake_date_variant`. These
seeds are the format-of-record reference; do not modify them when building real cases — add new
files under `cases/` instead.
