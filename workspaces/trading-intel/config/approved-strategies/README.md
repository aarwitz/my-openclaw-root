# Approved strategy manifests

This directory is the only source-controlled crossing point from offline
research into the internal paper-trading mechanism ledger.

An approval manifest is valid only when `promotion_gate.py` verifies that it:

- is committed unchanged at `HEAD`;
- identifies an operator approval and `DECISION_LOG.md` D-number;
- has not expired;
- binds the exact candidate-set digest;
- binds the exact digest of a completed artifact under
  `state/research-artifacts/`;
- references a minimum-duration locked forward-shadow result, never a
  development or historical-replay result.

There is intentionally no manifest while the canonical verdict is `NO_EDGE`.
Do not add placeholders, examples, drafts, or agent-authored approvals here.
