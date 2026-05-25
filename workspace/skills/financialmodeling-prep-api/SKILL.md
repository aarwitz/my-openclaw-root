---
name: financialmodeling-prep-api
aliases: ["fmp"]
description: Analyst revisions, ratings breadth, price-target context, and estimate context for catalyst research support.
metadata:
  emoji: "📊"
  os: "linux"
  requires: "credentials.financialmodeling-prep-api.json (email, password, api key)"
  primaryEnv: "production"
  authType: "api-key"
  rateLimitPerMinute: 100
---

# FMP Router (Lean + Deterministic)

Use this as a deterministic analyst-sentiment support layer.
Do not treat FMP as sole catalyst proof.

## Role in the stack

Primary:
- ratings breadth / consensus context
- price-target range and revision activity context

Secondary:
- estimate trend context when plan access allows

Not primary for:
- catalyst truth verification
- options implied move
- execution or portfolio state

## Deterministic Endpoint Order

For ticker `T`:
1. `grades-consensus`
2. `grades-historical`
3. `price-target-consensus`
4. `price-target-summary`
5. Optional: analyst/financial estimates if available under plan

If endpoint is gated/empty, mark unavailable and fail closed.

## Error Discipline

- `401`: credential/key issue
- plan-gated response: unavailable under subscription
- `429`: back off and retry later
- empty array: treat as unavailable, not bullish

## Integration Rule

Use FMP as supporting evidence only.
Catalyst gate must be verified from primary sources (for example Finnhub/news flow).
Positive sentiment cannot override failed catalyst gate.

## Output Contract

Return structured summary:
- ticker
- endpoint status map (ok/gated/empty/error)
- key sentiment/target fields
- confidence impact (supports, neutral, weakens)
- explicit note: "secondary evidence only"

## On-Demand Deep Reference

For full endpoint cookbook, command examples, scoring integration, and workflow timing:
- `workspace/skills/financialmodeling-prep-api/REFERENCE_FULL.md`
