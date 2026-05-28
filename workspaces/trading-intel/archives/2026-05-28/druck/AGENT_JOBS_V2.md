# Agent Jobs V2

Status: active recurring job map

## Researcher

- `06:30 ET` core-source delta ingestion
- `20:30 ET` nightly hypothesis refresh
- event-driven run after major filing / trial / macro / sector changes

## Quant

- `08:15 ET` pre-market scoring and regime refresh
- `12:45 ET` intraday rerank
- `16:20 ET` after-close recalculation

## Trader

- `09:00 ET` pre-market decision pass
- `11:00 ET` confirmation / invalidation pass
- `13:30 ET` replacement / rotation pass
- `15:30 ET` close-risk pass
- event-driven order and reconciliation checks

## Critic

- `08:35 ET` high-priority hypothesis review
- before any `ready` intent can execute
- `17:00 ET` postmortem / error review queue
