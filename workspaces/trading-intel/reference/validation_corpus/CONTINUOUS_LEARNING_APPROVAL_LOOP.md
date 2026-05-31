# Continuous Learning + Case Approval Loop

Goal: let Druck generate thesis/outcome data continuously, then promote high-quality items into validation cases with your approval.

## Two lanes

- Operations lane (specific, real names/numbers): used by Druck for thesis and execution decisions.
- Evaluation lane (masked): used for validation corpus and robustness checks.

## Daily loop (10-15 minutes)

1. Export learning queue from resolved hypotheses:

```bash
python3 workspaces/trading-intel/reference/validation_corpus/export_learning_queue.py
```

2. Review queue file:

- `workspaces/trading-intel/reference/validation_corpus/tmp/learning_queue.jsonl`

3. For each good item, create a raw detailed case in:

- `workspaces/trading-intel/reference/validation_corpus/raw_cases/`

4. Build masked evaluation cases:

```bash
python3 workspaces/trading-intel/reference/validation_corpus/build_masked_from_raw.py
```

5. Validate corpus:

```bash
python3 workspaces/trading-intel/reference/validation_corpus/validate_corpus.py
```

## Approval rule

Promote only if all are true:

- Mechanism is concrete and externally checkable.
- Outcome horizon is explicit.
- Decision rationale is not hindsight-only.
- Fake-date companion exists for post_cutoff substantive cases.

## Suggested weekly target

- 5 approved post_cutoff substantive cases
- 5 matching fake-date variants
- 10 approved negative_control cases
- 2 approved winner cases

At this pace, corpus grows quickly without burnout while staying high quality.
