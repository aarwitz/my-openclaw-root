#!/usr/bin/env python3
"""Archivist · market_debrief.py — structural capture of what moved and why.

Writes one `market_events` row per trading day (or ad-hoc event) so the system
learns even on no-trade days. The canonical example this exists to capture is
the 2026-06-05 session: a hot jobs report repriced the rate path, real yields
rose, and high-multiple tech/AI/chip names sold off violently — compounded by
an Iran/oil inflation impulse. That lesson belongs in the world model whether
or not we had a position on.

Division of labour (determinism-first):
  - The numbers (index moves, our day P&L, exposure alignment) are pulled from
    connectors + portfolio_snapshots by THIS script.
  - The judgement (headline, catalyst class, surprise framing, the concise
    lesson, which mechanisms were exercised) is authored by the Archivist LLM
    and passed in as flags.

When `--mechanisms id:outcome[:weight]` is supplied, the script appends one
`mechanism_observations` row (source_type='market_event') per mechanism, so a
day's price action becomes Beta evidence that `calibrate.py` folds into the
posterior on its next pass — even with zero open trades.

Usage:
    python3 market_debrief.py \
        --headline "Hot jobs print → higher-for-longer → tech/AI/chip selloff" \
        --catalyst-class macro_release \
        --surprise "NFP well above consensus; cuts priced out" \
        --lesson "Labour upside surprises lift real yields and compress long-duration tech multiples; pair with oil to gauge inflation persistence." \
        --mechanisms mech_jobs_duration_tech:hit,mech_oil_inflation_rates:hit \
        --sources '["https://bls.gov/...","https://eia.gov/..."]'
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
try:
    from connectors.marketdata import ConnectorError, daily_bars  # noqa: E402
except Exception:  # pragma: no cover - connectors optional in dry contexts
    ConnectorError = Exception  # type: ignore

    def daily_bars(*_a, **_k):  # type: ignore
        raise ConnectorError("connectors unavailable")

import theme_model as tm  # noqa: E402

EXPERIMENT_DEFAULT = "world_model_v1"
INDEX_PROXIES = ("SPY", "QQQ")
VALID_OUTCOMES = ("hit", "miss", "partial")
EXIT_OK = 0
EXIT_FAIL_LOUD = 2


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"trading-intel DB missing at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _regime_as_of(conn, event_date: str) -> str:
    row = conn.execute(
        "SELECT current FROM regime WHERE determined_at < ? "
        "ORDER BY determined_at DESC LIMIT 1",
        (event_date[:10] + "T23:59:59Z",),
    ).fetchone()
    return row["current"] if row else "unknown"


def _move_as_of(bars: list[dict], event_date: str) -> float | None:
    """Close-to-close percent move ending on ``event_date``.

    Backfills must never use the newest two bars: that silently writes today's
    market under an old event date.
    """
    eligible = [
        bar for bar in bars
        if str(bar.get("t") or "")[:10] <= event_date[:10] and bar.get("c") is not None
    ]
    if len(eligible) < 2 or str(eligible[-1].get("t") or "")[:10] != event_date[:10]:
        return None
    prev, last = float(eligible[-2]["c"]), float(eligible[-1]["c"])
    return round((last - prev) / prev * 100.0, 3) if prev else None


def _index_moves(event_date: str) -> dict:
    moves: dict[str, float] = {}
    for sym in INDEX_PROXIES:
        try:
            bars = daily_bars(sym, days=260)
        except ConnectorError:
            continue
        move = _move_as_of(bars, event_date)
        if move is not None:
            moves[sym] = move
    return moves


def _tracked_big_moves(
    conn,
    event_date: str,
    min_abs_pct: float = 2.0,
    cap: int = 12,
) -> dict:
    """Deterministic single-name moves for the tracked universe (book + recent ideas).

    Single-name moves used to arrive ONLY via the optional --moves flag, i.e. an LLM
    remembering to pass numbers on a CLI — which silently stopped after 2026-07-17 and
    starved big-story coverage + event decomposition (invariant #1: numbers come from
    scripts). Now computed from bars for every tracked name, biggest first.
    """
    names: set[str] = set()
    try:
        for r in conn.execute(
            "SELECT DISTINCT UPPER(ticker) t FROM positions "
            "WHERE opened_at < ? AND (closed_at IS NULL OR closed_at >= ?)",
            (event_date[:10] + "T23:59:59Z", event_date[:10] + "T00:00:00Z"),
        ):
            if r["t"]:
                names.add(r["t"])
        for r in conn.execute(
            "SELECT tickers FROM hypotheses WHERE created_at <= ? "
            "AND created_at >= datetime(?,'-30 days')",
            (event_date[:10] + "T23:59:59Z", event_date[:10]),
        ):
            try:
                names.update(str(t).upper() for t in json.loads(r["tickers"] or "[]"))
            except (ValueError, TypeError):
                continue
    except sqlite3.Error:
        return {}
    moves: dict[str, float] = {}
    for sym in sorted(names - set(INDEX_PROXIES)):
        try:
            bars = daily_bars(sym, days=260)
        except (ConnectorError, Exception):
            continue
        move = _move_as_of(bars, event_date)
        if move is not None and abs(move) >= min_abs_pct:
            moves[sym] = move
    return dict(sorted(moves.items(), key=lambda kv: -abs(kv[1]))[:cap])


def _snapshot_for_date(conn, event_date: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT day_pl, equity FROM portfolio_snapshots "
        "WHERE substr(captured_at,1,10)=? ORDER BY captured_at DESC LIMIT 1",
        (event_date[:10],),
    ).fetchone()


def _infer_alignment(day_pl: float | None) -> str | None:
    if day_pl is None:
        return None
    if abs(day_pl) < 1e-6:
        return "flat"
    return "benefited" if day_pl > 0 else "suffered"


def _parse_mechanisms(raw: str | None) -> list[tuple[str, str, float]]:
    out: list[tuple[str, str, float]] = []
    if not raw:
        return out
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        parts = token.split(":")
        mid = parts[0].strip()
        outcome = (parts[1].strip() if len(parts) > 1 else "hit").lower()
        weight = float(parts[2]) if len(parts) > 2 else 1.0
        if outcome not in VALID_OUTCOMES:
            raise ValueError(f"bad outcome '{outcome}' for {mid} "
                             f"(expected one of {VALID_OUTCOMES})")
        out.append((mid, outcome, weight))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", default=_today())
    p.add_argument("--headline", required=True)
    p.add_argument("--catalyst-class", required=True,
                   choices=("macro_release", "geopolitical", "earnings",
                            "policy", "liquidity", "technical", "other"))
    p.add_argument("--surprise", default=None)
    p.add_argument("--lesson", default=None, help="concise lesson, <=800 chars")
    p.add_argument("--moves", default=None,
                   help="JSON object of extra observed moves to merge, e.g. "
                        "'{\"NVDA\":-5.1,\"AVGO\":-4.2}'")
    p.add_argument("--mechanisms", default=None,
                   help="comma list id:outcome[:weight] (outcome hit|miss|partial)")
    p.add_argument("--themes", default=None,
                   help="comma list theme-id[:support|contradict|mixed|context]")
    p.add_argument("--alignment", default=None,
                   choices=("benefited", "suffered", "neutral", "flat"))
    p.add_argument("--sources", default=None, help="JSON array of source URLs/refs")
    p.add_argument("--experiment-id", default=EXPERIMENT_DEFAULT)
    p.add_argument(
        "--allow-additional-event",
        action="store_true",
        help="permit a second event row for the same date (default enforces one daily debrief)",
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    try:
        mechs = _parse_mechanisms(args.mechanisms)
        themes = tm.parse_theme_tags(args.themes)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return EXIT_FAIL_LOUD

    extra_moves: dict = {}
    if args.moves:
        try:
            extra_moves = json.loads(args.moves)
        except json.JSONDecodeError as exc:
            print(json.dumps({"error": f"bad --moves JSON: {exc}"}), file=sys.stderr)
            return EXIT_FAIL_LOUD
    sources = []
    if args.sources:
        try:
            sources = json.loads(args.sources)
        except json.JSONDecodeError as exc:
            print(json.dumps({"error": f"bad --sources JSON: {exc}"}), file=sys.stderr)
            return EXIT_FAIL_LOUD

    if args.lesson and len(args.lesson) > 800:
        print(json.dumps({"error": "lesson exceeds 800 chars"}), file=sys.stderr)
        return EXIT_FAIL_LOUD

    try:
        conn = _connect()
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return EXIT_FAIL_LOUD

    existing = conn.execute(
        "SELECT id FROM market_events WHERE event_date=? LIMIT 1", (args.date[:10],)
    ).fetchone()
    if existing and not args.allow_additional_event:
        print(json.dumps({
            "error": f"market debrief already exists for {args.date[:10]}",
            "existing_event_id": existing["id"],
        }), file=sys.stderr)
        return EXIT_FAIL_LOUD

    observed_moves = _index_moves(args.date)
    observed_moves.update(_tracked_big_moves(conn, args.date))
    observed_moves.update(extra_moves)                # LLM extras may ADD, never required

    snap = _snapshot_for_date(conn, args.date)
    day_pl = float(snap["day_pl"]) if snap and snap["day_pl"] is not None else None
    alignment = args.alignment or _infer_alignment(day_pl)
    regime = _regime_as_of(conn, args.date)

    event_id = "mev-" + uuid.uuid4().hex[:20]
    attributed = [m[0] for m in mechs]

    if args.dry_run:
        print(json.dumps({
            "dry_run": True, "event_id": event_id, "event_date": args.date,
            "observed_moves": observed_moves, "our_pnl_that_day": day_pl,
            "exposure_alignment": alignment, "regime": regime,
            "attributed_mechanisms": attributed,
            "observations_would_emit": len(mechs),
            "theme_observations_would_emit": len(themes),
        }, indent=2))
        return EXIT_OK

    conn.execute(
        "INSERT INTO market_events (id, event_date, created_at, created_by, headline, "
        "catalyst_class, observed_moves_json, surprise_vs_expectation, "
        "attributed_mechanism_ids_json, our_pnl_that_day, our_exposure_alignment, "
        "lesson_concise, primary_source_refs_json, experiment_id) "
        "VALUES (?, ?, ?, 'archivist', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, args.date, _now_iso(), args.headline, args.catalyst_class,
         json.dumps(observed_moves), args.surprise, json.dumps(attributed),
         day_pl, alignment, args.lesson, json.dumps(sources), args.experiment_id),
    )

    obs_emitted = 0
    for mid, outcome, weight in mechs:
        exists = conn.execute("SELECT 1 FROM mechanisms WHERE id=?", (mid,)).fetchone()
        if not exists:
            print(json.dumps({"error": f"unknown mechanism '{mid}'"}), file=sys.stderr)
            conn.rollback()
            return EXIT_FAIL_LOUD
        oid = "mobs-" + uuid.uuid4().hex[:20]
        conn.execute(
            "INSERT INTO mechanism_observations (id, mechanism_id, observed_at, "
            "source_type, source_id, outcome, weight, regime_at_obs, notes, "
            "experiment_id) VALUES (?, ?, ?, 'market_event', ?, ?, ?, ?, ?, ?)",
            (oid, mid, _now_iso(), event_id, outcome, weight, regime,
             f"market_debrief {args.date}: {args.headline[:120]}", args.experiment_id),
        )
        obs_emitted += 1

    theme_obs_emitted = 0
    for theme_id, outcome in themes:
        emitted = tm.append_observation(
            conn, theme_id=theme_id, source_type="market_event",
            source_id=event_id, outcome=outcome, as_of=args.date,
            evidence={"event_id": event_id, "event_date": args.date,
                      "headline": args.headline,
                      "observed_moves": observed_moves},
            notes=f"market_debrief {args.date}: {args.headline[:180]}",
        )
        theme_obs_emitted += int(emitted)

    aid = "AUDIT-" + _now_iso().replace(":", "").replace("-", "") + "-" + event_id[:20]
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise, experiment_id) "
        "VALUES (?, ?, 'archivist', 'market_event', ?, 'market_debrief', NULL, ?, ?, ?)",
        (aid, _now_iso(), event_id, args.catalyst_class,
         f"{args.headline[:200]} | obs+={obs_emitted}", args.experiment_id),
    )

    conn.commit()
    print(json.dumps({
        "event_id": event_id, "event_date": args.date,
        "observed_moves": observed_moves, "our_pnl_that_day": day_pl,
        "exposure_alignment": alignment, "regime": regime,
        "attributed_mechanisms": attributed, "observations_emitted": obs_emitted,
        "theme_observations_emitted": theme_obs_emitted,
    }, indent=2))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
