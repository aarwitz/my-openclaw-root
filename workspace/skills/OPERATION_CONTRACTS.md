# Skill Operation Contracts Index

This index tracks lean deterministic routers and their primary operation contracts.
Use each skill's `SKILL.md` for invocation rules and `REFERENCE_FULL.md` only for on-demand depth.

| Skill | Primary contract focus | Deep reference |
|---|---|---|
| alpaca | paper account readiness, positions/orders, live quote corroboration | workspace/skills/alpaca/REFERENCE_FULL.md |
| browser_app_QA | browser UI evidence capture and verification handoff | workspace/skills/browser_app_QA/REFERENCE_FULL.md |
| cloudflare | DNS/Pages/account ops with secret-safe minimal change workflow | workspace/skills/cloudflare/REFERENCE_FULL.md |
| codex-execution-harness | execution-first implementation loop with mandatory validation evidence | workspace/skills/codex-execution-harness/REFERENCE_FULL.md |
| druck-research | deterministic research synthesis and source validation | workspace/skills/druck-research/REFERENCE_FULL.md |
| ewag-testing-menu | deterministic testing menu and execution path selection | workspace/skills/ewag-testing-menu/REFERENCE_FULL.md |
| ewag-visual-qa | visual QA capture, compare, and issue evidence contract | workspace/skills/ewag-visual-qa/REFERENCE_FULL.md |
| financialmodeling-prep-api | fundamentals/events retrieval and scoring support | workspace/skills/financialmodeling-prep-api/REFERENCE_FULL.md |
| finnhub | catalyst checks, earnings/news truth, lightweight quotes | workspace/skills/finnhub/REFERENCE_FULL.md |
| github-ssh | git transport/auth preflight and deterministic account routing | workspace/skills/github-ssh/REFERENCE_FULL.md |
| massive | historical structure, regime math, and volatility framing | workspace/skills/massive/REFERENCE_FULL.md |
| newsapi-ai | published-news article/event/trend retrieval | workspace/skills/newsapi-ai/REFERENCE_FULL.md |
| openclaw-ops | config change safety, health verification, and safe-restart policy | workspace/skills/openclaw-ops/REFERENCE_FULL.md |
| resilife-product | product truth retrieval and deterministic answer shaping | workspace/skills/resilife-product/REFERENCE_FULL.md |
| task-manager | task lifecycle routing, status transitions, and guardrails | workspace/skills/task-manager/REFERENCE_FULL.md |
| task-manager-maintainer | maintenance workflows and deterministic updater behavior | workspace/skills/task-manager-maintainer/REFERENCE_FULL.md |

## Notes

- Keep `SKILL.md` lean and deterministic.
- Add new skills here when they adopt the router + deep-reference split.
- If a skill is not listed, it has not yet been normalized to this contract index.
