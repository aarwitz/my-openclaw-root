# Druck Phase II Pipeline

Lightweight, deterministic Phase II trading-research pipeline implementing
[`AUTONOMOUS_PM_OPERATING_MODEL.md`](../AUTONOMOUS_PM_OPERATING_MODEL.md).

## Design principles

- **Rules-first, not ML.** Every score and class derives from explicit rules.
- **Source authority.** Finnhub = catalyst truth, Massive = price-structure truth,
  Alpaca = account/live-execution truth, FMP = analyst-sentiment support.
- **Fail closed.** Missing primary inputs degrade class — never assume favorable.
- **Idempotent.** All sheet writes keyed by `(date, ticker)`.
- **Auditable.** Every scored row carries the raw inputs that produced it.
- **Stateless modules.** Each function takes inputs in, produces a typed record out.

## Layout

```
phase2/
├── README.md                  this file
├── RULES.md                   quant calibration & rule definitions
├── schema.py                  CandidateRecord + enums (canonical row schema)
├── http_util.py               retrying HTTP wrapper + on-disk raw JSON cache
├── adapters/
│   ├── finnhub.py             earnings, news, revisions, quotes
│   ├── massive.py             daily aggregates, ATR, dollar volume, regime
│   ├── alpaca.py              account, positions, quotes, orders (paper)
│   ├── fmp.py                 grades, ratings snapshot, price-target context
│   └── sheets.py              gog wrapper for Candidates/Outcomes idempotent I/O
├── regime.py                  SPY/VIX → regime label
├── setup_classifier.py        rule-based setup-state assignment
├── scoring.py                 deterministic scorer + penalties + class
├── normalize.py               one (date, ticker) → CandidateRecord
├── candidate_gen.py           build universe from Finnhub catalysts + Massive movers
├── monday_open.py             Monday-open orchestrator
├── replay.py                  recompute scores from frozen raw + outcome filler
└── cli.py                     `python -m phase2.cli <command>`
```

## Install / run

No package install — runs in place. Requires Python 3.10+, `requests`, and
the `gog` CLI for sheet I/O. All credentials read from `~/.openclaw/credentials/`.

```bash
cd ~/.openclaw/workspaces/druck
# universe → scored Candidates row(s)
python3 -m phase2.cli normalize --ticker NVDA
python3 -m phase2.cli normalize --ticker NVDA --write-sheet

# candidate generation (catalyst + movers)
python3 -m phase2.cli candidates --date 2026-05-09 --max 25

# Monday-open orchestration
python3 -m phase2.cli monday-open

# regime only
python3 -m phase2.cli regime

# ATS v6 schema/config validation
python3 -m phase2.cli validate-ats-v6

# candidate-decision replay / holdout persistence smoke test
python3 -m phase2.cli replay-candidate-decisions

# replay / outcomes filler
python3 -m phase2.cli replay --since 2026-04-15
python3 -m phase2.cli outcomes --as-of 2026-05-09
```

## Raw-JSON cache

Every adapter call writes its raw response to
`~/.openclaw/workspaces/druck/phase2_cache/raw/<source>/<YYYY-MM-DD>/<ticker>.<endpoint>.json`.

This local cache:
- Guarantees replay can recompute scores from frozen inputs.
- Survives process crashes (audit trail on disk).
- Enables debugging (inspect raw data that fed a score).

**Note:** This is a local cache only. To archive raw data to Drive for
long-term storage/analysis, use a separate scheduled task (e.g., cron job
that `gog files upload` the cache directory). The phase2 pipeline does not
auto-upload to Drive.

## Sheets

Spreadsheet `19LPX1xGCme4umn22GN4Z7WBQxGZBWWcysDjM6JEW-D4` —
4 tabs: Holdings, Watchlist, Candidates, Outcomes.

Idempotency: writes look up `(date, ticker)` first; update if found, append
otherwise; read back and assert before claiming success.

## Failure semantics

- **Adapter failure** (HTTP, auth, parse): record carries `error` + the
  affected derived fields are `None`. Scoring respects nullness.
- **Missing required scoring input**: `recommendation_class = incomplete`.
- **Failed catalyst gate**: `recommendation_class = watch_only` max.
- **Live conflict between Alpaca & Massive near open**: `conditional_buy` max + `data_discrepancy` flag.
- **Crisis regime**: all → `watch_only` regardless.

## Non-goals

No live broker execution. No options pricing (Phase III). No social-media
ingestion (Phase III). No ML ranking.
