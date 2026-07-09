# AutoTrade — Data Sources Catalog

**Discipline:** a source only feeds *sized trading* after its features pass the FDR-corrected,
cost-net backtest gate (`mechanism_backtest.py`). Free live-only sources (no history) can be
**advisory** (shown in UI) or **forward-collected** (build history → validate later), never
sized un-validated. Status as of **2026-07-03** (full dependency audit; see git history for
the audit detail). The monthly `data-scout-monthly` cron keeps this file honest — scout
entries append to the Scout log at the bottom.

> **Operator action wanted:** fill in the *Cost/yr* column below so add/remove decisions can
> be made on ROI. FMP is known-expensive; whether it stays depends on what only-FMP data
> actually earns.

## Tier 1 — wired & trading (the current brain)

| Source | Cost/yr (fill in) | What we use | Features fed | Only-from-this-source? |
|---|---|---|---|---|
| **FMP** | $___ (expensive) | fundamentals ttm, insider tx, analyst grades, EOD prices (20yr incl. delisted), profiles, S&P constituents, stock-peers, screener | 8 fundamentals + insider_net_180d, rating_net_90d, sector_rel_63d, peer_mom_21d, valuation, KG edges | **peers, grades, insider, delisted-history are FMP-only today.** Prices/fundamentals have free substitutes (Alpaca/EDGAR) at engineering cost |
| **Massive (Polygon)** | $___ (expensive) | daily bars, biweekly short interest, ticker news + article text | news_sent_7d/30d, news_vol_z, days_to_cover, short_int_chg_2m, LLM news features (article text!) | **article TEXT is the LLM-feature fuel — only source we hold.** Options endpoints exist on higher tier (403 today) |
| **X (Twitter)** | $___ (paid, confirmed working) | full-archive cashtag mention counts | x_mention_vol_z — top single feature 2024-26 (IC 0.056 mega-caps / 0.034 broad) | yes — no substitute archive |
| **Alpaca** | free | **DATA ONLY since D52 (2026-07-07)**: IEX bars/quotes + clock/calendar | spy_trend, execution-time quotes, calendar gates | broker layer ELIMINATED (internal engine is the desk broker); next: FMP/Massive quotes to complete the exit |
| **FRED/Treasury/Cboe** | free | rates curve, credit spreads, VIX term, macro series | 17 macro features; 16 validated macro mechanisms; regime signals | irreplaceable & free |
| **SEC EDGAR** | free | company facts (XBRL), 10-K/Q full text | valuation cross-check, filing_delta (Lazy-Prices MinHash) | free forever; underused (8-K event timing still open) |
| **Event Registry** | free tier | entity-tagged recent news + sentiment | catalyst_scan brief (advisory) | replaceable |

## Tier 2 — decided candidates (my recommendation, pending operator cost check)

| Candidate | Verdict | Why / expected value |
|---|---|---|
| **Options positioning (evaluated 2026-07-03)** | **AUDITION FREE, then Massive Options** | Target features: put/call vol + OI ratios, IV rank, 25Δ skew, net premium flow (daily, per underlying). Plan: (1) ThetaData FREE tier (1yr EOD hist, 20req/min — needs operator signup) → IC screen on ~100 names; (2) if any \|IC\|≥0.03 → Massive Options Developer $79/mo (4yr hist, flat files, same vendor/connector) → full FDR backtest 2022-26; (3) permanent slot only if it passes the standard bar. REJECTED: Unusual Whales (vendor-precomputed, not point-in-time reproducible, priciest), Databento OPRA (pay-per-volume institutional overkill for EOD aggregates) |
| **Reddit retail sentiment (apewisdom/free or SocialGrep)** | ADD (cheap) | complements X attention; WSB mention velocity had real 2021-24 signal in meme/mid-caps — exactly where X dilutes; forward-collect + backtest what history exists |
| **Congressional trading (QuiverQuant ~$500/yr, or free STOCK Act scrapes)** | TRIAL (cheap) | 30-45d disclosure lag blunts it; published alpha modest at our horizon. Worth one backtest column, not a subscription commitment until it passes FDR |
| **Google Trends (free)** | ADD (free) | search attention per name; free history via pytrends; cheap experiment alongside X |
| **Daily short interest (Ortex $$$/S3)** | HOLD | our biweekly Massive SI already earns a slot; daily adds most in squeeze regimes — revisit if a squeeze-mechanism proposal passes |
| **Earnings-call transcripts + LLM tone (FMP higher tier $___, or free EDGAR 8-K/press)** | HOLD until FMP cost decision | llm_features.py is ready for it; value real but paywalled — bundle with the FMP keep/kill decision |
| **Satellite (RS Metrics/SpaceKnow, $10k+/yr)** | **SKIP for now** | value concentrates in specific retail/energy names at quarterly horizon with long validation cycles; poor ROI at our book size ($100k paper). Revisit at real capital |
| **Credit-card panels (Second Measure etc., $50k+/yr)** | SKIP | gold standard but priced for funds; not at this scale |
| **Web/app traffic (Similarweb), job postings (LinkUp)** | WATCH | quarterly-horizon fundamentals nowcasts; scout re-checks pricing monthly |

## Housekeeping (from the 2026-07-03 audit)

- **gdelt.py** — orphaned in the Python pipeline (only lidi's intel-pack.ts uses GDELT independently). Candidate for deletion from connectors.
- **alphavantage.py** — silent no-op (no credential). Either provision a key (free tier) for its NEWS_SENTIMENT history or delete the branch.
- **yahoo.py** — fallback-only (valuation VIX/yield backup). Keep; it's the free failover.
- **finnhub-api.json, schwab-dev-*.json** — credentials exist, nothing calls them. Schwab token expired. Candidates for the shredder unless Finnhub's free insider/recs become a cross-check column.
- **FMP price targets WIRED 2026-07-03** (`price-target-news`, PIT by publishedDate): pt_upside, pt_rev_60d, pt_count_90d — in GEN_FEATURES, 600-name backfill running. **Institutional ownership: NOT on our tier** (all /stable paths 40x) — factor into the renewal decision. Estimate *revisions* have no PIT history endpoint; target revisions serve as the proxy.
- **ThetaData audition RUNNING 2026-07-03**: free-tier terminal live (tools/thetadata/, v2 REST :25510), `options_audit.py` pulling 1yr bulk EOD for top-64 names → options_daily table → IC screen (opt_pcr_vol / opt_vol_z / opt_net_prem). Decision rule per Tier 2.
- **Deleted 2026-07-03**: gdelt.py + alphavantage.py connectors and the feature_store alphavantage branch (credential-less no-ops).
- The web layer (`trader-live.ts`) hits Alpaca directly — must be re-pointed when the internal paper engine (docs/07) cuts over.

## Scout log (appended monthly by `data-scout-monthly`)

**2026-07-04 (operator-session audition, pre-cron):** ThetaData free-tier options
audition COMPLETE — 63 names × 1yr EOD aggregates, $0 spent. `opt_net_prem` pooled
IC 0.059 (passes the ≥0.03 audition bar); `opt_pcr_vol` 0.018; `opt_vol_z` 0.001
(the 20-name preview's −0.11 was small-sample noise). Verdict: AUDITION PASSED → operator
purchased Massive Options Developer 2026-07-09; 4yr flat-file backfill same day
(563,543 rows, 592 names, 2022-07-11..2026-07-08). **FULL TEST FAILED: 0 of 7
constructions survive BH-FDR q=0.10** (best: opt_net_prem IC 0.017, t=1.19,
46 rebalances). Family KILLED; recommend canceling the add-on. Panel banked.
