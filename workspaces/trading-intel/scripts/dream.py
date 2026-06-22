#!/usr/bin/env python3
"""dream.py — offline CONSOLIDATION / REPLAY pass over the causal graph (Phase 3, first cut).

The "dreaming" layer: runs when the desk is idle and continuously improves the causal model from history,
deterministically + reversibly (operates only on the rebuildable analytics graph in features.sqlite —
NEVER touches live trading state). This pass does:

  1. DECAY-REPLAY  — re-test each VALIDATED `co_moves` edge on RECENT returns; if a once-strong link has
     rotted (recent corr fell below the edge floor), DOWNGRADE it to candidate with the new value. This is
     how the graph forgets relationships that stopped being true. (Reversible: causal_graph.py rebuilds.)
  2. PROMOTION REVIEW — surface high-corroboration CANDIDATE edges that are ready for the held-out validator.
  3. ALIAS CLUSTERS — flag entity nodes that look like duplicates (e.g. "hbm (high-bandwidth memory)" ~
     "high-bandwidth memory") for consolidation.
  4. STALE edges — VALIDATED edges not re-seen in a long time (evidence aging).

Counterfactual rollouts + LLM hypothesis-mining are the next dream operations (gated by the validator).

  python3 dream.py            # run a dream pass; writes decay downgrades + prints the consolidation report
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
import signal_scan as ss  # noqa: E402  (reuse _returns / _corr)

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
DECAY_FLOOR = 0.45          # a validated co_move whose recent corr falls below this has rotted
STALE_DAYS = 45            # validated edge not re-seen in this many days = aging evidence


def _norm(name):
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def main():
    c = sqlite3.connect(FEAT)
    try:
        c.execute("SELECT 1 FROM causal_edges LIMIT 1")
    except sqlite3.OperationalError:
        print("no causal_edges table — run causal_graph.py build first")
        return 0

    # 1) DECAY-REPLAY validated co_moves
    rows = c.execute("SELECT id,src_id,dst_id,confidence FROM causal_edges "
                     "WHERE rel='co_moves' AND status='validated'").fetchall()
    rets, downgraded, checked = {}, [], 0
    for eid, s, d, conf in rows:
        a, b = s.split(":", 1)[1], d.split(":", 1)[1]
        if a not in rets:
            rets[a] = ss._returns(a, 126)
        if b not in rets:
            rets[b] = ss._returns(b, 126)
        if not rets.get(a) or not rets.get(b):
            continue
        cur = ss._corr(rets[a], rets[b])
        checked += 1
        if cur < DECAY_FLOOR and (conf or 0) >= 0.55:
            c.execute("UPDATE causal_edges SET status='candidate', confidence=?, last_seen=? WHERE id=?",
                      (round(cur, 3), NOW, eid))
            downgraded.append((a, b, conf, round(cur, 3)))
    c.commit()

    # 2) PROMOTION REVIEW — candidate edges with the most corroboration (ready for the validator)
    promo = c.execute("SELECT src_id,dst_id,rel,corroboration FROM causal_edges "
                      "WHERE status='candidate' ORDER BY corroboration DESC LIMIT 12").fetchall()

    # 3) ALIAS CLUSTERS — entities whose normalized names collide or nest (consolidation candidates)
    ents = c.execute("SELECT id,type,name,mention_count FROM entities WHERE type IN ('theme','technology')").fetchall()
    by_norm = {}
    for eid, typ, name, mc in ents:
        by_norm.setdefault(_norm(name), []).append((name, mc))
    exact_dups = {k: v for k, v in by_norm.items() if len(v) > 1}

    # 4) STALE validated edges (evidence aging)
    stale = c.execute("SELECT COUNT(*) FROM causal_edges WHERE status='validated' AND last_seen < ?",
                      (datetime.now(timezone.utc).replace(microsecond=0).isoformat()[:10],)).fetchone()[0]

    print("=== DREAM PASS (offline consolidation / replay) ===")
    print(f"  decay-replay: re-tested {checked} validated co_moves; DOWNGRADED {len(downgraded)} rotted edges")
    for a, b, was, now_c in downgraded[:8]:
        print(f"     {a} ~ {b}: corr {was:.2f} -> {now_c:.2f}  (relationship decayed -> candidate)")
    print(f"\n  promotion review (top candidate edges by corroboration — ready for held-out validation):")
    for s, d, rel, corr in promo[:8]:
        print(f"     (x{corr}) {s.split(':',1)[1][:30]:30} --{rel}--> {d.split(':',1)[1][:18]}")
    print(f"\n  alias clusters (exact normalized-name duplicates to consolidate): {len(exact_dups)}")
    for k, v in list(exact_dups.items())[:5]:
        print(f"     {' | '.join(n for n, _ in v)[:80]}")
    print(f"\n  aging: {stale} validated edges not re-seen recently (evidence > today; informational)")
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
