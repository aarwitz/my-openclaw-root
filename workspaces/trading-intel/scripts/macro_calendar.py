#!/usr/bin/env python3
"""Macro release calendar + surprise ledger (the 'see it coming' layer).

Three jobs:
  seed       — populate the forward calendar of high-impact scheduled releases
               from the published BLS calendar. Unknown dates fail closed;
               calendar arithmetic is never presented as an official date.
  upcoming   — list scheduled releases in the next N days, each annotated with
               the mechanism it tends to fire — so the desk pre-positions
               duration/risk BEFORE the print and can pull matching episodes.
  pull-actuals — for past scheduled releases, fetch the actual from FRED
               (keyless), compute the surprise vs prior/consensus, set the
               rate-path lean, and on a LARGE surprise write a market_event +
               mechanism_observation so the world model learns the macro link.

Free + deterministic only (FRED actuals, reviewed BLS release calendar). No browser.

Usage:
  python3 macro_calendar.py seed [--months 3]
  python3 macro_calendar.py upcoming [--days 10] [--json]
  python3 macro_calendar.py pull-actuals [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/developer/scripts")
from developer_db import audit, connect, emit, now_iso  # noqa: E402
from connectors._http import ConnectorError  # noqa: E402
from connectors.fred import fetch_series  # noqa: E402

EXPERIMENT_ID = "macro_calendar_bls_v2"
BLS_SCHEDULE_SOURCE = "https://www.bls.gov/schedule/2026/home.htm"

# Release-month dates copied from the official BLS 2026 calendar. Employment
# Situation carries both NFP and UNRATE. Keep this explicit and reviewable:
# holidays and special scheduling make "first Friday" / "second Wednesday"
# false (2026-07 employment was Thursday 07-02; 2026-09 CPI is Friday 09-11).
_BLS_2026 = {
    "employment": {
        1: 9, 2: 6, 3: 6, 4: 3, 5: 8, 6: 5,
        7: 2, 8: 7, 9: 4, 10: 2, 11: 6, 12: 4,
    },
    "cpi": {
        1: 13, 2: 13, 3: 11, 4: 10, 5: 12, 6: 10,
        7: 14, 8: 12, 9: 11, 10: 14, 11: 10, 12: 10,
    },
}

# Release definitions. mechanisms = the world-model links a surprise tends to fire.
RELEASES = {
    "NFP": {
        "label": "Nonfarm payrolls (jobs report)",
        "fred": "PAYEMS",
        "kind": "mom_change_k",        # headline = month-over-month change, thousands
        "mechanisms": ["mech_jobs_duration_tech", "mech_dovish_surprise_growth"],
        "surprise_hot_is": "hawkish",  # an upside jobs surprise lifts the rate path
        "big_surprise": 75.0,          # |actual - prior| in thousands that counts as a shock
    },
    "CPI_YOY": {
        "label": "CPI inflation (year-over-year %)",
        "fred": "CPIAUCSL",
        "kind": "yoy_pct",
        "mechanisms": ["mech_oil_inflation_rates", "mech_dovish_surprise_growth"],
        "surprise_hot_is": "hawkish",  # hot inflation = fewer cuts = hawkish
        "big_surprise": 0.2,           # YoY pct points
    },
    "UNRATE": {
        "label": "Unemployment rate (%)",
        "fred": "UNRATE",
        "kind": "level",
        "mechanisms": ["mech_jobs_duration_tech"],
        "surprise_hot_is": "dovish",   # a higher jobless rate is dovish (more cuts)
        "big_surprise": 0.2,
    },
}


def _official_release_day(series: str, y: int, m: int) -> date | None:
    if y != 2026:
        return None
    family = "cpi" if series == "CPI_YOY" else "employment"
    day = _BLS_2026[family].get(m)
    return date(y, m, day) if day else None


def _months_ahead(n: int) -> list[tuple[int, int]]:
    out = []
    today = datetime.now(timezone.utc).date()
    y, m = today.year, today.month
    for _ in range(n + 1):
        out.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def seed(conn, months: int) -> dict:
    """Populate scheduled rows. The release on date D reports the PRIOR month's
    data (NFP first Friday of June -> May data)."""
    added = 0
    corrected = []
    missing_official_schedule = []
    for (y, m) in _months_ahead(months):
        # period reported = the month before the release month
        py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
        period = f"{py:04d}-{pm:02d}"
        plan = []
        for series in ("NFP", "CPI_YOY", "UNRATE"):
            rel_date = _official_release_day(series, y, m)
            if rel_date is None:
                missing_official_schedule.append(f"{series}:{y:04d}-{m:02d}")
            else:
                plan.append((series, rel_date))
        for series, rel_date in plan:
            spec = RELEASES[series]
            rid = f"MAC-{series}-{rel_date.isoformat()}"
            existing = conn.execute(
                "SELECT id,release_date FROM macro_releases WHERE series=? AND period=? "
                "ORDER BY created_at DESC LIMIT 1",
                (series, period),
            ).fetchone()
            official_ts = rel_date.isoformat() + "T12:30:00Z"
            if existing and (
                existing["id"] != rid or existing["release_date"] != official_ts
            ):
                old_id, old_date = existing["id"], existing["release_date"]
                conn.execute(
                    "UPDATE macro_releases SET id=?,release_date=?,notes=?,updated_at=?,"
                    "experiment_id=? WHERE id=?",
                    (rid, official_ts, f"official BLS schedule: {BLS_SCHEDULE_SOURCE}",
                     now_iso(), EXPERIMENT_ID, old_id),
                )
                event_id = f"mev-macro-{series}-{period}"
                conn.execute(
                    "UPDATE market_events SET event_date=? WHERE id=?",
                    (rel_date.isoformat(), event_id),
                )
                conn.execute(
                    "UPDATE mechanism_observations SET observed_at=? WHERE source_id=?",
                    (official_ts, event_id),
                )
                audit(
                    conn, actor="developer", entity_type="macro_release", entity_id=rid,
                    action="correct_official_release_date",
                    rationale=(f"{series} {period}: {old_date} -> {official_ts}; "
                               f"source={BLS_SCHEDULE_SOURCE}"),
                    experiment_id=EXPERIMENT_ID,
                )
                corrected.append({"series": series, "period": period,
                                  "from": old_date, "to": official_ts})
                existing = conn.execute(
                    "SELECT id,release_date FROM macro_releases WHERE id=?", (rid,)
                ).fetchone()
            exists = bool(existing) or bool(
                conn.execute("SELECT 1 FROM macro_releases WHERE id=?", (rid,)).fetchone()
            )
            if exists:
                conn.execute(
                    "UPDATE macro_releases SET notes=?,experiment_id=? WHERE id=?",
                    (f"official BLS schedule: {BLS_SCHEDULE_SOURCE}", EXPERIMENT_ID, rid),
                )
                continue
            conn.execute(
                "INSERT INTO macro_releases (id, series, label, release_date, period, status, "
                "impact, fred_series_id, linked_mechanism_ids_json, notes, created_at, experiment_id) "
                "VALUES (?, ?, ?, ?, ?, 'scheduled', 'high', ?, ?, ?, ?, ?)",
                (rid, series, spec["label"], official_ts, period,
                 spec["fred"], json.dumps(spec["mechanisms"]),
                 f"official BLS schedule: {BLS_SCHEDULE_SOURCE}",
                 now_iso(), EXPERIMENT_ID),
            )
            added += 1
    conn.commit()
    return {
        "scheduled_added": added,
        "corrected_dates": corrected,
        "missing_official_schedule": missing_official_schedule,
        "schedule_source": BLS_SCHEDULE_SOURCE,
    }


def upcoming(conn, days: int) -> dict:
    horizon = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = now_iso()
    rows = conn.execute(
        "SELECT * FROM macro_releases WHERE status='scheduled' AND release_date >= ? AND release_date <= ? "
        "ORDER BY release_date",
        (now, horizon),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        out.append({
            "id": d["id"],
            "series": d["series"],
            "label": d["label"],
            "release_date": d["release_date"],
            "period": d["period"],
            "impact": d["impact"],
            "mechanisms": json.loads(d.get("linked_mechanism_ids_json") or "[]"),
            "prep": f"retrieve_episodes.py --mechanism {json.loads(d.get('linked_mechanism_ids_json') or '[]')[0]} "
                    f"--as-of {now[:10]}" if d.get("linked_mechanism_ids_json") else None,
        })
    return {"now": now, "horizon_days": days, "count": len(out), "releases": out}


def _actual_for(series: str, period: str):
    """Return (actual_value, prior_value, surprise_basis_value_list) from FRED for
    the given period (YYYY-MM). Raises ConnectorError if unavailable."""
    spec = RELEASES[series]
    ser = fetch_series(spec["fred"])  # [(date, value)], oldest first
    by_month = {d[:7]: v for d, v in ser}
    if period not in by_month:
        raise ConnectorError(f"{spec['fred']}: no observation for {period}")
    kind = spec["kind"]
    py, pm = period.split("-")
    prev_period = f"{int(py)-1 if pm=='01' else int(py):04d}-{12 if pm=='01' else int(pm)-1:02d}"
    if kind == "level":
        actual = by_month[period]
        prior = by_month.get(prev_period)
        return round(actual, 3), (round(prior, 3) if prior is not None else None)
    if kind == "mom_change_k":
        if prev_period not in by_month:
            raise ConnectorError(f"{spec['fred']}: no prior month {prev_period}")
        actual = round(by_month[period] - by_month[prev_period], 1)  # thousands
        # prior headline = the month-before change (for vs_trend surprise)
        pp_py, pp_pm = prev_period.split("-")
        prev2 = f"{int(pp_py)-1 if pp_pm=='01' else int(pp_py):04d}-{12 if pp_pm=='01' else int(pp_pm)-1:02d}"
        prior = round(by_month[prev_period] - by_month[prev2], 1) if prev2 in by_month else None
        return actual, prior
    if kind == "yoy_pct":
        yoy_prev_period = f"{int(py)-1:04d}-{pm}"
        if yoy_prev_period not in by_month:
            raise ConnectorError(f"{spec['fred']}: no year-ago {yoy_prev_period}")
        actual = round((by_month[period] / by_month[yoy_prev_period] - 1.0) * 100.0, 2)
        # prior headline = last month's YoY
        if prev_period in by_month:
            yoy_pp = f"{int(py)-1 if pm!='01' else int(py)-1:04d}-{12 if pm=='01' else int(pm)-1:02d}"
            prior = round((by_month[prev_period] / by_month[yoy_pp] - 1.0) * 100.0, 2) if yoy_pp in by_month else None
        else:
            prior = None
        return actual, prior
    raise ConnectorError(f"unknown kind {kind}")


def pull_actuals(conn, dry_run: bool) -> dict:
    now = now_iso()
    rows = conn.execute(
        "SELECT * FROM macro_releases WHERE status='scheduled' AND release_date < ? AND fred_series_id IS NOT NULL",
        (now,),
    ).fetchall()
    updated, events, errors = [], [], []
    for r in rows:
        d = dict(r)
        series, period = d["series"], d["period"]
        spec = RELEASES.get(series)
        if not spec or not period:
            continue
        try:
            actual, prior = _actual_for(series, period)
        except ConnectorError as exc:
            errors.append({"id": d["id"], "error": str(exc)})
            continue
        consensus = d.get("consensus_value")
        basis = "vs_consensus" if consensus is not None else "vs_prior"
        ref = consensus if consensus is not None else prior
        surprise = round(actual - ref, 3) if (ref is not None) else None
        # rate-path lean: a "hot" surprise leans the way the release defines it.
        lean = "neutral"
        if surprise is not None and abs(surprise) >= spec["big_surprise"] * 0.5:
            hot = surprise > 0
            lean = spec["surprise_hot_is"] if hot else (
                "dovish" if spec["surprise_hot_is"] == "hawkish" else "hawkish")
        big = surprise is not None and abs(surprise) >= spec["big_surprise"]
        rec = {"id": d["id"], "series": series, "period": period, "actual": actual,
               "prior": prior, "surprise": surprise, "basis": basis, "lean": lean, "big": big}
        updated.append(rec)
        if dry_run:
            continue
        conn.execute(
            "UPDATE macro_releases SET status='released', actual_value=?, prior_value=?, "
            "surprise=?, surprise_basis=?, rate_path_lean=?, updated_at=? WHERE id=?",
            (actual, prior, surprise, basis, lean, now, d["id"]),
        )
        audit(conn, actor="archivist", entity_type="macro_release", entity_id=d["id"],
              action="release_actual",
              rationale=f"{series} {period}: actual={actual} prior={prior} surprise={surprise} lean={lean}",
              experiment_id=EXPERIMENT_ID)
        if big:
            events.append(rec)
            _write_surprise_market_event(conn, d, rec, spec)
    if not dry_run:
        conn.commit()
    return {"updated": updated, "big_surprises": events, "errors": errors}


def _write_surprise_market_event(conn, rel: dict, rec: dict, spec: dict) -> None:
    """Record a large macro surprise as a market_event + mechanism observations,
    but only if the desk hasn't already recorded one for that date (idempotent)."""
    event_date = rel["release_date"][:10]
    headline = (f"{rel['label']} {rec['period']} surprised {rec['lean']}: "
                f"actual {rec['actual']} vs {rec['basis'].replace('vs_','')} {rec['prior']} "
                f"(surprise {rec['surprise']:+})")
    mev_id = f"mev-macro-{rec['series']}-{rec['period']}"
    if conn.execute("SELECT 1 FROM market_events WHERE id=?", (mev_id,)).fetchone():
        return
    mechs = json.loads(rel.get("linked_mechanism_ids_json") or "[]")
    conn.execute(
        "INSERT INTO market_events (id, event_date, created_at, created_by, headline, "
        "catalyst_class, observed_moves_json, surprise_vs_expectation, attributed_mechanism_ids_json, "
        "lesson_concise, primary_source_refs_json, experiment_id) "
        "VALUES (?, ?, ?, 'archivist', ?, 'macro_release', '{}', ?, ?, ?, ?, ?)",
        (mev_id, event_date, now_iso(), headline,
         f"{rec['series']} actual {rec['actual']} vs {rec['prior']} ({rec['basis']})",
         json.dumps(mechs),
         f"A {rec['lean']} macro surprise reprices the rate path; size duration/risk to the lean.",
         json.dumps([f"fred:{spec['fred']}"]), EXPERIMENT_ID),
    )
    # Each linked mechanism gets a 'hit' observation (the macro link fired).
    for mech in mechs:
        obs_id = f"mobs-macro-{rec['series']}-{rec['period']}-{mech}"
        if conn.execute("SELECT 1 FROM mechanism_observations WHERE id=?", (obs_id,)).fetchone():
            continue
        conn.execute(
            "INSERT INTO mechanism_observations (id, mechanism_id, observed_at, source_type, "
            "source_id, outcome, weight, notes, experiment_id) "
            "VALUES (?, ?, ?, 'market_event', ?, 'hit', 0.7, ?, ?)",
            (obs_id, mech, rel["release_date"], mev_id,
             f"macro surprise: {headline[:140]}", EXPERIMENT_ID),
        )
    audit(conn, actor="archivist", entity_type="market_event", entity_id=mev_id,
          action="macro_surprise_logged", rationale=headline[:300], experiment_id=EXPERIMENT_ID)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed"); s.add_argument("--months", type=int, default=3)
    u = sub.add_parser("upcoming"); u.add_argument("--days", type=int, default=10); u.add_argument("--json", action="store_true")
    a = sub.add_parser("pull-actuals"); a.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    conn = connect()
    try:
        if args.cmd == "seed":
            emit({"ok": True, **seed(conn, args.months)})
        elif args.cmd == "upcoming":
            res = upcoming(conn, args.days)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Upcoming high-impact macro releases (next {args.days}d):\n")
                for r in res["releases"]:
                    print(f"  {r['release_date'][:10]}  {r['series']:9} {r['label']}  (period {r['period']})")
                    print(f"             watch mechanisms: {', '.join(r['mechanisms'])}")
                if not res["releases"]:
                    print("  (none scheduled in window — run `seed` to extend the calendar)")
        elif args.cmd == "pull-actuals":
            emit({"ok": True, **pull_actuals(conn, args.dry_run)})
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
