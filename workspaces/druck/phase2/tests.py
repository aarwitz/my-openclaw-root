"""Pure-function smoke tests — no network, no filesystem mutation.

Run: `python3 -m phase2.tests`
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

from .schema import (
    CandidateRecord, RecommendationClass, Regime, SetupState, CatalystType,
)
from . import scoring, ats_v6, candidate_decisions, db
from .setup_classifier import SetupInputs, classify as classify_setup
from .regime import classify as classify_regime


FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    if not cond:
        FAILED.append(label)
        print(f"  FAIL: {label}")
    else:
        print(f"  ok:   {label}")


def test_regime():
    print("\n[regime]")
    check(classify_regime(420, 415, 405, 14, 0.01)[0] == Regime.RISK_ON.value, "risk_on basic")
    check(classify_regime(400, 410, 415, 22, -0.01)[0] == Regime.RISK_OFF.value, "risk_off below 50d MA")
    check(classify_regime(400, 405, 395, 20, 0.0)[0] == Regime.CAUTION.value, "caution below 20d MA")
    check(classify_regime(400, 405, 395, 40, 0.0)[0] == Regime.CRISIS.value, "crisis VIX>35")
    check(classify_regime(400, 405, 395, 22, -0.06)[0] == Regime.CRISIS.value, "crisis spy 5d -6%")
    check(classify_regime(420, 415, 405, None, 0.01)[0] == Regime.MACRO_UNKNOWN.value, "missing VIX becomes macro unknown")


def test_setup_classifier():
    print("\n[setup_classifier]")
    s_breakout = SetupInputs(
        last_close=105, prev_close=104, twenty_day_high=104.5,
        twenty_day_ma=102, fifty_day_ma=95,
        atr_abs=2.0, atr_pct=0.019, volume_ratio=1.8, rsi_14=60,
        five_day_change_pct=0.04, catalyst_in_last_5d=True,
    )
    state, _ = classify_setup(s_breakout)
    check(state == SetupState.BREAKOUT_CONTINUATION.value, "breakout detected")

    s_overext = SetupInputs(
        last_close=120, prev_close=119, twenty_day_high=121,
        twenty_day_ma=100, fifty_day_ma=95,
        atr_abs=5.0, atr_pct=0.04, volume_ratio=1.0, rsi_14=80,
        five_day_change_pct=0.20, catalyst_in_last_5d=False,
    )
    state, _ = classify_setup(s_overext)
    check(state == SetupState.OVEREXTENDED_CHASE.value, "overextended overrides breakout")

    s_meanrev = SetupInputs(
        last_close=98, prev_close=95, twenty_day_high=110,
        twenty_day_ma=100, fifty_day_ma=102,
        atr_abs=2.0, atr_pct=0.02, volume_ratio=1.0, rsi_14=22,
        five_day_change_pct=-0.08,
    )
    state, _ = classify_setup(s_meanrev)
    check(state == SetupState.MEAN_REVERSION_BOUNCE.value, "mean reversion detected")


def _make_record(**over) -> CandidateRecord:
    base = dict(
        date="2026-05-09", ticker="NVDA",
        last_close=110.0, prev_close=108.0,
        twenty_day_high=109.0, twenty_day_ma=100.0, fifty_day_ma=95.0,
        atr_abs=2.5, atr_pct=0.023,
        volume_ratio=1.8, dollar_volume_m=2500.0, five_day_change_pct=0.04,
        extension_vs_20d_ma_atr=4.0,  # default very extended; tests override
        catalyst_pass=True, verified_catalyst_type=CatalystType.EARNINGS_DOUBLE_BEAT.value,
        eps_actual=2.10, eps_estimate=1.80, revenue_actual=120e9, revenue_estimate=110e9,
        guidance_raise_flag=True,
        setup_state=SetupState.POST_EARNINGS_DRIFT.value,
        sector="Tech", factor="AI",
        portfolio_fit_bucket="missing",
        regime=Regime.RISK_ON.value,
    )
    base.update(over)
    return CandidateRecord(**base)


def test_scoring_buy_ready():
    print("\n[scoring buy_ready]")
    r = _make_record(extension_vs_20d_ma_atr=1.0)
    scoring.score(r)
    check(r.recommendation_class == RecommendationClass.BUY_READY.value,
          f"buy_ready expected, got {r.recommendation_class} (score={r.total_score_final})")
    check(r.extension_penalty == 0, "no ext penalty under 1.5 ATR")


def test_scoring_extension_demote():
    print("\n[scoring extension demote]")
    r = _make_record(extension_vs_20d_ma_atr=2.6, setup_state=SetupState.BREAKOUT_CONTINUATION.value)
    scoring.score(r)
    check(r.extension_penalty == -15.0, "max ext penalty at >=2.5 ATR")
    check(r.recommendation_class == RecommendationClass.CONDITIONAL_BUY.value
          or r.recommendation_class == RecommendationClass.WATCH_ONLY.value,
          f"demoted from buy_ready, got {r.recommendation_class}")


def test_scoring_no_catalyst():
    print("\n[scoring no catalyst]")
    r = _make_record(catalyst_pass=False, verified_catalyst_type=CatalystType.NONE.value, extension_vs_20d_ma_atr=1.0)
    scoring.score(r)
    check(r.recommendation_class in (RecommendationClass.WATCH_ONLY.value, RecommendationClass.AVOID.value),
          f"no catalyst → never buy, got {r.recommendation_class}")


def test_scoring_crisis_overlay():
    print("\n[scoring crisis overlay]")
    r = _make_record(extension_vs_20d_ma_atr=1.0, regime=Regime.CRISIS.value)
    scoring.score(r)
    check(r.recommendation_class == RecommendationClass.WATCH_ONLY.value,
          f"crisis → watch_only, got {r.recommendation_class}")


def test_scoring_incomplete():
    print("\n[scoring incomplete]")
    r = _make_record(atr_pct=None)
    scoring.score(r)
    check(r.recommendation_class == RecommendationClass.INCOMPLETE.value, "missing ATR → incomplete")


def test_vol_efficiency_bounds():
    print("\n[vol efficiency bounds]")
    r = _make_record(atr_pct=0.001, extension_vs_20d_ma_atr=1.0)
    scoring.score(r)
    check(0 <= r.volatility_efficiency_score <= 10, "vol-eff capped 0..10 even with tiny ATR")


def test_alpha_ranker():
    print("\n[alpha ranker]")
    from .alpha_ranker import score_one
    from .market_scanner import LiquidMover
    from .catalyst_verifier import CatalystResult
    m = LiquidMover(
        ticker="TEST", asset_type="stock", last_price=100.0,
        pct_change_1d=0.02, pct_change_5d=0.06, dollar_volume=300.0,
        atr_pct=0.025, above_20dma=True, above_50dma=True, above_200dma=True,
        spread_pct=0.003, sector="Technology",
    )
    cat = CatalystResult(ticker="TEST", catalyst_type="earnings_double_beat",
                         catalyst_confidence=0.9)
    s = score_one(m, cat, regime="risk_on", spy_5d_pct=0.01, sector_5d_pct=0.02)
    check(s.score > 70, f"strong setup → score >70, got {s.score}")
    check(s.confidence >= 0.9, f"confidence high, got {s.confidence}")
    check(s.dominant_risk == "none", f"no dominant risk, got {s.dominant_risk}")


def test_alpha_ranker_extended():
    print("\n[alpha ranker extension]")
    from .alpha_ranker import score_one
    from .market_scanner import LiquidMover
    m = LiquidMover(
        ticker="EXT", asset_type="stock", last_price=100.0,
        pct_change_5d=0.30, dollar_volume=200.0, atr_pct=0.04,
        above_20dma=True, above_50dma=True, extension_flag=True,
    )
    s = score_one(m, None, regime="neutral", spy_5d_pct=0.01)
    check(s.penalties["extension"] <= -10, f"extension penalty applied, got {s.penalties['extension']}")
    check(s.dominant_risk == "extension", f"dominant_risk extension, got {s.dominant_risk}")


def test_alpha_ranker_regime_overlay():
    print("\n[alpha ranker regime overlay]")
    from .alpha_ranker import score_one
    from .market_scanner import LiquidMover
    from .catalyst_verifier import CatalystResult
    m = LiquidMover(
        ticker="CR", asset_type="stock", last_price=100.0,
        pct_change_5d=0.05, dollar_volume=300.0, atr_pct=0.025,
        above_20dma=True, above_50dma=True, above_200dma=True, spread_pct=0.005,
    )
    cat = CatalystResult(ticker="CR", catalyst_type="earnings_double_beat", catalyst_confidence=0.9)
    s_crisis = score_one(m, cat, regime="crisis", spy_5d_pct=-0.06)
    check(s_crisis.score <= 30, f"crisis caps at 30, got {s_crisis.score}")


def test_sector_sympathy_catalyst():
    print("\n[catalyst sympathy]")
    from . import catalyst_verifier as cv

    orig_quote = cv.finnhub.quote
    orig_verify = cv.verify
    try:
        cv.finnhub.quote = lambda ticker: {"dp": 19.3}

        def fake_verify(ticker, *, days_back=14, allow_sympathy=True):
            if ticker == "NVDA" and not allow_sympathy:
                return cv.CatalystResult(
                    ticker="NVDA",
                    catalyst_type="earnings_double_beat",
                    catalyst_source="finnhub.earnings_calendar",
                    catalyst_date="2026-05-20",
                    catalyst_confidence=0.9,
                )
            return orig_verify(ticker, days_back=days_back, allow_sympathy=allow_sympathy)

        cv.verify = fake_verify
        res = cv._detect_sector_sympathy("MU", days_back=14)
        check(res is not None and res.catalyst_type == "sector_sympathy_confirmed", "MU detects sector sympathy")
    finally:
        cv.finnhub.quote = orig_quote
        cv.verify = orig_verify


def test_universe():
    print("\n[universe]")
    from .universe import etf_universe, is_junk_ticker, sector_etf_for
    u = etf_universe()
    check(len(u) >= 80, f"~100 ETFs in universe, got {len(u)}")
    check("SPY" in u and "QQQ" in u and "XLK" in u, "core ETFs present")
    check(is_junk_ticker("BRK.B") and not is_junk_ticker("AAPL"), "junk filter works")
    check(sector_etf_for("Technology") == "XLK", "sector mapping works")


def test_candidate_pool_dataclass():
    print("\n[candidate pool]")
    from .candidate_gen import CandidatePool
    cp = CandidatePool(ticker="NVDA")
    cp.add_tag("double_beat")
    cp.add_tag("top_gainer")
    cp.add_tag("double_beat")  # dup
    check(cp.source_tags == ["double_beat", "top_gainer"], "tags preserved + deduped")


def test_cache_manager():
    print("\n[cache manager]")
    from . import cache_manager as cm
    cm.put("test_smoke", {"v": 1}, ttl_sec=10)
    got = cm.get("test_smoke")
    check(got == {"v": 1}, "cache put+get roundtrip")
    info = cm.info("test_smoke")
    check(info is not None and info.get("fresh") is True, "cache info shows fresh")


def test_intraday_limit_plan_live_price_and_guard():
    print("\n[intraday alpha]")
    from . import intraday_alpha
    from .market_scanner import LiquidMover
    from .alpha_ranker import AlphaScore

    orig_alpaca = intraday_alpha.alpaca.latest_quote
    orig_finnhub = intraday_alpha.finnhub.quote
    try:
        intraday_alpha.alpaca.latest_quote = lambda ticker: {"bp": 72.9, "ap": 73.1}
        intraday_alpha.finnhub.quote = lambda ticker: {"c": 73.0}
        m = LiquidMover(ticker="OKLO", last_price=78.05, atr_14=3.0, dollar_volume=500.0, spread_pct=0.002)
        s = AlphaScore(ticker="OKLO", score=60)
        plan = intraday_alpha._limit_plan(m, s)
        check(plan.reference_price == 73.0, f"live price used, got {plan.reference_price}")
        check(plan.price_source == "alpaca_mid", f"alpaca mid preferred, got {plan.price_source}")
        check(plan.limit_price is None or plan.limit_price <= 73.0 * 1.01, f"limit sanity respected, got {plan.limit_price}")
    finally:
        intraday_alpha.alpaca.latest_quote = orig_alpaca
        intraday_alpha.finnhub.quote = orig_finnhub


def test_intraday_limit_plan_rejects_bad_limit():
    print("\n[intraday alpha guard]")
    from . import intraday_alpha
    from .market_scanner import LiquidMover
    from .alpha_ranker import AlphaScore

    orig_live = intraday_alpha._live_price
    try:
        intraday_alpha._live_price = lambda ticker, fallback: (73.0, "test_live")
        m = LiquidMover(ticker="BUG", last_price=100.0, atr_14=0.1, dollar_volume=100.0, spread_pct=0.5, high_of_day_distance_pct=0.02, source_tags=['x'])
        s = AlphaScore(ticker="BUG", score=50)
        plan = intraday_alpha._limit_plan(m, s)
        check(plan.acceptable_chase_to is not None and plan.acceptable_chase_to <= 73.0 * 1.02, f"chase is capped near live price, got {plan.acceptable_chase_to}")
    finally:
        intraday_alpha._live_price = orig_live


def test_monday_open_execution_blockers():
    print("\n[monday_open blockers]")
    from . import monday_open

    risk_cfg = {
        "hard_gates": {
            "block_on_unresolved_order_state": True,
            "block_on_broker_api_instability": True,
        }
    }
    blockers = monday_open._execution_blockers(
        risk_cfg=risk_cfg,
        warnings=["alpaca.account failed: boom"],
        open_orders=[{"id": "1"}],
    )
    check(any("open orders" in b for b in blockers), "open orders block execution")
    check(any("broker/data instability" in b for b in blockers), "broker instability blocks execution")


def test_ats_v6_validation():
    print("\n[ats v6]")
    out = ats_v6.validate_to_dict()
    check(out["sample_trade_id"] == "trade-opp-001", "opportunistic trade roundtrip validation")
    check(out["sample_candidate_id"] == "cand-opp-001", "candidate decision roundtrip validation")
    check(out["sample_attribution_trade_id"] == "trade-opp-001", "nightly attribution roundtrip validation")
    check(out["conviction_trade_id"] == "trade-conv-001", "conviction path validation")
    check(out["opportunistic_trade_id"] == "trade-opp-001", "opportunistic path validation")
    check(out["config_summary"]["startup_mode"] == "seed", "startup mode defaults to seed")
    check(out["config_summary"]["portfolio_size_usd"] == 100000, "portfolio size exposed in config layer")
    check(out["config_summary"]["broker_mode"] == "alpaca_paper", "broker mode exposed in config layer")
    check(out["config_summary"]["allowed_instruments"] == ["stocks"], "allowed instruments exposed in config layer")
    check(out["config_summary"]["engine_types"] == ["conviction", "opportunistic"], "engine types exposed in config layer")
    storage_paths = out["config_summary"]["storage_paths"] or {}
    check(storage_paths.get("sqlite_db", "").endswith("sql/ats_v6.db"), "storage paths exposed in config layer")
    alloc = out["config_summary"]["capital_allocation"] or {}
    check(alloc.get("conviction") == 60 and alloc.get("opportunistic") == 30 and alloc.get("cash_reserve") == 10,
          "Aaron capital allocation encoded")
    engine_summary = out.get("report_preview", {}).get("engine_summary", [])
    candidate_summary = out.get("report_preview", {}).get("candidate_decision_summary", [])
    json_probe = out.get("report_preview", {}).get("json_query_probe", {})
    check(any(r.get("engine") == "conviction" for r in engine_summary), "report includes conviction engine")
    check(any(r.get("engine") == "opportunistic" for r in engine_summary), "report includes opportunistic engine")
    check(any(r.get("decision") == "trade" for r in candidate_summary), "report includes candidate decision summary")
    check(json_probe.get("spread_bps") == 8.0, "entry_conditions_json is queryable via json_extract")
    check(json_probe.get("atr_pct") == 0.025, "entry condition ATR is queryable via json_extract")
    check(json_probe.get("has_stale_quote_flag") == 0, "data_quality_flags_json is queryable via json_each")


def test_candidate_decision_replay():
    print("\n[candidate decisions]")
    ats_v6.validate_to_dict()
    out = candidate_decisions.replay_sample_candidate_paths()
    check(out["trade"]["candidate_id"] == "replay-trade-001", "trade decision persisted")
    check(out["watch"]["candidate_id"] == "replay-watch-001", "watch decision persisted")
    check(out["reject"]["candidate_id"] == "replay-reject-001", "reject decision persisted")
    check(out["trade_link"]["eventual_trade_id"] == "trade-opp-001", "eventual trade linked")
    check(out["watch_holdouts"]["fwd_return_7d"] == 0.05, "watch holdout returns persisted")
    check(out["reject_holdouts"]["signal_holdout_30d_pnl"] == -25.0, "reject holdout pnl persisted")
    decisions = {row["candidate_id"]: row["decision"] for row in out["all_rows"]}
    check(decisions.get("replay-trade-001") == "trade", "trade decision listed")
    check(decisions.get("replay-watch-001") == "watch", "watch decision listed")
    check(decisions.get("replay-reject-001") == "reject", "reject decision listed")


def test_sqlite_wal_and_busy_timeout():
    print("\n[sqlite policy]")
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "ats_v6_test.db"
        ats_v6.initialize_database(db_path)

        with db.connect(db_path) as conn:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

        check(str(journal_mode).lower() == "wal", f"journal mode is WAL, got {journal_mode}")
        check(int(busy_timeout) >= 30000, f"busy timeout configured, got {busy_timeout}")


def test_sqlite_writer_waits_instead_of_immediate_lock_failure():
    print("\n[sqlite contention]")
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "ats_v6_lock_test.db"
        ats_v6.initialize_database(db_path)

        acquired = threading.Event()
        release = threading.Event()
        writer_done = threading.Event()
        outcome: dict[str, object] = {}

        def hold_write_lock() -> None:
            with db.connect(db_path, write=True) as conn:
                acquired.set()
                release.wait(timeout=2.0)
                conn.commit()

        def contending_writer() -> None:
            acquired.wait(timeout=1.0)
            started = time.monotonic()
            try:
                with db.connect(db_path, write=True) as conn:
                    conn.execute("SELECT 1")
                    conn.commit()
                outcome["waited"] = time.monotonic() - started
                outcome["ok"] = True
            except sqlite3.OperationalError as exc:
                outcome["error"] = str(exc)
                outcome["ok"] = False
            finally:
                writer_done.set()

        t1 = threading.Thread(target=hold_write_lock)
        t2 = threading.Thread(target=contending_writer)
        t1.start()
        t2.start()

        check(acquired.wait(timeout=1.0), "primary writer acquired lock")
        time.sleep(0.2)
        release.set()
        check(writer_done.wait(timeout=2.0), "contending writer completed after lock release")
        t1.join(timeout=1.0)
        t2.join(timeout=1.0)

        check(outcome.get("ok") is True, f"contending writer succeeded: {outcome}")
        check(float(outcome.get("waited") or 0.0) >= 0.15, f"writer waited for lock release, got {outcome.get('waited')}")


def main():
    for fn in (
        test_regime, test_setup_classifier, test_scoring_buy_ready,
        test_scoring_extension_demote, test_scoring_no_catalyst,
        test_scoring_crisis_overlay, test_scoring_incomplete,
        test_vol_efficiency_bounds,
        test_alpha_ranker, test_alpha_ranker_extended, test_alpha_ranker_regime_overlay,
        test_sector_sympathy_catalyst,
        test_universe, test_candidate_pool_dataclass, test_cache_manager,
        test_intraday_limit_plan_live_price_and_guard, test_intraday_limit_plan_rejects_bad_limit,
        test_monday_open_execution_blockers,
        test_ats_v6_validation, test_candidate_decision_replay,
        test_sqlite_wal_and_busy_timeout, test_sqlite_writer_waits_instead_of_immediate_lock_failure,
    ):
        try:
            fn()
        except Exception as e:
            FAILED.append(f"{fn.__name__} threw {e}")
            traceback.print_exc()
    print()
    if FAILED:
        print(f"FAILED ({len(FAILED)}):")
        for f in FAILED:
            print(f"  {f}")
        sys.exit(1)
    print("All tests passed.")


if __name__ == "__main__":
    main()
