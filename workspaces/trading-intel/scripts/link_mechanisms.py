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
              Generated multi-feature mechanisms require T1 exact-name linkage;
              fuzzy feature/class links over-attributed plain growth prose in
              the TM-257 replay cohort.
  T3 CLASS:   ≥2 distinct ≥4-char tokens from antecedent/consequent classes
              appear in thesis + evidence indicators (the legacy rule, now fed
              with evidence text, not just prose).

Used by predict.py at prediction time and as a CLI to backfill open predictions:

  python3 link_mechanisms.py backfill [--dry-run]     # link open predictions
  python3 link_mechanisms.py relink-resolved --dry-run # preview approved TM-288 relink
  python3 link_mechanisms.py relink-resolved --execute # backup + relink resolved predictions
  python3 link_mechanisms.py show <hypothesis_id>     # debug one hypothesis
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
ROOT = Path(os.path.expanduser("~/.openclaw"))
RELINK_PROPOSAL_ID = "rp-brier-root-relink-20260724"
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


def load_mechanisms(conn, *, include_deprecated: bool = False) -> list[dict]:
    sql = (
        "SELECT id, name, antecedent_class, consequent_class, direction, horizon, status "
        "FROM mechanisms"
    )
    if not include_deprecated:
        sql += " WHERE status != 'deprecated'"
    return [dict(r) for r in conn.execute(sql)]


def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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
        elif mid.startswith("multi_"):
            # TM-263/TM-257 follow-up: generated conjunctive feature stacks are
            # too specific for fuzzy prose linkage. Require the creating signal
            # to name the multi mechanism exactly.
            continue
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


def _parse_link_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out = []
    for item in data:
        if isinstance(item, dict) and item.get("id"):
            out.append(str(item["id"]))
        elif isinstance(item, str):
            out.append(item)
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit_id(stem: str) -> str:
    return f"AUDIT-{_now_iso().replace(':', '').replace('-', '')}-{stem}-{uuid.uuid4().hex[:8]}"


def _audit(conn, *, entity_type: str, entity_id: str, action: str,
           before: str | None, after: str | None, rationale: str) -> str:
    aid = _audit_id(entity_id[:20])
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise, experiment_id) "
        "VALUES (?, ?, 'developer', ?, ?, ?, ?, ?, ?, ?)",
        (aid, _now_iso(), entity_type, entity_id, action, before, after,
         rationale[:500], RELINK_PROPOSAL_ID),
    )
    return aid


def _backup_ledger(db_path: str = DB_PATH) -> dict:
    src = Path(db_path).expanduser()
    backup_dir = ROOT / "backups" / "ledger"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst = backup_dir / f"trading-intel-PRE-RELINK-{stamp}.sqlite"

    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    check = sqlite3.connect(str(dst)).execute("PRAGMA integrity_check").fetchone()[0]
    if check != "ok":
        raise RuntimeError(f"backup integrity_check={check}")
    return {"path": str(dst), "integrity_check": check}


def _resolved_rows(conn: sqlite3.Connection, days: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.id, p.hypothesis_id, p.mechanism_ids_json, p.realized_outcome,
               p.resolved_at, p.regime_at_prediction, h.thesis_summary, h.time_horizon
        FROM predictions p
        JOIN hypotheses h ON h.id = p.hypothesis_id
        WHERE p.resolved_at > datetime('now', ?)
          AND p.brier_component IS NOT NULL
        ORDER BY p.resolved_at DESC, p.id DESC
        """,
        (f"-{days} days",),
    ).fetchall()


def _build_relink_plan(conn: sqlite3.Connection, days: int) -> dict:
    # Match brier_contributors.py's TM-257 replay cohort: historical resolved
    # rows are replayed against the full mechanism universe, including ids later
    # deprecated, so stored history can be relinked without inventing a new cohort.
    mechs = load_mechanisms(conn, include_deprecated=True)
    rows = _resolved_rows(conn, days)
    changes = []
    by_src = {"name": 0, "feature": 0, "class": 0}
    old_link_count = 0
    new_link_count = 0

    for row in rows:
        old_ids = _parse_link_ids(row["mechanism_ids_json"])
        text = hypothesis_text(conn, row["hypothesis_id"], row["thesis_summary"])
        new_links = link(text, mechs, row["time_horizon"])
        new_ids = [item["id"] for item in new_links]
        old_link_count += len(old_ids)
        new_link_count += len(new_ids)
        for item in new_links:
            by_src[item["src"]] += 1
        if old_ids != new_ids:
            changes.append({
                "prediction_id": row["id"],
                "hypothesis_id": row["hypothesis_id"],
                "resolved_at": row["resolved_at"],
                "realized_outcome": row["realized_outcome"],
                "regime_at_prediction": row["regime_at_prediction"],
                "old_ids": old_ids,
                "new_links": new_links,
                "new_ids": new_ids,
            })

    return {
        "window_days": days,
        "resolved_predictions": len(rows),
        "changed_links": len(changes),
        "changed_predictions": len(changes),
        "old_link_count": old_link_count,
        "new_link_count": new_link_count,
        "by_src": by_src,
        "changes": changes,
    }


def _observation_outcome(realized_outcome: str, align: int) -> str | None:
    if realized_outcome not in ("correct", "incorrect"):
        return None
    thesis_correct = realized_outcome == "correct"
    mech_correct = thesis_correct if align >= 0 else not thesis_correct
    return "hit" if mech_correct else "miss"


def _apply_relink_plan(conn: sqlite3.Connection, plan: dict, backup: dict) -> dict:
    obs_deleted = 0
    obs_inserted = 0
    prediction_audits = []
    before_obs = conn.execute(
        "SELECT COUNT(*) FROM mechanism_observations WHERE source_type='prediction'"
    ).fetchone()[0]

    for change in plan["changes"]:
        old_json = json.dumps([{"id": mid, "align": 1} for mid in change["old_ids"]],
                              sort_keys=True)
        new_json = json.dumps(change["new_links"], sort_keys=True)
        cur = conn.execute(
            "SELECT mechanism_ids_json FROM predictions WHERE id=?",
            (change["prediction_id"],),
        ).fetchone()
        before_raw = cur["mechanism_ids_json"] if cur else None

        deleted = conn.execute(
            "DELETE FROM mechanism_observations WHERE source_type='prediction' AND source_id=?",
            (change["prediction_id"],),
        ).rowcount
        obs_deleted += max(0, deleted)

        for link_item in change["new_links"]:
            outcome = _observation_outcome(change["realized_outcome"], int(link_item.get("align", 1) or 1))
            if outcome is None:
                continue
            conn.execute(
                "INSERT INTO mechanism_observations (id, mechanism_id, observed_at, "
                "source_type, source_id, outcome, weight, regime_at_obs, notes, experiment_id) "
                "VALUES (?, ?, ?, 'prediction', ?, ?, 1.0, ?, ?, ?)",
                (
                    "mobs-" + uuid.uuid4().hex[:20],
                    link_item["id"],
                    change["resolved_at"] or _now_iso(),
                    change["prediction_id"],
                    outcome,
                    change["regime_at_prediction"],
                    f"TM-288 relink from prediction {change['prediction_id']} "
                    f"(align={int(link_item.get('align', 1) or 1)}, thesis={change['realized_outcome']})",
                    RELINK_PROPOSAL_ID,
                ),
            )
            obs_inserted += 1

        conn.execute(
            "UPDATE predictions SET mechanism_ids_json=? WHERE id=?",
            (new_json, change["prediction_id"]),
        )
        prediction_audits.append(_audit(
            conn,
            entity_type="prediction",
            entity_id=change["prediction_id"],
            action="relink_resolved_prediction",
            before=before_raw if before_raw is not None else old_json,
            after=new_json,
            rationale=(
                f"{RELINK_PROPOSAL_ID}: resolved-history mechanism relink; "
                f"old={change['old_ids']} new={change['new_ids']}"
            ),
        ))

    after_obs = conn.execute(
        "SELECT COUNT(*) FROM mechanism_observations WHERE source_type='prediction'"
    ).fetchone()[0]
    backup_audit = _audit(
        conn,
        entity_type="ledger",
        entity_id="trading-intel.sqlite",
        action="ledger_backup",
        before=None,
        after=backup["path"],
        rationale=(
            f"{RELINK_PROPOSAL_ID}: pre-relink same-day ledger backup "
            f"{backup['path']} integrity={backup['integrity_check']}"
        ),
    )
    bulk_audit = _audit(
        conn,
        entity_type="prediction",
        entity_id=RELINK_PROPOSAL_ID,
        action="relink_resolved",
        before=f"prediction_obs={before_obs}",
        after=f"prediction_obs={after_obs}",
        rationale=(
            f"TM-288 applied current linker to {plan['changed_predictions']} changed "
            f"resolved predictions over {plan['resolved_predictions']} rows/{plan['window_days']}d; "
            f"obs_deleted={obs_deleted} obs_inserted={obs_inserted}; backup={backup['path']}"
        ),
    )
    return {
        "obs_deleted": obs_deleted,
        "obs_inserted": obs_inserted,
        "prediction_observations_before": before_obs,
        "prediction_observations_after": after_obs,
        "backup_audit_id": backup_audit,
        "bulk_audit_id": bulk_audit,
        "prediction_audit_count": len(prediction_audits),
        "prediction_audit_sample": prediction_audits[:5],
    }


def relink_resolved(*, dry_run: bool, execute: bool, days: int = 30,
                    db_path: str = DB_PATH) -> dict:
    if dry_run == execute:
        raise ValueError("choose exactly one of dry_run or execute")
    conn = _connect(db_path)
    try:
        plan = _build_relink_plan(conn, days)
        result = {
            "mode": "execute" if execute else "dry-run",
            "dry_run": dry_run,
            "proposal_id": RELINK_PROPOSAL_ID,
            "window_days": plan["window_days"],
            "resolved_predictions": plan["resolved_predictions"],
            "changed_links": plan["changed_links"],
            "changed_predictions": plan["changed_predictions"],
            "old_link_count": plan["old_link_count"],
            "new_link_count": plan["new_link_count"],
            "by_src": plan["by_src"],
            "changed_prediction_sample": [
                {
                    "prediction_id": change["prediction_id"],
                    "old_ids": change["old_ids"],
                    "new_ids": change["new_ids"],
                }
                for change in plan["changes"][:10]
            ],
        }
        if execute:
            backup = _backup_ledger(db_path)
            result["backup"] = backup
            result["apply"] = _apply_relink_plan(conn, plan, backup)
            conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def backfill(dry_run: bool = False) -> dict:
    conn = _connect(DB_PATH)
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
    if argv[0] == "relink-resolved":
        dry_run = "--dry-run" in argv
        execute = "--execute" in argv
        days = 30
        if "--days" in argv:
            try:
                days = int(argv[argv.index("--days") + 1])
            except (IndexError, ValueError):
                print("relink-resolved requires integer --days", file=sys.stderr)
                return 2
        if dry_run == execute:
            print("relink-resolved requires exactly one of --dry-run or --execute", file=sys.stderr)
            return 2
        print(json.dumps(relink_resolved(dry_run=dry_run, execute=execute, days=days), indent=2))
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
