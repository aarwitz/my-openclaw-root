"""Phase II CLI — `python -m phase2.cli <command> [args]`."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import (
    candidate_gen,
    candidate_decisions,
    cache_manager,
    catalyst_verifier,
    checkpoints,
    decision_journal,
    intraday_alpha,
    event_alpha,
    market_scanner,
    x_monitor,
    normalize as norm,
    regime as regime_mod,
    replay,
    scan_market as sm,
    ats_v6,
)
from . import monday_open as mo
from .adapters import sheets


def _emit(payload, fmt: str = "json"):
    if fmt == "text":
        if isinstance(payload, str):
            print(payload)
        else:
            print(json.dumps(payload, indent=2, default=str))
    else:
        print(json.dumps(payload, indent=2, default=str))


def cmd_regime(args):
    rg = regime_mod.compute()
    _emit(asdict(rg), args.format)


def cmd_normalize(args):
    rg = None
    if not args.no_regime:
        rg = regime_mod.compute()
    r = norm.normalize(
        args.ticker,
        date=args.date,
        regime=rg,
        include_alpaca_live=args.live,
        nav_usd=args.nav,
    )
    if args.write_sheet:
        result = sheets.upsert_candidate(r.as_dict())
        out = {"record": r.as_dict(), "sheet": result}
    else:
        out = r.as_dict()
    _emit(out, args.format)


def cmd_candidates(args):
    seed = args.seed.split(",") if args.seed else None
    universe = candidate_gen.generate(seed=seed, days_back=args.days_back, max_tickers=args.max)
    out: list = []
    rg = regime_mod.compute() if not args.no_regime else None
    for t in universe:
        try:
            r = norm.normalize(t, date=args.date, regime=rg, include_alpaca_live=args.live, nav_usd=args.nav)
            if args.write_sheet:
                sheets.upsert_candidate(r.as_dict())
            out.append({
                "ticker": r.ticker, "class": r.recommendation_class,
                "score": r.total_score_final, "setup": r.setup_state,
                "catalyst": r.verified_catalyst_type,
            })
        except Exception as e:
            out.append({"ticker": t, "error": str(e)})
    _emit(sorted(out, key=lambda x: -x.get("score", 0) if isinstance(x.get("score"), (int, float)) else 0), args.format)


def cmd_monday_open(args):
    rep = mo.run(
        place_orders=args.place_orders,
        write_sheet=not args.no_sheet,
        seed=args.seed.split(",") if args.seed else None,
        max_universe=args.max,
    )
    if args.format == "text":
        print(mo.report_to_text(rep))
    else:
        _emit(asdict(rep))


def cmd_outcomes(args):
    rows = replay.fill_outcomes(as_of=args.as_of, write_sheet=not args.no_sheet)
    _emit([r.as_sheet_dict() for r in rows], args.format)


def cmd_replay(args):
    rows = sheets.read_candidates_since(args.since)
    out = []
    for r in rows:
        new = replay.recompute_score_for(r)
        out.append({
            "date": r.get("date"), "ticker": r.get("ticker"),
            "old_class": r.get("recommendation_class"),
            "new_class": new.get("recommendation_class"),
            "old_score": r.get("total_score_final"),
            "new_score": new.get("total_score_final"),
        })
    _emit(out, args.format)


# ----------- new MVP commands -----------

def cmd_scan_market(args):
    """Top-N liquid US stocks/ETFs ranked for next-week SPY outperformance."""
    seed = args.seed.split(",") if args.seed else None
    use_cache = not args.no_cache
    key = f"market_scan_top{args.top}_ipo{int(args.include_ipos)}"

    def _do():
        return sm.run(seed=seed, top_n=args.top, include_ipos=args.include_ipos,
                      verify_catalysts=not args.skip_catalysts)

    report = cache_manager.get_or_compute(key, ttl_sec=cache_manager.REFRESH_CADENCE_SEC["market_scan"], fn=_do) \
        if use_cache else _do()

    if args.snapshot:
        decision_journal.snapshot_candidates(report, label="scan-market")

    if args.format == "text":
        print(sm.format_text(report, n=args.top))
    else:
        _emit(report)


def cmd_rank_weekly_alpha(args):
    """Alias for scan-market with focus on alpha ranking only."""
    cmd_scan_market(args)


def cmd_sector_leaders(args):
    """Sector ETF leaderboard over last 5d."""
    movers = market_scanner.scan_market(include_etfs=True, include_unusual_vol=False, max_total=80)
    leaders = market_scanner.sector_etf_leaders(movers, n=11)
    out = [m.as_dict() for m in leaders]
    if args.format == "text":
        print("Sector ETF leaders (5d):")
        for m in leaders:
            print(f"  {m.ticker:<5}  5d={(m.pct_change_5d or 0)*100:>+5.1f}%  "
                  f"$vol={m.dollar_volume or 0:>6.0f}M")
    else:
        _emit(out)


def cmd_ipo_movers(args):
    """Recently-listed names with meaningful liquidity."""
    movers = market_scanner.scan_market(include_etfs=False, include_unusual_vol=True, max_total=300)
    ipos = market_scanner.recent_ipo_movers(movers, n=args.top)
    out = [m.as_dict() for m in ipos]
    if args.format == "text":
        print(f"Recent IPO movers ({len(out)}):")
        for m in ipos:
            print(f"  {m.ticker:<6}  age={m.days_since_ipo}d  "
                  f"5d={(m.pct_change_5d or 0)*100:>+5.1f}%  $vol={m.dollar_volume or 0:>6.0f}M")
    else:
        _emit(out)


def cmd_verify_catalyst(args):
    """Run Finnhub catalyst verification for one or more tickers."""
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if len(tickers) == 1:
        result = catalyst_verifier.verify(tickers[0])
        _emit(result.as_dict(), args.format)
    else:
        results = catalyst_verifier.verify_many(tickers)
        _emit({t: r.as_dict() for t, r in results.items()}, args.format)


def cmd_cache(args):
    """Inspect or clear intraday cache."""
    if args.clear:
        from pathlib import Path
        for p in (cache_manager.CACHE_DIR.glob("*.json") if cache_manager.CACHE_DIR.exists() else []):
            p.unlink()
        print("cache cleared")
        return
    info = cache_manager.list_all()
    _emit(info, args.format)


def cmd_x_monitor(args):
    accounts = [a.strip() for a in args.accounts.split(',')] if args.accounts else None
    out = x_monitor.fetch_events(accounts=accounts, limit_per_account=args.limit)
    _emit(out, args.format)


def cmd_event_alpha(args):
    accounts = [a.strip() for a in args.accounts.split(',')] if args.accounts else None
    out = event_alpha.generate_event_alpha(accounts=accounts, limit_per_account=args.limit)
    _emit(out, args.format)


def cmd_intraday_alpha(args):
    report = intraday_alpha.run(
        objective=args.objective,
        cash_hurdle_apr=args.cash_hurdle_apr,
        extra_seed=args.seed.split(",") if args.seed else None,
        use_cache=not args.no_cache,
        max_total=args.max_total,
    )
    _emit(report, args.format)


def cmd_checkpoint(args):
    result = checkpoints.run_checkpoint(
        args.name,
        create_intents=not args.no_intents,
        max_replacements=args.max_replacements,
        max_rotations=args.max_rotations,
        scan_limit=args.scan_limit,
    )
    if args.format == "text":
        print(checkpoints.result_to_text(result))
    else:
        _emit(asdict(result))


def cmd_validate_ats_v6(args):
    out = ats_v6.validate_to_dict()
    _emit(out, args.format)


def cmd_replay_candidate_decisions(args):
    out = candidate_decisions.replay_sample_candidate_paths()
    _emit(out, args.format)



def main(argv=None):
    p = argparse.ArgumentParser(prog="phase2")
    p.add_argument("--format", choices=("json", "text"), default="json")
    sp = p.add_subparsers(dest="cmd", required=True)

    sp_r = sp.add_parser("regime")
    sp_r.set_defaults(fn=cmd_regime)

    sp_n = sp.add_parser("normalize")
    sp_n.add_argument("--ticker", required=True)
    sp_n.add_argument("--date")
    sp_n.add_argument("--no-regime", action="store_true")
    sp_n.add_argument("--live", action="store_true", help="include Alpaca live quote")
    sp_n.add_argument("--nav", type=float, default=None)
    sp_n.add_argument("--write-sheet", action="store_true")
    sp_n.set_defaults(fn=cmd_normalize)

    sp_c = sp.add_parser("candidates")
    sp_c.add_argument("--date")
    sp_c.add_argument("--seed")
    sp_c.add_argument("--days-back", type=int, default=10)
    sp_c.add_argument("--max", type=int, default=25)
    sp_c.add_argument("--live", action="store_true")
    sp_c.add_argument("--nav", type=float, default=None)
    sp_c.add_argument("--no-regime", action="store_true")
    sp_c.add_argument("--write-sheet", action="store_true")
    sp_c.set_defaults(fn=cmd_candidates)

    sp_m = sp.add_parser("monday-open")
    sp_m.add_argument("--place-orders", action="store_true")
    sp_m.add_argument("--no-sheet", action="store_true")
    sp_m.add_argument("--seed")
    sp_m.add_argument("--max", type=int, default=25)
    sp_m.set_defaults(fn=cmd_monday_open)

    sp_o = sp.add_parser("outcomes")
    sp_o.add_argument("--as-of")
    sp_o.add_argument("--no-sheet", action="store_true")
    sp_o.set_defaults(fn=cmd_outcomes)

    sp_re = sp.add_parser("replay")
    sp_re.add_argument("--since", required=True, help="YYYY-MM-DD")
    sp_re.set_defaults(fn=cmd_replay)

    # ----- new MVP commands -----
    sp_sm = sp.add_parser("scan-market", help="Top-N liquid US names ranked for next-week SPY beat")
    sp_sm.add_argument("--top", type=int, default=50)
    sp_sm.add_argument("--seed", help="comma-separated extra seed tickers")
    sp_sm.add_argument("--include-ipos", action="store_true")
    sp_sm.add_argument("--skip-catalysts", action="store_true",
                       help="skip Finnhub catalyst verification (faster, no catalyst tags)")
    sp_sm.add_argument("--no-cache", action="store_true")
    sp_sm.add_argument("--snapshot", action="store_true",
                       help="write candidate-snapshot JSON to phase2/logs/")
    sp_sm.set_defaults(fn=cmd_scan_market)

    sp_rwa = sp.add_parser("rank-weekly-alpha", help="Alias for scan-market")
    sp_rwa.add_argument("--top", type=int, default=50)
    sp_rwa.add_argument("--seed")
    sp_rwa.add_argument("--include-ipos", action="store_true")
    sp_rwa.add_argument("--skip-catalysts", action="store_true")
    sp_rwa.add_argument("--no-cache", action="store_true")
    sp_rwa.add_argument("--snapshot", action="store_true")
    sp_rwa.set_defaults(fn=cmd_rank_weekly_alpha)

    sp_sl = sp.add_parser("sector-leaders", help="Sector ETF 5d leaderboard")
    sp_sl.set_defaults(fn=cmd_sector_leaders)

    sp_ipo = sp.add_parser("ipo-movers", help="Recently-listed names with liquidity")
    sp_ipo.add_argument("--top", type=int, default=15)
    sp_ipo.set_defaults(fn=cmd_ipo_movers)

    sp_vc = sp.add_parser("verify-catalyst", help="Finnhub catalyst check for ticker(s)")
    sp_vc.add_argument("--tickers", required=True, help="comma-separated")
    sp_vc.set_defaults(fn=cmd_verify_catalyst)

    sp_ca = sp.add_parser("cache", help="Inspect / clear intraday cache")
    sp_ca.add_argument("--clear", action="store_true")
    sp_ca.set_defaults(fn=cmd_cache)

    sp_ia = sp.add_parser("intraday-alpha", help="Portfolio-aware weekly alpha ranking with execution plans")
    sp_ia.add_argument("--objective", default="beat_spy_this_week")
    sp_ia.add_argument("--cash-hurdle-apr", type=float, default=0.03)
    sp_ia.add_argument("--seed")
    sp_ia.add_argument("--max-total", type=int, default=120)
    sp_ia.add_argument("--no-cache", action="store_true")
    sp_ia.set_defaults(fn=cmd_intraday_alpha)

    sp_cp = sp.add_parser("checkpoint", help="Run one deterministic checkpoint and persist state")
    sp_cp.add_argument("--name", required=True, choices=("preopen_0900", "morning_1100", "rerank_1330", "close_1530"))
    sp_cp.add_argument("--no-intents", action="store_true")
    sp_cp.add_argument("--max-replacements", type=int, default=5)
    sp_cp.add_argument("--max-rotations", type=int, default=3)
    sp_cp.add_argument("--scan-limit", type=int, default=30)
    sp_cp.set_defaults(fn=cmd_checkpoint)

    sp_x = sp.add_parser("x-monitor", help="Fetch and normalize X posts for trading signals")
    sp_x.add_argument("--accounts", help="comma-separated usernames")
    sp_x.add_argument("--limit", type=int, default=5)
    sp_x.set_defaults(fn=cmd_x_monitor)

    sp_ea = sp.add_parser("event-alpha", help="Convert elite-source events into theme baskets and preferred expressions")
    sp_ea.add_argument("--accounts", help="comma-separated usernames")
    sp_ea.add_argument("--limit", type=int, default=3)
    sp_ea.set_defaults(fn=cmd_event_alpha)

    sp_v6 = sp.add_parser("validate-ats-v6", help="Initialize and validate ATS v6 SQLite/config foundation")
    sp_v6.set_defaults(fn=cmd_validate_ats_v6)

    sp_cd = sp.add_parser("replay-candidate-decisions", help="Replay sample trade/watch/reject candidate decision paths")
    sp_cd.set_defaults(fn=cmd_replay_candidate_decisions)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main(sys.argv[1:])
