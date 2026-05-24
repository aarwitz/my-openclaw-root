"""Replay & outcomes filler.

Two jobs:
  1. `recompute(date)` — load frozen raw cache for past Candidates rows and
      re-score them. Useful for calibrating scoring/penalty changes against
      historical decisions without re-querying upstream.
  2. `fill_outcomes(as_of)` — for every Candidates row with date in
     [as_of - 14d, as_of - 1d], pull Massive aggregates and compute
     1d/5d/10d returns + max runup/drawdown into Outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date as _date, timedelta
from typing import Optional

from .adapters import massive, sheets
from .schema import OUTCOMES_HEADER


@dataclass
class OutcomeRow:
    date_added: str
    ticker: str
    regime: str
    setup_state: str
    recommendation_class: str
    entry_reference_price: Optional[float]
    one_d_return: Optional[float]
    five_d_return: Optional[float]
    ten_d_return: Optional[float]
    max_runup: Optional[float]
    max_drawdown: Optional[float]
    falsifier_resolved: Optional[str]
    outcome_label: str
    postmortem: Optional[str] = None

    def as_sheet_dict(self) -> dict:
        return {
            "date_added": self.date_added,
            "ticker": self.ticker,
            "regime": self.regime,
            "setup_state": self.setup_state,
            "recommendation_class": self.recommendation_class,
            "entry_reference_price": self.entry_reference_price,
            "1d_return": self.one_d_return,
            "5d_return": self.five_d_return,
            "10d_return": self.ten_d_return,
            "max_runup": self.max_runup,
            "max_drawdown": self.max_drawdown,
            "falsifier_resolved": self.falsifier_resolved,
            "outcome_label": self.outcome_label,
            "postmortem": self.postmortem,
        }


def _label(one_d: Optional[float], five_d: Optional[float], ten_d: Optional[float]) -> str:
    """Cheap deterministic outcome label."""
    if five_d is None:
        return "unknown"
    if five_d >= 0.05:
        return "winner"
    if five_d <= -0.04:
        return "loser"
    if ten_d is not None and ten_d >= 0.07:
        return "delayed_winner"
    if ten_d is not None and ten_d <= -0.05:
        return "delayed_loser"
    return "chop"


def _slice_after(bars: list[dict], anchor_iso: str) -> list[dict]:
    try:
        anchor_ts = int(datetime.fromisoformat(anchor_iso).timestamp() * 1000)
    except Exception:
        return bars
    return [b for b in bars if int(b.get("t", 0)) > anchor_ts]


def compute_outcome(ticker: str, date_added: str) -> Optional[OutcomeRow]:
    """Return an OutcomeRow if we have enough data, else None."""
    try:
        bars = massive.daily_aggregates(ticker, lookback_days=45)
    except Exception:
        return None
    if not bars:
        return None
    forward = _slice_after(bars, date_added + "T00:00:00")
    if not forward:
        return None
    entry = float(forward[0]["o"]) if "o" in forward[0] else float(forward[0]["c"])
    closes = [float(b["c"]) for b in forward]
    highs  = [float(b["h"]) for b in forward]
    lows   = [float(b["l"]) for b in forward]

    def ret(n: int) -> Optional[float]:
        if len(closes) <= n - 1 or entry <= 0:
            return None
        return round((closes[n - 1] - entry) / entry, 4)

    horizon = min(len(forward), 10)
    max_runup = round(max((highs[i] - entry) / entry for i in range(horizon)), 4) if entry > 0 else None
    max_drawdown = round(min((lows[i] - entry) / entry for i in range(horizon)), 4) if entry > 0 else None

    one_d = ret(1); five_d = ret(5); ten_d = ret(10)
    return OutcomeRow(
        date_added=date_added, ticker=ticker, regime="", setup_state="",
        recommendation_class="", entry_reference_price=round(entry, 4),
        one_d_return=one_d, five_d_return=five_d, ten_d_return=ten_d,
        max_runup=max_runup, max_drawdown=max_drawdown,
        falsifier_resolved=None, outcome_label=_label(one_d, five_d, ten_d),
    )


def fill_outcomes(as_of: Optional[str] = None, *, write_sheet: bool = True) -> list[OutcomeRow]:
    today = _date.fromisoformat(as_of) if as_of else _date.today()
    horizon_start = (today - timedelta(days=14)).isoformat()
    candidates = sheets.read_candidates_since(horizon_start)
    out: list[OutcomeRow] = []
    for c in candidates:
        d = c.get("date"); t = c.get("ticker")
        if not d or not t:
            continue
        if d == today.isoformat():  # skip same-day rows
            continue
        row = compute_outcome(t, d)
        if not row:
            continue
        row.regime = c.get("regime") or ""
        row.setup_state = c.get("setup_state") or ""
        row.recommendation_class = c.get("recommendation_class") or ""
        out.append(row)
        if write_sheet:
            try:
                sheets.upsert_outcome(row.as_sheet_dict())
            except Exception:
                pass
    return out


# ---------- replay (recompute scores against frozen raw cache) ----------

def recompute_score_for(record: dict) -> dict:
    """Take a Candidates row dict, re-run scoring with the current engine.

    Note: this only re-scores; it does not re-derive structural fields.
    The intent is to validate that scoring/calibration changes move the
    class in the expected direction across past decisions.
    """
    from .schema import CandidateRecord
    from . import scoring

    fields = {k: v for k, v in record.items() if k in CandidateRecord.__dataclass_fields__}
    # cast strings
    for k, v in list(fields.items()):
        if isinstance(v, str) and v.lower() in ("true", "false"):
            fields[k] = (v.lower() == "true")
        elif isinstance(v, str) and v == "":
            fields[k] = None
        elif isinstance(v, str):
            try:
                fields[k] = float(v)
            except ValueError:
                pass
    r = CandidateRecord(**fields)  # type: ignore[arg-type]
    scoring.score(r)
    return r.as_dict()
