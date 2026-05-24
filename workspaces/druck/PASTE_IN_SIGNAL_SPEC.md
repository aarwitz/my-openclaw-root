# Paste-In Signal Spec

Status: V1 primary human signal interface
Transport: FastAPI endpoint with bearer token auth
Endpoint: `POST /ingest/note`

## 1. Purpose

Manual paste-in is the highest-leverage human edge interface.

The endpoint must:
- preserve the original user note
- create a signal record
- extract a structured hypothesis
- trigger fast auto-investigation
- route the result into watch, trade, thesis, or ignore flows

The endpoint should never bypass the normal signal -> idea -> intent -> trade pipeline.

## 2. Input contract

Accepted fields:
- `text` (required)
- `url` (optional)
- `source_name` (optional)
- `intent` (required)

Allowed intent values:
- `trade_candidate`
- `monitor_only`
- `thesis_seed`
- `ignore_unless_confirmed`

## 3. Raw persistence

Before extraction, store:
- full raw text
- source_name
- url
- submit timestamp
- authenticated caller identity if available
- hash of text for dedupe checks

Raw content should be retained so later extraction improvements can be replayed.

## 4. LLM extraction output

Extract and store:
- tickers
- source
- claim
- claimed_evidence
- direction (`long`, `short`, `unknown`)
- time_sensitivity (`immediate`, `days`, `weeks`, `months`)
- confidence_signaled
- what_would_disprove
- strategy_route candidates
- llm_extraction_confidence

If extraction confidence is low, attach a data-quality flag.

## 5. Routing behavior by intent

### `trade_candidate`
- run auto-investigation immediately
- create or update signal and idea
- if corroboration >= threshold and strategy/risk filters pass, stage trade intent
- otherwise place in `watch`

### `monitor_only`
- create signal
- enrich enough for watch context
- do not trade automatically
- compute forward returns later for learning

### `thesis_seed`
- create signal
- attach to thesis discovery queue
- do not auto-trade

### `ignore_unless_confirmed`
- store signal
- no immediate surfacing unless independent corroborating signal arrives later

## 6. Auto-investigation payload

Target SLA: within 60 seconds when services are healthy.

Enrichment should gather:
- price/volume snapshot
- 1d, 5d, 30d returns
- ATR context
- filings from the last 90 days
- Event Registry news over 30 days
- analyst context via FMP
- average dollar volume and market cap
- thematic cluster assignment
- open-position correlation check
- existing exposure summary

## 7. Telegram response format

The Telegram response should be concise and decision-oriented:
- ticker(s)
- extracted claim
- key corroborations
- disconfirming facts if present
- liquidity / setup snapshot
- routing result (`staged`, `watch`, `thesis_queue`, `stored_only`)
- if staged, entry, stop, targets, and strategy

## 8. Trusted-source handling

If `source_name` matches a trusted-source registry:
- attach trust metadata to the signal
- track downstream outcomes by source
- use trust score only as a sizing/routing input, never as sole trade authorization

## 9. Dedupe logic

Likely duplicates should not create duplicate live ideas unless materially new.

Dedupe checks should consider:
- same ticker
- same source_name
- same or highly similar text hash / embedding similarity
- close submission timestamps

Duplicate paste-ins may still append evidence to an existing idea.

## 10. Learning outputs

Every paste-in should later contribute to:
- candidate decision logs
- forward return measurement
- source calibration
- false-positive / false-negative review

This is important even when the note does not become a trade.

## 11. Security requirements

- bearer token required
- token stored in `.env` only
- request body length capped
- raw content stored privately, never echoed into group contexts by default
- errors should not leak secrets or internal config

## 12. Required tests

1. valid trade_candidate paste creates a signal row
2. low-confidence extraction sets data-quality flag
3. monitor_only paste never stages a trade
4. ignore_unless_confirmed stores without surfacing
5. duplicate paste updates existing idea instead of creating duplicate live risk
6. Telegram summary is generated after enrichment completes

## 13. Next implementation companion

Create `trusted_sources.yaml` with:
- source handle / name
- trust tier or score
- notes
- active flag
