#!/usr/bin/env python3
"""Seed the named/dated episode library (and the mechanisms it exercises).

This is the curated, real-name ground-truth set the desk learns market structure
from. It replaces the abandoned anonymized validation_corpus. Sources: the
operator's hand-written ground-truth cases
(workspaces/trading-intel/aaron_manual_ground_truth_cases.txt) plus the desk's
own resolved market_events.

Design:
  - Every episode has real tickers + real dates and a walk-forward `knowable_at`.
  - Each links to a `mechanisms` row (the causal claim it exercises). Mechanisms
    that weren't in the seed-7 are created here as `candidate` (human-gated
    promotion via the normal rule_proposals flow — agents never self-promote).
  - Negative controls (correct action = no_trade despite an apparent signal) are
    first-class, not hidden.

Idempotent: deterministic ids (EP-<slug>, mech_<slug>) + INSERT OR REPLACE.

Usage:
  python3 seed_episodes.py                     # mechanisms + episodes
  python3 seed_episodes.py --seed-observations # also fold resolved episodes
                                               # into mechanism_observations,
                                               # then recompute posteriors
  python3 seed_episodes.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/developer/scripts")
from _db import audit, connect, emit, now_iso  # noqa: E402

EXPERIMENT_ID = "episode_library_v1"

# --- Mechanisms exercised by the library that weren't in the seed-7 ----------
# Kept as `candidate`; the calibration loop proposes promotion once enough
# decayed observations clear the CI bar. direction is the mechanism's own call.
NEW_MECHANISMS = [
    {
        "id": "mech_incumbent_ai_fear_overreaction",
        "name": "Durable-moat incumbent oversold on AI-disruption fear -> reverts as earnings hold",
        "antecedent_class": "ai_disruption_narrative_vs_incumbent",
        "transmission_chain": ["disruption headline", "sentiment-driven multiple compression",
                               "core franchise + earnings prove durable", "multiple re-rates back up"],
        "consequent_class": "oversold_incumbent_outperforms",
        "direction": "long",
        "horizon": "trend_1_3m",
        "regime_context": "works best when the franchise has pricing power and recurring revenue; fails for structurally disrupted names",
    },
    {
        "id": "mech_saas_seat_ai_substitution",
        "name": "Seat-based SaaS facing AI substitution -> estimate cuts -> de-rating ('saaspocalypse')",
        "antecedent_class": "ai_substitutes_per_seat_software",
        "transmission_chain": ["AI does the seat's work", "seat growth + net-retention slows",
                               "forward estimates cut", "multiple + price de-rate"],
        "consequent_class": "seat_saas_underperforms",
        "direction": "short",
        "horizon": "trend_1_3m",
        "regime_context": "discriminate: only names whose value is literally per-seat labor; sticky data/workflow moats are the GOOG case, not this",
    },
    {
        "id": "mech_datacenter_power_demand",
        "name": "AI datacenter buildout -> structural power/energy demand -> energy & land beneficiaries outperform",
        "antecedent_class": "ai_compute_buildout",
        "transmission_chain": ["compute capex surge", "grid/power becomes the bottleneck",
                               "power, fuel, land, utility names bid", "beneficiaries outperform"],
        "consequent_class": "energy_power_beneficiary_outperforms",
        "direction": "long",
        "horizon": "long_6m_plus",
        "regime_context": "structural multi-quarter theme; watch for crowding once consensus",
    },
    {
        "id": "mech_govt_contract_award",
        "name": "Real awarded government contract -> durable revenue catalyst -> beneficiary outperforms",
        "antecedent_class": "material_govt_contract_award",
        "transmission_chain": ["binding award filed/announced", "forward revenue + backlog revised up",
                               "estimates rise", "price drifts up"],
        "consequent_class": "contract_winner_outperforms",
        "direction": "long",
        "horizon": "position_1_4w",
        "regime_context": "the DURABLE leg (distinct from a politician's verbal mention) — needs a real, sized, filed award",
    },
    {
        "id": "mech_political_signal_reflexive",
        "name": "Public political/official signal names equities -> reflexive attention pop (NOT a durable edge)",
        "antecedent_class": "official_political_signal_naming_equities",
        "transmission_chain": ["signal visible to everyone", "attention/retail flow",
                               "short-term pop", "fades without a real cash-flow mechanism"],
        "consequent_class": "named_equity_short_term_pop",
        "direction": "long",
        "horizon": "swing_1_5d",
        "regime_context": "tradeable tactically off a genuinely knowable signal, but reflexive and crowd-followed; only convert to a core position if a durable mechanism (contract/earnings/regulation) attaches",
    },
    {
        "id": "mech_launch_dependency_shock",
        "name": "Single-point launch/infra dependency fails -> dependent name repriced down",
        "antecedent_class": "critical_dependency_failure",
        "transmission_chain": ["dependency (launch/supplier) fails", "timeline/viability risk repriced",
                               "dependent single-name sells off"],
        "consequent_class": "dependent_name_drops",
        "direction": "short",
        "horizon": "swing_1_5d",
        "regime_context": "event-driven; size for binary risk",
    },
    {
        "id": "mech_no_cashflow_narrative_decay",
        "name": "Asset with no repeatable cash-flow mechanism decays as hype/network-effect fades",
        "antecedent_class": "speculative_no_cashflow_asset",
        "transmission_chain": ["value rests on attention/network hope", "hype fades / better alternatives",
                               "no cash-flow floor", "structural drift down vs productive assets"],
        "consequent_class": "speculative_asset_underperforms",
        "direction": "short",
        "horizon": "long_6m_plus",
        "regime_context": "negative-control mechanism: the lesson is to AVOID buying hype without a cash-flow mechanism",
    },
    {
        "id": "mech_leveraged_etf_trend_compounding",
        "name": "Leveraged index ETF compounds strongly through a sustained uptrend (trap: vol decay + getting shaken out)",
        "antecedent_class": "sustained_index_uptrend",
        "transmission_chain": ["durable secular uptrend", "daily-rebalanced leverage compounds",
                               "outsized cumulative return", "BUT volatility decay punishes chop / fear-selling"],
        "consequent_class": "leveraged_etf_outsized_return",
        "direction": "long",
        "horizon": "long_6m_plus",
        "regime_context": "ONLY in a confirmed risk-on uptrend; lethal in chop/bear. Conviction + holding discipline is the edge, not the instrument",
    },
    {
        "id": "mech_memory_supply_cycle",
        "name": "AI-driven memory demand outstrips DRAM/NAND supply -> pricing power -> memory names outperform",
        "antecedent_class": "memory_demand_exceeds_supply",
        "transmission_chain": ["AI/datacenter memory demand surges", "fab supply is slow/constrained",
                               "DRAM/NAND pricing rises", "memory makers' margins + estimates rise"],
        "consequent_class": "memory_makers_outperform",
        "direction": "long",
        "horizon": "trend_1_3m",
        "regime_context": "cyclical; watch for the supply response / demand digestion that ends the cycle",
    },
    {
        "id": "mech_priced_in_insider_distribution",
        "name": "Growth largely priced in + insider distribution -> muted forward returns until a fresh catalyst",
        "antecedent_class": "priced_in_growth_with_insider_selling",
        "transmission_chain": ["consensus already extrapolates the growth", "insiders distribute",
                               "limited marginal buyer", "range-bound until a NEW catalyst (e.g. demand/geographic unlock)"],
        "consequent_class": "muted_until_fresh_catalyst",
        "direction": "neutral",
        "horizon": "trend_1_3m",
        "regime_context": "two-sided: the signal is muted forward returns, not a clean short; a genuine new catalyst flips it long",
    },
]

# --- The episode library -----------------------------------------------------
# knowable_at = earliest primary-source availability (the walk-forward gate).
# Dates use the operator's stated dates where given; coarser (month) where the
# case is structural and the exact day isn't the point — confidence reflects this.
EPISODES = [
    {
        "id": "EP-jobs-duration-tech-2026-06",
        "title": "May-2026 jobs blowout -> rate-cut hopes repriced out -> violent high-multiple tech/AI selloff",
        "tickers": ["SPY", "QQQ", "NVDA", "AVGO", "TSLA"],
        "theme": "macro_rates",
        "catalyst": "May nonfarm payrolls (released 2026-06-05) printed well above consensus. With inflation still above target, a hot labor market removed the case for near-term rate cuts. Real yields rose and the market repriced the rate path higher-for-longer.",
        "catalyst_class": "macro_release",
        "knowable_at": "2026-06-05T12:30:00Z",
        "resolved_at": "2026-06-05T20:00:00Z",
        "mechanism_id": "mech_jobs_duration_tech",
        "direction": "short",
        "correct_action": "Going into a hot-jobs surprise with inflation still high, trim/hedge long-duration high-multiple tech and AI/chip exposure; the repricing is in real yields, not company fundamentals.",
        "naive_trap": "Reading 'strong economy = good for stocks' and staying max-long high-multiple tech into the print.",
        "observed_moves": {"SPY": -2.58, "QQQ": -2.4, "NVDA": -5.1, "AVGO": -4.3, "TSLA": -3.8},
        "outcome": "thesis_confirmed",
        "horizon": "swing_1_5d",
        "regime_context": "inflation-above-target, data-dependent Fed",
        "lesson": "Labor-market upside surprises lift the expected rate path and real yields, compressing long-duration high-multiple multiples. The catalyst is knowable in advance: the jobs calendar date + a read on consensus. Pre-position duration risk ahead of jobs/CPI when inflation is still hot.",
        "is_negative_control": 0,
        "confidence": "high",
        "source_refs": ["https://www.bls.gov/news.release/empsit.nr0.htm"],
    },
    {
        "id": "EP-iran-oil-relief-2026-06",
        "title": "Iran crude sanctions relief -> oil & vol down -> relief bid in long-duration growth",
        "tickers": ["SPY", "QQQ", "USO", "CL"],
        "theme": "macro_rates",
        "catalyst": "US Treasury OFAC action (2026-06-10) authorized Iranian crude-related transactions and a Strait of Hormuz reopening path; by 2026-06-12 the tape traded the energy/inflation risk premium as EASING.",
        "catalyst_class": "geopolitical",
        "knowable_at": "2026-06-10T18:00:00Z",
        "resolved_at": "2026-06-12T20:00:00Z",
        "mechanism_id": "mech_oil_inflation_rates",
        "direction": "long",
        "correct_action": "When a feared energy-supply shock starts resolving through official policy + shipping normalization, fade the inflation-premium: expect lower oil, lower vol, relief bid in duration-sensitive growth.",
        "naive_trap": "Staying positioned for the Middle-East inflation shock after the policy catalyst already reversed it.",
        "observed_moves": {"SPY": 0.54, "QQQ": 0.65},
        "outcome": "thesis_confirmed",
        "horizon": "swing_1_5d",
        "regime_context": "easing geopolitical risk premium",
        "lesson": "The fastest cross-asset response to a resolving supply shock is lower oil -> lower vol -> duration relief. Track official policy actions (OFAC, shipping) as primary sources, not headlines.",
        "is_negative_control": 0,
        "confidence": "high",
        "source_refs": ["https://ofac.treasury.gov/recent-actions"],
    },
    {
        "id": "EP-goog-ai-fear-overreaction",
        "title": "GOOG oversold on OpenAI-browser/AI-late fear -> reverted as Gemini + search earnings held",
        "tickers": ["GOOG", "GOOGL"],
        "theme": "ai_disruption",
        "catalyst": "OpenAI signaled a possible browser/search push; a wave of bearish 'Google is late on AI / ChatGPT usurps search' sentiment compressed GOOG's multiple.",
        "catalyst_class": "sentiment",
        "knowable_at": "2026-01-15T00:00:00Z",
        "resolved_at": "2026-04-30T00:00:00Z",
        "mechanism_id": "mech_incumbent_ai_fear_overreaction",
        "direction": "long",
        "correct_action": "Buy the fear-driven dip in a durable-moat incumbent whose core revenue is intact and whose AI (Gemini) is actually competitive; the narrative overstated disruption.",
        "naive_trap": "Selling GOOG on the disruption narrative and crowd fear despite no fundamental impairment.",
        "observed_moves": {"GOOG": 0.0},
        "outcome": "thesis_confirmed",
        "horizon": "trend_1_3m",
        "regime_context": "AI-disruption narrative dominating sentiment",
        "lesson": "Distinguish narrative fear from fundamental impairment. A durable-moat incumbent (recurring revenue, pricing power, competitive AI) sells off on disruption fear and re-rates as earnings keep coming. The discriminator vs the saaspocalypse names: does the moat actually get substituted, or just feared?",
        "is_negative_control": 0,
        "confidence": "high",
        "source_refs": [],
    },
    {
        "id": "EP-saaspocalypse-seat-ai",
        "title": "Saaspocalypse: seat-based enterprise SaaS de-rated on AI substitution",
        "tickers": ["FIG", "DUOL", "WDAY", "NOW", "CRM", "EPAM"],
        "theme": "ai_disruption",
        "catalyst": "Growing evidence AI substitutes per-seat software labor; bearish sentiment on seat-based SaaS led to estimate cuts and stagnant/lower prices across the group.",
        "catalyst_class": "sentiment",
        "knowable_at": "2026-02-01T00:00:00Z",
        "resolved_at": "2026-06-01T00:00:00Z",
        "mechanism_id": "mech_saas_seat_ai_substitution",
        "direction": "short",
        "correct_action": "Underweight/short seat-based SaaS whose value is literally per-seat labor (design seats, language-learning seats, dev seats) as AI compresses seat growth and net retention.",
        "naive_trap": "Treating all 'AI losers' the same — shorting durable-moat incumbents (GOOG) alongside genuinely substitutable seat-SaaS.",
        "observed_moves": {},
        "outcome": "thesis_confirmed",
        "horizon": "trend_1_3m",
        "regime_context": "AI capability inflection hitting software labor",
        "lesson": "The short is specifically per-seat labor software. The paired discriminator is GOOG: durable workflow/data moats are NOT the same trade as substitutable seats. Mis-classifying the two is the main way this thesis loses money.",
        "is_negative_control": 0,
        "confidence": "medium",
        "source_refs": [],
    },
    {
        "id": "EP-dell-pentagon-contract-2026-05",
        "title": "DELL: from a politician's 'buy a Dell' to a real $9.7B Pentagon award + earnings beat",
        "tickers": ["DELL"],
        "theme": "political_signal",
        "catalyst": "2026-05-08 a political figure publicly said 'go buy a Dell'. 2026-05-27 DELL was awarded a real ~$9.7B US Pentagon contract; ~2026-05-28 it reported a strong earnings beat and surged ~30%. Up ~80% since 2026-05-08.",
        "catalyst_class": "policy",
        "knowable_at": "2026-05-27T13:00:00Z",
        "resolved_at": "2026-05-29T00:00:00Z",
        "mechanism_id": "mech_govt_contract_award",
        "direction": "long",
        "correct_action": "Trade the DURABLE leg: the filed $9.7B contract award (real forward revenue/backlog), confirmed by the subsequent earnings beat — not the verbal 'buy a Dell' mention.",
        "naive_trap": "Either dismissing the whole thing as a political stunt, OR buying purely on the tweet with no durable mechanism behind it.",
        "observed_moves": {"DELL": 30.0},
        "outcome": "thesis_confirmed",
        "horizon": "position_1_4w",
        "regime_context": "govt-spending tailwind, AI-server demand",
        "lesson": "Separate the reflexive signal (a public mention) from the durable catalyst (a real, sized, filed contract + earnings confirmation). The durable leg is what justifies a core position. Same family: INTC/MP/LAC also moved on real govt/treasury action.",
        "is_negative_control": 0,
        "confidence": "high",
        "source_refs": [],
    },
    {
        "id": "EP-hood-baby-accounts-2026-06",
        "title": "HOOD: official 'baby accounts' rollout shown hosted by Robinhood -> reflexive +35%",
        "tickers": ["HOOD"],
        "theme": "political_signal",
        "catalyst": "A White House baby-stock-account program; the official commercial visibly used Robinhood as the host. The hosting relationship was knowable from the primary source (the commercial).",
        "catalyst_class": "policy",
        "knowable_at": "2026-06-05T00:00:00Z",
        "resolved_at": "2026-06-11T00:00:00Z",
        "mechanism_id": "mech_political_signal_reflexive",
        "direction": "long",
        "correct_action": "A small tactical long off a genuinely knowable hosting signal is defensible (+35% over days). But it is reflexive attention flow, not a durable cash-flow edge — keep it small and don't confuse it with a core thesis.",
        "naive_trap": "Sizing it like a durable-edge core position, or chasing every politically-named ticker as if the signal repeats reliably.",
        "observed_moves": {"HOOD": 35.0},
        "outcome": "thesis_confirmed",
        "horizon": "swing_1_5d",
        "regime_context": "retail-attention-driven, policy-adjacent",
        "lesson": "Knowable public signals CAN produce real short-term moves, but following political signals systematically is not a durable edge unless a real cash-flow mechanism (a contract, a fee stream) attaches. Tactical, small, time-boxed. Contrast with DELL where a real contract DID attach.",
        "is_negative_control": 0,
        "confidence": "medium",
        "source_refs": [],
    },
    {
        "id": "EP-memory-supply-cycle",
        "title": "Memory supercycle: AI demand outstrips DRAM/NAND supply -> MU/SNDK outperform",
        "tickers": ["MU", "SNDK"],
        "theme": "supply_cycle",
        "catalyst": "AI/datacenter memory demand surged while fab supply stayed constrained; everything became memory-constrained, lifting DRAM/NAND pricing.",
        "catalyst_class": "supply_chain",
        "knowable_at": "2026-01-01T00:00:00Z",
        "resolved_at": "2026-06-01T00:00:00Z",
        "mechanism_id": "mech_memory_supply_cycle",
        "direction": "long",
        "correct_action": "Own memory makers (MU, SNDK) and memory-exposed baskets when AI demand visibly outruns slow fab supply — pricing power flows to margins.",
        "naive_trap": "Missing it because memory is 'commodity/cyclical' and assuming no pricing power.",
        "observed_moves": {},
        "outcome": "thesis_confirmed",
        "horizon": "trend_1_3m",
        "regime_context": "AI capex supercycle",
        "lesson": "The signs were observable: memory-constrained datacenters, rising DRAM contract prices. Supply-constrained commodities with a structural demand driver get pricing power. Watch the supply response for the cycle top.",
        "is_negative_control": 0,
        "confidence": "medium",
        "source_refs": [],
    },
    {
        "id": "EP-datacenter-energy-demand",
        "title": "AI datacenter power demand -> energy/utility/land beneficiaries (CEG, TPL, MPC, VLO, BE)",
        "tickers": ["CEG", "TPL", "MPC", "VLO", "BE"],
        "theme": "energy_demand",
        "catalyst": "Data-center buildout drove a step-change in power and energy demand; power producers, refiners, royalty land, and fuel-cell names benefited.",
        "catalyst_class": "supply_chain",
        "knowable_at": "2026-02-01T00:00:00Z",
        "resolved_at": "2026-06-01T00:00:00Z",
        "mechanism_id": "mech_datacenter_power_demand",
        "direction": "long",
        "correct_action": "Own the power/energy bottleneck of the AI buildout — the picks-and-shovels one layer down from chips (power generation, fuel, land).",
        "naive_trap": "Only buying chips/AI software and ignoring that power is the binding constraint.",
        "observed_moves": {},
        "outcome": "thesis_confirmed",
        "horizon": "long_6m_plus",
        "regime_context": "AI compute buildout, grid constraints",
        "lesson": "When a boom has a physical bottleneck (power), the bottleneck's suppliers capture durable demand. Map the supply chain one layer past the obvious winner.",
        "is_negative_control": 0,
        "confidence": "medium",
        "source_refs": [],
    },
    {
        "id": "EP-asts-launch-dependency-shock",
        "title": "ASTS sold off after a Blue Origin launch explosion (dependency shock)",
        "tickers": ["ASTS"],
        "theme": "launch_risk",
        "catalyst": "A Blue Origin launch explosion raised timeline/viability risk for launch-dependent ASTS, which fell the next day.",
        "catalyst_class": "corporate_action",
        "knowable_at": "2026-05-20T00:00:00Z",
        "resolved_at": "2026-05-21T00:00:00Z",
        "mechanism_id": "mech_launch_dependency_shock",
        "direction": "short",
        "correct_action": "Reprice/derisk a name whose plan depends on a single launch/infra provider when that provider has a public failure.",
        "naive_trap": "Treating the dependent name as insulated from its supplier's failure.",
        "observed_moves": {"ASTS": 0.0},
        "outcome": "thesis_confirmed",
        "horizon": "swing_1_5d",
        "regime_context": "event-driven, binary",
        "lesson": "Map single-point dependencies. A supplier's public failure transmits straight into the dependent name's timeline risk. Event-driven and binary — size accordingly.",
        "is_negative_control": 0,
        "confidence": "medium",
        "source_refs": [],
    },
    {
        "id": "EP-leveraged-etf-trend-hold",
        "title": "TQQQ / SOXL: leveraged ETFs compounded huge through the secular uptrend (trap: getting shaken out)",
        "tickers": ["TQQQ", "SOXL"],
        "theme": "trend_compounding",
        "catalyst": "A sustained multi-year risk-on uptrend in the Nasdaq-100 and semis; daily-rebalanced 3x ETFs compounded to outsized cumulative returns.",
        "catalyst_class": "other",
        "knowable_at": "2022-10-01T00:00:00Z",
        "resolved_at": "2026-06-01T00:00:00Z",
        "mechanism_id": "mech_leveraged_etf_trend_compounding",
        "direction": "long",
        "correct_action": "In a CONFIRMED secular uptrend, a held leveraged-index position compounds enormously; the edge is conviction + holding discipline through volatility, not timing.",
        "naive_trap": "Getting scared out during drawdowns (the operator's own stated mistake) and forfeiting the compounding; OR holding leveraged ETFs through chop/bear where volatility decay destroys capital.",
        "observed_moves": {},
        "outcome": "thesis_confirmed",
        "horizon": "long_6m_plus",
        "regime_context": "secular risk-on uptrend (regime-gated)",
        "lesson": "Leveraged ETFs are a regime instrument: lethal in chop/bear (vol decay), spectacular in a sustained uptrend. Gate by regime, then the hard part is psychological — holding through drawdowns. NOT a buy-and-forget.",
        "is_negative_control": 0,
        "confidence": "medium",
        "source_refs": [],
    },
    {
        "id": "EP-nvda-priced-in-thiel-jensen",
        "title": "NVDA: priced-in growth + Thiel distribution -> muted H1-2026 until the Jensen China-visit catalyst",
        "tickers": ["NVDA"],
        "theme": "priced_in",
        "catalyst": "Through H1 2026 NVDA was a muted winner — growth largely priced in, with notable insider distribution (Thiel sold ahead). A fresh catalyst (Jensen's China visit/invite) spiked it before it faded back.",
        "catalyst_class": "sentiment",
        "knowable_at": "2026-01-01T00:00:00Z",
        "resolved_at": "2026-06-01T00:00:00Z",
        "mechanism_id": "mech_priced_in_insider_distribution",
        "direction": "neutral",
        "correct_action": "When growth is consensus-priced and insiders distribute, expect muted/range-bound forward returns; trade the FRESH catalyst (China demand unlock) rather than the stale growth story.",
        "naive_trap": "Assuming a great company is always a great stock — buying priced-in growth and expecting continued outperformance with no new catalyst.",
        "observed_moves": {},
        "outcome": "thesis_confirmed",
        "horizon": "trend_1_3m",
        "regime_context": "late-cycle leadership, crowded",
        "lesson": "Price already reflects consensus growth; the marginal return needs a NEW catalyst. Insider distribution is a tell that the easy money is made. Watch for fresh demand unlocks (geographies, products) as the re-rating trigger.",
        "is_negative_control": 0,
        "confidence": "medium",
        "source_refs": [],
    },
    {
        "id": "EP-bitcoin-no-cashflow-decay",
        "title": "Bitcoin: no cash-flow mechanism -> hype/network-effect faded -> structural underperformance (negative control)",
        "tickers": ["BTC"],
        "theme": "speculative_narrative",
        "catalyst": "The investment case rested on network-effect/hype rather than a repeatable cash-flow mechanism; as the hype faded and alternatives competed, it underperformed productive assets.",
        "catalyst_class": "sentiment",
        "knowable_at": "2025-06-01T00:00:00Z",
        "resolved_at": "2026-06-01T00:00:00Z",
        "mechanism_id": "mech_no_cashflow_narrative_decay",
        "direction": "short",
        "correct_action": "Do NOT allocate to an asset whose entire thesis is attention/network hope with no cash-flow floor; prefer productive, cash-generating assets.",
        "naive_trap": "Buying on hype/FOMO and a 'number go up' narrative with no underlying cash-flow mechanism.",
        "observed_moves": {},
        "outcome": "thesis_confirmed",
        "horizon": "long_6m_plus",
        "regime_context": "post-hype, higher-real-rate world",
        "lesson": "Negative control: an asset with no repeatable cash-flow mechanism has no valuation floor when sentiment turns. The mandate requires a falsifiable cash-flow mechanism — hype alone is a decline-to-trade.",
        "is_negative_control": 1,
        "confidence": "medium",
        "source_refs": [],
    },
    {
        "id": "EP-vicor-unexplained-jump",
        "title": "VICOR jumped sharply — suspected deals/design-wins (low-confidence, needs primary-source attribution)",
        "tickers": ["VICR"],
        "theme": "supply_chain",
        "catalyst": "VICOR (VICR) rose sharply, suspected to be deal/design-win driven, but the operator did not have the primary source at the time.",
        "catalyst_class": "other",
        "knowable_at": "2026-04-01T00:00:00Z",
        "resolved_at": None,
        "mechanism_id": "mech_supply_constraint_pricing_power",
        "direction": "long",
        "correct_action": "Flagged as a research gap: when a name moves sharply, hunt the primary source (8-K, design-win, contract) BEFORE concluding — don't backfit a story.",
        "naive_trap": "Inventing a post-hoc narrative for a move you didn't source, or chasing it blind.",
        "observed_moves": {},
        "outcome": "inconclusive",
        "horizon": "position_1_4w",
        "regime_context": "unknown — unsourced",
        "lesson": "Honest gap in the record. The lesson is process: a sharp unexplained move is a prompt to find the primary catalyst, not to fabricate one. Left low-confidence/inconclusive until sourced.",
        "is_negative_control": 0,
        "confidence": "low",
        "source_refs": [],
    },
]

CONF_WEIGHT = {"high": 1.0, "medium": 0.6, "low": 0.3}


def upsert_mechanisms(conn, dry_run: bool) -> int:
    existing = {r[0] for r in conn.execute("SELECT id FROM mechanisms")}
    n = 0
    for m in NEW_MECHANISMS:
        if m["id"] in existing:
            continue
        n += 1
        if dry_run:
            continue
        conn.execute(
            "INSERT INTO mechanisms (id, created_at, created_by, name, antecedent_class, "
            "transmission_chain_json, consequent_class, direction, horizon, regime_context, "
            "prior_alpha, prior_beta, half_life_days, status, experiment_id) "
            "VALUES (?, ?, 'developer', ?, ?, ?, ?, ?, ?, ?, 1.0, 1.0, 180, 'candidate', ?)",
            (m["id"], now_iso(), m["name"], m["antecedent_class"],
             json.dumps(m["transmission_chain"]), m["consequent_class"], m["direction"],
             m.get("horizon"), m.get("regime_context"), EXPERIMENT_ID),
        )
        audit(conn, actor="developer", entity_type="mechanism", entity_id=m["id"],
              action="seed_candidate", rationale=f"episode-library mechanism: {m['name'][:120]}",
              experiment_id=EXPERIMENT_ID)
    return n


def upsert_episodes(conn, dry_run: bool) -> int:
    if dry_run:
        return len(EPISODES)
    for e in EPISODES:
        conn.execute("DELETE FROM episodes WHERE id = ?", (e["id"],))
        conn.execute(
            "INSERT INTO episodes (id, created_at, created_by, title, tickers_json, theme, "
            "catalyst, catalyst_class, knowable_at, resolved_at, mechanism_id, direction, "
            "correct_action, naive_trap, observed_moves_json, outcome, horizon, regime_context, "
            "lesson_concise, is_negative_control, confidence, source_refs_json, experiment_id) "
            "VALUES (?, ?, 'human', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (e["id"], now_iso(), e["title"], json.dumps(e["tickers"]), e.get("theme"),
             e["catalyst"], e.get("catalyst_class"), e["knowable_at"], e.get("resolved_at"),
             e.get("mechanism_id"), e.get("direction"), e["correct_action"], e.get("naive_trap"),
             json.dumps(e.get("observed_moves", {})), e.get("outcome"), e.get("horizon"),
             e.get("regime_context"), e.get("lesson"), e.get("is_negative_control", 0),
             e.get("confidence"), json.dumps(e.get("source_refs", [])), EXPERIMENT_ID),
        )
    audit(conn, actor="human", entity_type="episode", entity_id="episode_library_v1",
          action="seed_library", rationale=f"{len(EPISODES)} named/dated episodes from operator ground-truth",
          experiment_id=EXPERIMENT_ID)
    return len(EPISODES)


def seed_observations(conn, dry_run: bool) -> int:
    """Fold resolved, directional episodes into the mechanism observation ledger.

    Skips negative controls and inconclusive/unsourced episodes — those teach
    discrimination, not mechanism reliability. observed_at = the episode's
    resolved_at so half-life decay ages them by their real vintage. weight scaled
    by our confidence in the episode. Idempotent via deterministic obs id.
    """
    n = 0
    for e in EPISODES:
        if e.get("is_negative_control"):
            continue
        if e.get("outcome") != "thesis_confirmed":
            continue
        mech = e.get("mechanism_id")
        resolved = e.get("resolved_at")
        if not mech or not resolved:
            continue
        obs_id = f"mobs-ep-{e['id']}"
        n += 1
        if dry_run:
            continue
        conn.execute("DELETE FROM mechanism_observations WHERE id = ?", (obs_id,))
        conn.execute(
            "INSERT INTO mechanism_observations (id, mechanism_id, observed_at, source_type, "
            "source_id, outcome, weight, regime_at_obs, notes, experiment_id) "
            "VALUES (?, ?, ?, 'manual', ?, 'hit', ?, ?, ?, ?)",
            (obs_id, mech, resolved, e["id"], CONF_WEIGHT.get(e.get("confidence"), 0.5),
             e.get("regime_context"), f"episode {e['id']}: {e['title'][:140]}", EXPERIMENT_ID),
        )
    return n


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed-observations", action="store_true",
                   help="also fold resolved episodes into mechanism_observations (then run calibrate.py to recompute)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    conn = connect()
    try:
        mech_n = upsert_mechanisms(conn, args.dry_run)
        ep_n = upsert_episodes(conn, args.dry_run)
        obs_n = seed_observations(conn, args.dry_run) if args.seed_observations else 0
        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()

    emit({
        "ok": True,
        "dry_run": args.dry_run,
        "mechanisms_added": mech_n,
        "episodes_seeded": ep_n,
        "observations_seeded": obs_n,
        "note": "run archivist/scripts/calibrate.py --no-resolve --no-propose to recompute posteriors"
                if obs_n else None,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
