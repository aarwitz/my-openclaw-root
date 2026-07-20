#!/usr/bin/env python3
"""Deterministic hypothesis→mechanism linker (D57).

Why: predictions carried mechanism_ids_json=[] on 168/184 rows because the only
automatic linker was a ≥2-token prose match that effectively never fired. With
no links, graded outcomes produce ZERO mechanism_observations — the desk grades
its trades and learns nothing. This module links deterministically, three tiers:

  T1 NAME:    mechanisms.name appears verbatim in the thesis (signal-created
              hypotheses embed exact names: "Mechanisms: <name>; <name>").
  T2 FEATURE: ≥2 distinct feature tokens from the mechanism id (drawdown_252,
              mom_12_1, vix_level, ...) appear in thesis + evidence indicators.
  T3 CLASS:   ≥2 distinct ≥4-char tokens from antecedent/consequent classes
              appear in thesis + evidence indicators (the legacy rule, now fed
              with evidence text, not just prose).

Used by predict.py at prediction time and as a CLI to backfill open predictions:

  python3 link_mechanisms.py backfill [--dry-run]     # link open predictions
  python3 link_mechanisms.py show <hypothesis_id>     # debug one hypothesis
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
HORIZON_ALIASES = {
    "1d": "intraday",
    "intraday": "intraday",
    "1-3d": "swing_1_5d",
    "1-5d": "swing_1_5d",
    "swing": "swing_1_5d",
    "1w": "swing_1_5d",
    "1-4w": "position_1_4w",
    "weeks": "position_1_4w",
    "1m": "position_1_4w",
    "position": "position_1_4w",
    "1-3m": "trend_1_3m",
    "months": "trend_1_3m",
    "3m": "trend_1_3m",
    "trend": "trend_1_3m",
    "6m+": "long_6m_plus",
    "1y": "long_6m_plus",
    "long": "long_6m_plus",
}

# tokens in mechanism ids that are structure, not features
_STRUCT = {"gen", "multi", "long", "short", "hi", "lo", "quarter", "month",
           "63d", "21d", "5d", "chg", "level", "ttm", "yoy", "2m", "12", "1"}


def _feature_tokens(mech_id: str) -> set[str]:
    base = mech_id.split("__")[0]
    return {t for t in base.split("_") if len(t) >= 3 and t not in _STRUCT}


def _class_tokens(mech: dict) -> set[str]:
    toks: set[str] = set()
    for f in ("antecedent_class", "consequent_class"):
        for part in re.split(r"[\s_/,+-]+", (mech.get(f) or "").lower()):
            if len(part) >= 4:
                toks.add(part)
    return toks


def _normalize_horizon(horizon: str | None) -> str | None:
    if not horizon:
        return None
    norm = horizon.strip().lower()
    return HORIZON_ALIASES.get(norm, norm)


def load_mechanisms(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name, antecedent_class, consequent_class, direction, horizon, status "
        "FROM mechanisms WHERE status != 'deprecated'")]


def hypothesis_text(conn, hyp_id: str, thesis: str | None = None) -> str:
    if thesis is None:
        row = conn.execute("SELECT thesis_summary FROM hypotheses WHERE id=?", (hyp_id,)).fetchone()
        thesis = row[0] if row else ""
    ev = " ".join(r[0] or "" for r in conn.execute(
        "SELECT indicator FROM hypothesis_evidence WHERE hypothesis_id=?", (hyp_id,)))
    return f"{thesis} {ev}".lower()


def link(text: str, mechanisms: list[dict], hypothesis_horizon: str | None = None) -> list[dict]:
    """Return [{'id','align','src'}] — align=1 (linked mechanisms support the thesis)."""
    out, seen = [], set()
    norm_horizon = _normalize_horizon(hypothesis_horizon)
    for m in mechanisms:
        mid = m["id"]
        if mid in seen:
            continue
        src = None
        name = (m.get("name") or "").lower()
        if len(name) > 10 and name in text:
            src = "name"
        elif len(_feature_tokens(mid) & set(re.split(r"[\s_,:;.()]+", text))) >= 2:
            src = "feature"
        elif sum(1 for t in _class_tokens(m) if t in text) >= 2:
            src = "class"
        if src:
            seen.add(mid)
            out.append({
                "id": mid,
                "align": 1,
                "src": src,
                "horizon_match": _normalize_horizon(m.get("horizon")) == norm_horizon,
            })
    # cap: over-linking pollutes learning with false attribution. Keep the
    # most-specific 6 (name matches are exact, feature matches structural,
    # class matches fuzzy).
    rank = {"name": 0, "feature": 1, "class": 2}
    out.sort(key=lambda e: (rank[e["src"]], -int(e["horizon_match"]), e["id"]))
    trimmed = out[:6]
    for entry in trimmed:
        entry.pop("horizon_match", None)
    return trimmed


def backfill(dry_run: bool = False) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    mechs = load_mechanisms(conn)
    rows = conn.execute(
        "SELECT p.id, p.hypothesis_id, h.thesis_summary FROM predictions p "
        "JOIN hypotheses h ON h.id = p.hypothesis_id "
        "WHERE p.resolved_at IS NULL AND (p.mechanism_ids_json IS NULL OR p.mechanism_ids_json IN ('[]',''))"
    ).fetchall()
    linked, empty, by_src = 0, 0, {"name": 0, "feature": 0, "class": 0}
    for r in rows:
        text = hypothesis_text(conn, r["hypothesis_id"], r["thesis_summary"])
        links = link(text, mechs)
        if not links:
            empty += 1
            continue
        for entry in links:
            by_src[entry["src"]] += 1
        if not dry_run:
            conn.execute("UPDATE predictions SET mechanism_ids_json=? WHERE id=?",
                         (json.dumps(links), r["id"]))
        linked += 1
    if not dry_run and linked:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
            "before_state, after_state, rationale_concise) VALUES (?, ?, 'quant', 'prediction', "
            "'backfill-links-20260709', 'backfill_mechanism_links', NULL, NULL, ?)",
            (f"AUDIT-{ts.replace(':','').replace('-','')}-linkbackfill",
             ts,
             f"D57: deterministic mechanism links backfilled onto {linked} open predictions "
             f"({empty} unlinkable) so the 2026-07-14 grading cohort produces mechanism_observations. "
             f"Tiers: name={by_src['name']} feature={by_src['feature']} class={by_src['class']}. "
             "p_correct NOT retro-changed (forecast integrity)."))
        conn.commit()
    return {"open_unlinked": len(rows), "linked": linked, "unlinkable": empty, "by_src": by_src,
            "dry_run": dry_run}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "backfill":
        print(json.dumps(backfill(dry_run="--dry-run" in argv), indent=2))
        return 0
    if argv[0] == "show" and len(argv) > 1:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        text = hypothesis_text(conn, argv[1])
        hrow = conn.execute("SELECT time_horizon FROM hypotheses WHERE id=?", (argv[1],)).fetchone()
        print(json.dumps({
            "text": text[:300],
            "links": link(text, load_mechanisms(conn), hrow[0] if hrow else None),
        }, indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
