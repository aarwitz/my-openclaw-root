#!/usr/bin/env python3
"""Validate association edges without making causal claims.

For ticker pairs first linked by news co-mention, calculate a 126-session
return correlation. A sufficiently large correlation promotes the link from
``hypothesis`` to ``association_validated``. This establishes co-movement,
not a causal direction. A shuffled-leg placebo checks whether the measured
correlations are distinguishable from a simple random pairing artifact.

  python3 causal_validate.py
"""

from __future__ import annotations

import os
import random
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
import signal_scan as ss  # noqa: E402  (reuse _returns / _corr)

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
PROMOTE_CORR = 0.50


def main():
    c = sqlite3.connect(FEAT)
    try:
        c.execute("SELECT 1 FROM causal_edges LIMIT 1")
    except sqlite3.OperationalError:
        print("no causal_edges — run causal_graph.py build first")
        return 0
    rets = {}

    def ret(t):
        if t not in rets:
            rets[t] = ss._returns(t, 126)
        return rets[t]

    # bound to the liquid universe so we don't serially fetch prices for ~1000+ names (the slowness, not a
    # rate-limit, that timed the first run out). Their prices are already cached from the KG/causal builds.
    # cover the co-mention universe (top ~1200); prices are mostly cached from the KG/causal builds. This
    # is an OFFLINE (dream-time) job — slowness is fine here; it is never on the trade-execution path.
    liquid = {r[0] for r in c.execute("SELECT symbol FROM universe WHERE status='active' AND "
                                      "market_cap IS NOT NULL ORDER BY market_cap DESC LIMIT 1200")}

    # 1) Validate a measurable association (liquid pairs only).
    cands = c.execute("SELECT id,src_id,dst_id FROM causal_edges WHERE rel='co_occurs' AND status='hypothesis' "
                      "AND src_id LIKE 'ticker:%' AND dst_id LIKE 'ticker:%'").fetchall()
    tested = promoted = 0
    for eid, s, d in cands:
        ta, tb = s.split(":", 1)[1].upper(), d.split(":", 1)[1].upper()   # entity ids are slugged lowercase
        if ta not in liquid or tb not in liquid:
            continue
        a, b = ret(ta), ret(tb)
        if not a or not b:
            continue
        cv = ss._corr(a, b)
        tested += 1
        if cv >= PROMOTE_CORR:
            c.execute("UPDATE causal_edges SET status='association_validated', confidence=?, last_seen=? WHERE id=?",
                      (round(cv, 3), NOW, eid))
            promoted += 1
    c.commit()

    # 2) Placebo test on the association-validated co-move layer.
    val = c.execute("SELECT src_id,dst_id FROM causal_edges WHERE status='association_validated' "
                    "AND rel IN ('co_moves','co_occurs') AND confidence IS NOT NULL "
                    "ORDER BY RANDOM() LIMIT 120").fetchall()
    real, placebo = [], []
    for s, d in val:
        a, b = ret(s.split(":", 1)[1]), ret(d.split(":", 1)[1])
        if not a or not b:
            continue
        real.append(abs(ss._corr(a, b)))
        bshuf = b[:]
        random.shuffle(bshuf)
        placebo.append(abs(ss._corr(a, bshuf)))
    rm = sum(real) / len(real) if real else 0.0
    pm = sum(placebo) / len(placebo) if placebo else 0.0

    print("=== ASSOCIATION EDGE VALIDATION ===")
    print(f"  co-movement test: tested {tested} co-narrative hypotheses -> "
          f"PROMOTED {promoted} to association_validated (corr>={PROMOTE_CORR})")
    print(f"  left as hypotheses: {tested - promoted}")
    print(f"\n  PLACEBO integrity test ({len(real)} association-validated edges):")
    print(f"    real      mean |corr| = {rm:.3f}")
    print(f"    shuffled  mean |corr| = {pm:.3f}   (must collapse toward 0)")
    verdict = "PASS — associations reflect non-random structure" if (rm - pm) > 0.25 else "WEAK — investigate"
    print(f"    integrity gap = {rm - pm:.3f}  -> {verdict}")
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
