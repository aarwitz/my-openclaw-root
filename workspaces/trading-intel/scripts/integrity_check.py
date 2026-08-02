#!/usr/bin/env python3
"""integrity_check.py — fail-fast verifier: is the desk actually closing the loop?

A month of silent holes (risk_model on a frozen book, positions.pnl NULL forever,
compute_attribution crashing nightly, the sizing signal anti-predictive) all looked
GREEN because nothing asserted the system's real job. Unit tests can't catch those —
each component "works"; the failures live in the SEAMS (loop not closed), the DATA
(silently wrong/NULL), and the OUTCOME (no edge). This asserts all three, read-only,
every run, and is LOUD when red.

Two verdicts, deliberately separate:
  * INTEGRITY — data-reality + loop-closure. RED = the machine is broken, fix fast.
    Exit code is non-zero on any integrity failure so a cron/health-sweep screams.
  * EDGE — does the strategy actually predict returns? RED here is an honest "no alpha
    yet", NOT a bug — but it must never be hidden behind "we're being patient".

Add a check when you find a new class of silent hole; that is the whole point.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from connectors.marketdata import daily_bar_complete, is_trading_day

DB_PATH = "/home/aaron/.openclaw/state/trading-intel.sqlite"
OPEN_STATES = ("opening", "open", "scaling", "trimming", "closing")

# (table, column, where, max_null_fraction, label) — a column the system CLAIMS to have.
NULL_HOLE_CHECKS = [
    ("positions", "unrealized_pnl_pct", "state != 'closed'", 0.5, "open positions have no marked P&L"),
    ("positions", "regime_at_first_open", "state != 'closed'", 0.9, "positions never record their entry regime"),
    ("attribution", "realized_edge_vs_spy_bps", "1=1", 0.5, "closed trades have no realized edge vs SPY"),
    ("predictions", "realized_excess_pct", "resolved_at IS NOT NULL", 0.5, "resolved predictions have no graded outcome"),
    ("portfolio_snapshots", "cash", "1=1", 0.5, "portfolio snapshots have no cash figure"),
]

# (table, ts_column, max_missed_sessions, label) — is the daily output fresh?
# Wall-clock hours are invalid across weekends and exchange holidays.
FRESHNESS_CHECKS = [
    ("benchmarks", "captured_at", 0, "SPY scoreboard"),
    ("capital_efficiency_snapshots", "as_of", 0, "capital-efficiency telemetry"),
    ("portfolio_risk", "as_of", 0, "portfolio risk snapshot"),
]

ET = ZoneInfo("America/New_York")


def _current_time(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    return current if current.tzinfo is not None else current.replace(tzinfo=timezone.utc)


def _timestamp(value: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def completed_sessions_since(timestamp: str, now: datetime | None = None) -> int:
    """Count completed NYSE sessions strictly after an artifact's ET date.

    A Friday artifact is fresh on Sunday and Monday before the close.  It
    becomes one session stale only after Monday's close.  This makes health
    deterministic across weekends, holidays, and early closes.
    """
    current = _current_time(now)
    current_et = current.astimezone(ET)
    cursor = _timestamp(timestamp).astimezone(ET).date() + timedelta(days=1)
    missed = 0
    while cursor <= current_et.date():
        day = cursor.isoformat()
        if is_trading_day(day) and daily_bar_complete(day, now=current):
            missed += 1
        cursor += timedelta(days=1)
    return missed


def _rows(conn, q, *a):
    return conn.execute(q, a).fetchall()


def data_reality(conn, now: datetime | None = None) -> list[dict]:
    out = []
    # SQLite does not enforce foreign keys unless every writer opts in. A
    # clean table count can therefore hide learning records whose mechanism or
    # hypothesis was deleted. Treat every orphan as a hard integrity failure.
    try:
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        sample = ", ".join(
            f"{r[0]} rowid={r[1]} -> {r[2]}" for r in violations[:5]
        )
        out.append({
            "family": "data",
            "id": "integrity:foreign_keys",
            "status": "RED" if violations else "OK",
            "detail": (
                f"{len(violations)} orphaned foreign-key reference(s)"
                + (f": {sample}" if sample else "")
            ),
        })
    except sqlite3.Error as exc:
        out.append({
            "family": "data",
            "id": "integrity:foreign_keys",
            "status": "RED",
            "detail": f"cannot run foreign_key_check: {exc}",
        })

    # One ticker cannot have several simultaneously live theses. That made the
    # same name look like repeated independent evidence and left 242 "active"
    # rows over a 38-name book. Dormant/resolved rows are retained history.
    try:
        duplicates = conn.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT UPPER(jt.value) ticker FROM hypotheses h,json_each(h.tickers) jt "
            "WHERE h.state IN ('raw','scored','challenged','ready','active') "
            "GROUP BY UPPER(jt.value) HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        out.append({
            "family": "data",
            "id": "integrity:duplicate_live_hypotheses",
            "status": "RED" if duplicates else "OK",
            "detail": f"{duplicates} ticker(s) have more than one live hypothesis",
        })
    except sqlite3.Error as exc:
        out.append({
            "family": "data",
            "id": "integrity:duplicate_live_hypotheses",
            "status": "RED",
            "detail": f"cannot verify live-hypothesis uniqueness: {exc}",
        })

    # A live position backed by a resolved/dormant thesis has no truthful
    # lifecycle owner and encourages research to originate the held name again.
    try:
        mismatches = conn.execute(
            "SELECT COUNT(*) FROM positions p JOIN hypotheses h ON h.id=p.hypothesis_id "
            "WHERE p.state IN ('opening','open','scaling','trimming','closing') "
            "AND h.state != 'active'"
        ).fetchone()[0]
        out.append({
            "family": "data",
            "id": "integrity:position_thesis_lifecycle",
            "status": "RED" if mismatches else "OK",
            "detail": f"{mismatches} open position(s) are backed by a non-active thesis",
        })
    except sqlite3.Error as exc:
        out.append({
            "family": "data",
            "id": "integrity:position_thesis_lifecycle",
            "status": "RED",
            "detail": f"cannot verify position/thesis lifecycle: {exc}",
        })

    for tbl, col, where, max_frac, label in NULL_HOLE_CHECKS:
        try:
            r = conn.execute(f"SELECT COUNT(*) n, SUM({col} IS NULL) nulls FROM {tbl} WHERE {where}").fetchone()
        except sqlite3.Error as e:
            out.append({"family": "data", "id": f"nullhole:{tbl}.{col}", "status": "RED",
                        "detail": f"cannot read {tbl}.{col}: {e}"})
            continue
        n = r["n"] or 0
        if n == 0:
            continue
        frac = (r["nulls"] or 0) / n
        out.append({
            "family": "data", "id": f"nullhole:{tbl}.{col}",
            "status": "RED" if frac > max_frac else "OK",
            "detail": f"{label}: {r['nulls']}/{n} NULL ({frac:.0%})",
        })

    # A resolved prediction without a numeric return was historically created
    # by the retired hypothesis-level grader before the forecast matured.
    # Explicit bad-input invalidations are the only legitimate exception.
    try:
        n = conn.execute(
            """
            SELECT COUNT(*)
            FROM predictions p
            WHERE p.resolved_at IS NOT NULL
              AND (p.realized_return_pct IS NULL OR p.realized_excess_pct IS NULL)
              AND NOT EXISTS (
                SELECT 1 FROM audits a
                WHERE a.entity_type='prediction' AND a.entity_id=p.id
                  AND a.action='invalidate_bad_inputs'
              )
            """
        ).fetchone()[0]
        out.append({
            "family": "data",
            "id": "integrity:prediction_grade_numeric",
            "status": "RED" if n else "OK",
            "detail": f"{n} resolved prediction(s) lack numeric realized return/excess",
        })
    except sqlite3.Error as exc:
        out.append({
            "family": "data",
            "id": "integrity:prediction_grade_numeric",
            "status": "RED",
            "detail": f"cannot verify numeric prediction grades: {exc}",
        })

    # Consistency: does the RISK MODEL see the same book the DB holds? (frozen-source detector)
    try:
        db_n = conn.execute(
            f"SELECT COUNT(DISTINCT UPPER(ticker)) FROM positions WHERE state IN ({','.join('?'*len(OPEN_STATES))})",
            OPEN_STATES,
        ).fetchone()[0]
        pr = conn.execute("SELECT n_positions FROM portfolio_risk ORDER BY as_of DESC LIMIT 1").fetchone()
        pr_n = pr["n_positions"] if pr else None
        if pr_n is not None and db_n:
            drift = abs(pr_n - db_n) / db_n
            out.append({
                "family": "data", "id": "consistency:risk_model_book",
                "status": "RED" if drift > 0.25 else "OK",
                "detail": f"portfolio_risk n_positions={pr_n} vs live book={db_n} (drift {drift:.0%}) "
                          "— a big gap means the risk view is on a stale/frozen source",
            })
    except sqlite3.Error:
        pass

    # GATE-BYPASS tripwire (invariant #3): any broker-reaching intent after the 2026-06-06
    # hardening without an approving risk_review = the single worst possible bug. 5 legacy
    # fills from the desk's first sessions (06-04/05) are grandfathered by the date floor.
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM trade_intents ti WHERE ti.state IN ('submitted','filled','partial') "
            "AND ti.created_at >= '2026-06-06' AND NOT EXISTS (SELECT 1 FROM risk_reviews rr "
            "WHERE rr.target_id=ti.id AND rr.verdict IN ('approved','resized'))"
        ).fetchone()[0]
        out.append({"family": "data", "id": "gate:bypass",
                    "status": "RED" if n else "OK",
                    "detail": f"{n} broker-reaching intents WITHOUT an approving risk_review (post-hardening)"})
    except sqlite3.Error:
        pass

    # PARTIAL-BAR leakage tripwire: only close-derived `price` features stamped
    # for an incomplete daily bar are contamination.  Same-day news/FMP rows are
    # legitimately knowable intraday and must not be mislabeled (2026-07-30:
    # 11 FMP + 36 news rows produced a false "47 partial rows" CRIT).
    try:
        import os as _os
        from datetime import datetime as _dt, timezone as _tz
        sys.path.insert(0, _os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))
        from connectors.marketdata import daily_bar_complete as _bar_complete
        from zoneinfo import ZoneInfo as _ZoneInfo
        today = _dt.now(_tz.utc).astimezone(
            _ZoneInfo("America/New_York")
        ).date().isoformat()
        fdb = sqlite3.connect(
            "file:" + _os.path.expanduser("~/.openclaw/state/features.sqlite") + "?mode=ro",
            uri=True,
        )
        n = fdb.execute(
            "SELECT COUNT(*) FROM features WHERE source='price' AND as_of>=?",
            (today,),
        ).fetchone()[0]
        fdb.close()
        complete = _bar_complete(today)
        leaking = n if not complete else 0
        out.append({
            "family": "data", "id": "data:partial_bar_leak",
            "status": "RED" if leaking else "OK",
            "detail": (
                f"{leaking} close-derived price feature rows stamped with an "
                f"incomplete {today} daily bar"
            ),
        })
    except Exception as exc:
        out.append({
            "family": "data", "id": "data:partial_bar_leak",
            "status": "RED",
            "detail": f"cannot verify daily-bar completeness; failing closed: {exc}",
        })

    # UNMARKABLE positions: mark_positions couldn't price an open position with any provider.
    # That position's stops/falsifiers can never fire — it is unprotected capital. Root causes:
    # ticker rename (BK->BNY sat unpriced 2026-07-08..28), delisting, symbol typo. mark_positions
    # writes state/mark-skips.json every run; stale telemetry (>24h) is itself a failure.
    try:
        import json as _json
        import os as _os
        from datetime import datetime as _dt, timezone as _tz
        p = _os.path.expanduser("~/.openclaw/state/mark-skips.json")
        if not _os.path.exists(p):
            out.append({"family": "data", "id": "data:unmarkable_positions", "status": "OK",
                        "detail": "no mark-skips telemetry yet (mark_positions has not run since upgrade)"})
        else:
            with open(p) as fh:
                sk = _json.load(fh)
            current = _current_time(now)
            age_h = ( current
                      - _dt.fromisoformat(str(sk.get("generated_at","1970-01-01T00:00:00Z")).replace("Z","+00:00"))
                    ).total_seconds() / 3600.0
            names = sorted({s.get("ticker","?") for s in sk.get("skipped", [])})
            missed = completed_sessions_since(sk.get("generated_at", "1970-01-01T00:00:00Z"), now=current)
            if missed > 0:
                out.append({"family": "data", "id": "data:unmarkable_positions", "status": "RED",
                            "detail": f"mark telemetry is {age_h:.0f}h old and {missed} completed session(s) stale — mark_positions is not running"})
            else:
                out.append({"family": "data", "id": "data:unmarkable_positions",
                            "status": "RED" if names else "OK",
                            "detail": (f"open positions NO provider can price (unprotected — rename/delisting?): {', '.join(names)}"
                                       if names else f"all open positions priced; {age_h:.0f}h wall-clock age, 0 completed sessions missed")})
    except Exception:
        pass

    # Debrief numeric decay: market_events whose observed_moves_json is empty of ticker moves.
    # Found 2026-07-23: every event after 07-17 recorded {} or index-only moves, silently starving
    # both research:big_story_direction and event-decomposition candidates. An LLM "remembering to
    # record numbers" is not a data source — flag the decay the day it happens.
    try:
        import json as _json
        rows = conn.execute(
            "SELECT observed_moves_json FROM market_events WHERE event_date >= date('now','-5 days')"
        ).fetchall()
        if len(rows) >= 2:
            idx = {"SPY", "QQQ", "SOXX", "IWM", "DIA", "VIX", "GLD", "TLT"}
            empty = 0
            for r in rows:
                try:
                    mv = _json.loads(r["observed_moves_json"] or "{}")
                except (ValueError, TypeError):
                    mv = {}
                if not any(k not in idx for k in (mv or {})):
                    empty += 1
            frac = empty / len(rows)
            out.append({
                "family": "data", "id": "nullhole:market_events.observed_moves",
                "status": "RED" if frac >= 0.8 else "OK",
                "detail": f"{empty}/{len(rows)} recent market_events have no single-name moves recorded "
                          "— the debrief stopped writing numbers (starves big-story coverage + event decomposition)",
            })
    except sqlite3.Error:
        pass
    return out


def loop_closure(conn, now: datetime | None = None) -> list[dict]:
    out = []
    current = _current_time(now)
    for tbl, tsc, max_missed, label in FRESHNESS_CHECKS:
        try:
            last = conn.execute(f"SELECT MAX({tsc}) FROM {tbl}").fetchone()[0]
        except sqlite3.Error:
            last = None
        if not last:
            out.append({"family": "loop", "id": f"fresh:{tbl}", "status": "RED", "detail": f"{label}: never written"})
            continue
        try:
            age_h = (current - _timestamp(last)).total_seconds() / 3600.0
            missed = completed_sessions_since(last, now=current)
        except (TypeError, ValueError) as exc:
            out.append({"family": "loop", "id": f"fresh:{tbl}", "status": "RED",
                        "detail": f"{label}: invalid timestamp {last!r} ({exc})"})
            continue
        out.append({"family": "loop", "id": f"fresh:{tbl}",
                    "status": "RED" if missed > max_missed else "OK",
                    "detail": f"{label}: last update {age_h:.1f}h ago, {missed} completed session(s) missed"})

    # Contract: closed positions must produce an attribution row WITH a realized edge (pnl->attribution loop).
    try:
        gap = conn.execute(
            "SELECT COUNT(*) FROM positions p WHERE p.state='closed' AND p.closed_at IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM attribution a WHERE a.position_id=p.id "
            "AND a.realized_edge_vs_spy_bps IS NOT NULL)"
        ).fetchone()[0]
        closed = conn.execute("SELECT COUNT(*) FROM positions WHERE state='closed' AND closed_at IS NOT NULL").fetchone()[0]
        out.append({
            "family": "loop",
            "id": "contract:closed->attribution",
            "status": "RED" if gap else "OK",
            "detail": (
                f"{gap}/{closed} closed trades have no realized-edge attribution"
                if gap
                else f"all {closed} closed trades have realized-edge attribution"
            ),
        })
    except sqlite3.Error:
        pass

    # Learning loop starving: use the resolver's exact trading-session maturity
    # windows. Calendar age is not maturity for a 21-session forecast.
    try:
        from resolve_prediction_backlog import resolve_prediction_backlog
        maturity = resolve_prediction_backlog(conn, dry_run=True)
        stale = int(maturity.get("matured", 0) or 0)
        blocked = int(maturity.get("data_blocked", 0) or 0)
        out.append({"family": "loop", "id": "learn:unresolved_backlog",
                    "status": "RED" if stale else "OK",
                    "detail": f"{stale} trading-window-matured predictions unresolved "
                              f"({blocked} blocked by missing market data)"})
    except Exception as exc:
        out.append({"family": "loop", "id": "learn:unresolved_backlog",
                    "status": "RED",
                    "detail": f"cannot verify exact prediction maturity: {exc}"})

    # Challenged->resolve loop: 'challenged' theses that rot un-resolved = the research-decision
    # loop is open (the desk notices it's wrong and does nothing). resolve_challenged should drain it.
    try:
        ch = conn.execute("SELECT COUNT(*) FROM hypotheses WHERE state='challenged'").fetchone()[0]
        out.append({"family": "loop", "id": "research:challenged_rot",
                    "status": "RED" if ch > 40 else ("WARN" if ch > 15 else "OK"),
                    "detail": f"{ch} theses stuck 'challenged' (flagged wrong, never resolved)"})
    except sqlite3.Error:
        pass

    # TRIGGER-FRESHNESS: a scheduled step that silently stops firing is a bug by construction
    # (a fix "deployed" into a cron chain that never runs looks identical to no fix — the
    # resolve_challenged deploy sat 22h before its first slot, invisible). Assert the resolver
    # actually FIRED recently whenever there is rot for it to drain. 80h threshold clears the
    # weekend gap (Fri 16:12 -> Mon 08:00 = ~64h) while catching a missed weekday by the next morning.
    try:
        if ch > 0:
            last = conn.execute(
                "SELECT MAX(timestamp) FROM audits WHERE actor='critic' "
                "AND action IN ('resolve_hold','resolve_close','resolve_flip')"
            ).fetchone()[0]
            if last is None:
                out.append({"family": "loop", "id": "trigger:resolver_fired",
                            "status": "RED",
                            "detail": f"{ch} challenged theses waiting but resolve_challenged has NEVER fired"})
            else:
                age_h = conn.execute("SELECT (julianday('now') - julianday(?)) * 24", (last,)).fetchone()[0]
                out.append({"family": "loop", "id": "trigger:resolver_fired",
                            "status": "RED" if age_h > 80 else "OK",
                            "detail": f"resolver last fired {age_h:.0f}h ago with {ch} challenged waiting"})
    except sqlite3.Error:
        pass
    return out


def edge(conn) -> list[dict]:
    """Honest 'do we have alpha?' — RED here is truth, not a bug."""
    out = []
    rows = _rows(conn, "SELECT p_correct pc, realized_excess_pct rex FROM predictions "
                       "WHERE resolved_at IS NOT NULL AND p_correct IS NOT NULL AND realized_excess_pct IS NOT NULL")
    if len(rows) >= 20:
        a = [r["pc"] for r in rows]
        b = [r["rex"] for r in rows]
        ma, mb = statistics.mean(a), statistics.mean(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        da = sum((x - ma) ** 2 for x in a) ** 0.5
        dbb = sum((y - mb) ** 2 for y in b) ** 0.5
        corr = num / (da * dbb) if da and dbb else 0.0
        out.append({"family": "edge", "id": "edge:conviction_predicts",
                    "status": "RED" if corr <= 0.05 else "OK",
                    "detail": f"corr(p_correct, realized_excess) = {corr:+.2f} over n={len(rows)} "
                              "— the sizing conviction signal does not predict returns"})
    row = conn.execute(
        "SELECT COUNT(*) n, AVG(realized_edge_vs_spy_bps) avg FROM attribution "
        "WHERE realized_edge_vs_spy_bps IS NOT NULL AND closed_at >= datetime('now','-90 days')"
    ).fetchone()
    if (row["n"] or 0) >= 8:
        out.append({"family": "edge", "id": "edge:selection_alpha",
                    "status": "RED" if (row["avg"] or 0) <= 0 else "OK",
                    "detail": f"closed-trade realized edge avg {row['avg']:.0f} bps vs SPY over {row['n']} trades"})
    return out


BIG_MOVE_PCT = 2.0
_INDEX = {"SPY", "QQQ", "SOXX", "IWM", "DIA", "VIX", "GLD", "TLT"}
_BEAR_WORDS = ("short", "bear", "downside", "de-rat", "sell", "avoid", "overvalued")


def research_coverage(conn) -> list[dict]:
    """Were we on the RIGHT SIDE of the biggest single-name stories? Missed/wrong-side
    opportunities are invisible to trade-outcome grading (you only grade trades you made),
    so this reads market_events' own recorded moves and asks: on each big move, did we hold
    a timely, correct-DIRECTION thesis? Consensus-long-into-a-rotation lights up here.
    RED = poor research, a quality verdict (WARN), not a machine break."""
    import json as _json
    events = _rows(conn, "SELECT event_date, observed_moves_json FROM market_events "
                         "WHERE event_date >= date('now','-30 days') AND observed_moves_json IS NOT NULL")
    big: dict[str, tuple[str, float]] = {}
    for e in events:
        try:
            moves = _json.loads(e["observed_moves_json"])
        except (ValueError, TypeError):
            continue
        for tk, mv in (moves or {}).items():
            if tk in _INDEX or not isinstance(mv, (int, float)):
                continue
            if abs(mv) >= BIG_MOVE_PCT and (tk not in big or abs(mv) > abs(big[tk][1])):
                big[tk] = (e["event_date"], float(mv))
    if len(big) < 3:
        return []
    covered = correct = 0
    misses = []
    for tk, (dt, mv) in sorted(big.items(), key=lambda x: -abs(x[1][1])):
        h = conn.execute(
            "SELECT h.thesis_summary, h.state, "
            "(SELECT p.return_p50 FROM predictions p WHERE p.hypothesis_id=h.id "
            " AND date(p.predicted_at) < date(?) ORDER BY p.predicted_at DESC LIMIT 1) return_p50 "
            "FROM hypotheses h WHERE h.tickers LIKE ? "
            "AND date(h.created_at) BETWEEN date(?, '-10 days') AND date(?, '-1 day') "
            "ORDER BY h.created_at DESC LIMIT 1",
            (dt, f'%"{tk}"%', dt, dt),
        ).fetchone()
        if not h:
            misses.append(f"{tk} {mv:+.1f}% {dt}: no timely pre-event thesis (missed story)")
            continue
        covered += 1
        th = (h["thesis_summary"] or "").lower()
        forecast = h["return_p50"]
        our_short = (
            float(forecast) < 0
            if forecast is not None and abs(float(forecast)) > 0.05
            else any(w in th for w in _BEAR_WORDS)
        )
        if (mv < 0) == our_short:
            correct += 1
        else:
            misses.append(f"{tk} {mv:+.1f}% {dt}: we were {'short' if our_short else 'long'} ({h['state']}) — wrong side")
    n = len(big)
    return [{
        "family": "research", "id": "research:big_story_direction",
        "status": "RED" if (n and correct / n < 0.4) else "OK",
        "detail": f"direction-correct on {correct}/{n} of the biggest single-name stories (30d), "
                  f"timely pre-event coverage {covered}/{n}"
                  + ("; " + " | ".join(misses[:4]) if misses else ""),
    }]


def judgment_quality() -> list[dict]:
    """Market-graded quality of the judgment organs (from grade_resolutions.py's summary).
    LLM-vs-LLM verdicts are inadmissible here — only forward market-relative outcomes count
    (2026-07-23: the resolver called 90% of challenges false alarms; the MARKET graded the
    decisive ones only 45% false / 55% vindicated)."""
    import os as _os, time as _time
    path = _os.path.expanduser("~/.openclaw/state/resolution-grades.json")
    out: list[dict] = []
    try:
        if _time.time() - _os.path.getmtime(path) > 48 * 3600:
            return [{"family": "edge", "id": "judgment:grades_stale", "status": "RED",
                     "detail": "resolution-grades.json older than 48h — grade_resolutions stopped running"}]
        rep = json.load(open(path))
    except (OSError, ValueError):
        return []
    ch = rep.get("challenges", {})
    far = ch.get("false_alarm_rate_decisive")
    nd = (ch.get("false_alarm", 0) or 0) + (ch.get("vindicated", 0) or 0)
    if far is not None and nd >= 10:
        out.append({"family": "edge", "id": "judgment:challenge_quality",
                    "status": "RED" if far > 0.6 else "OK",
                    "detail": f"critic challenges (market-graded, n={nd} decisive): "
                              f"{far:.0%} false alarms, avg post-challenge excess {ch.get('avg_fwd_excess_pct')}%"})
    # Valuation engine quality (market-graded by grade_valuations.py)
    vpath = _os.path.expanduser("~/.openclaw/state/valuation-grades.json")
    try:
        vrep = json.load(open(vpath))
        sp = vrep.get("cheap_minus_rich_spread_pp")
        if sp is not None and vrep.get("n_graded", 0) >= 30:
            out.append({"family": "edge", "id": "judgment:valuation_quality",
                        "status": "RED" if sp < -2.0 else "OK",
                        "detail": f"cheap-minus-rich 21td spread {sp:+.1f}pp over {vrep['n_graded']} graded calls "
                                  f"({vrep.get('caveat','')})"})
    except (OSError, ValueError):
        pass

    hold, close = rep.get("resolver_hold", {}), rep.get("resolver_close", {})
    n_res = (hold.get("n_graded", 0) or 0) + (close.get("n_graded", 0) or 0)
    if n_res >= 10:
        wrong = (hold.get("wrong", 0) or 0) + (close.get("wrong", 0) or 0)
        correct = (hold.get("correct", 0) or 0) + (close.get("correct", 0) or 0)
        out.append({"family": "edge", "id": "judgment:resolver_quality",
                    "status": "RED" if wrong > correct else "OK",
                    "detail": f"resolver decisions (market-graded, n={n_res}): {correct} correct / {wrong} wrong "
                              f"(HOLD avg {hold.get('avg_fwd_excess_pct')}%, CLOSE avg {close.get('avg_fwd_excess_pct')}%)"})
    return out


def main() -> int:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        integrity = data_reality(conn) + loop_closure(conn)
        edge_checks = edge(conn) + research_coverage(conn) + judgment_quality()
    finally:
        conn.close()
    integ_red = [c for c in integrity if c["status"] == "RED"]
    edge_red = [c for c in edge_checks if c["status"] == "RED"]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "integrity": {"status": "BROKEN" if integ_red else "OK",
                      "red": len(integ_red), "checks": integrity},
        "edge": {"status": "NO_EDGE" if edge_red else "OK",
                 "note": "RED = honest 'no alpha/poor research yet', not a bug — never hide it behind 'be patient'",
                 "checks": edge_checks},
        "headline": (
            f"INTEGRITY {'BROKEN' if integ_red else 'OK'} ({len(integ_red)} broken loops/data holes); "
            f"EDGE/RESEARCH {'FAILING' if edge_red else 'ok'} ({len(edge_red)} red)"
        ),
    }
    print(json.dumps(report, indent=1))
    # Non-zero ONLY on integrity failure — a broken machine must fail the check.
    # No-edge is reported loudly but does not itself fail (it's a strategy verdict, not a bug).
    return 1 if integ_red else 0


if __name__ == "__main__":
    raise SystemExit(main())
