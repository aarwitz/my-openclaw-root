#!/usr/bin/env python3
"""WHY-engine — explain what evidence is linked to an entity.

The historical storage table is named ``causal_edges``, but its contents are
predictive associations, correlations, co-mentions, and explicit hypotheses.
None of those establish a causal effect. The engine therefore presents
validated associations and hypotheses without converting either into a
"because" claim. Deterministic graph traversal, no embeddings.

  python3 why_engine.py NVDA
  python3 why_engine.py "high bandwidth memory"
  python3 why_engine.py jobs          # fuzzy-matches event headlines / entity names
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")


def _slug(s):
    return "".join(ch if ch.isalnum() else "_" for ch in (s or "").strip().lower())[:60]


def _name(c, eid):
    r = c.execute("SELECT name FROM entities WHERE id=?", (eid,)).fetchone()
    return r[0] if r else eid


def resolve(c, q):
    ql = q.strip().lower()
    for cand in (f"ticker:{ql}", f"theme:{_slug(ql)}", f"market_state:{_slug(ql)}", f"regime:{ql}",
                 f"policy:{_slug(ql)}", f"catalyst_type:{_slug(ql)}"):
        if c.execute("SELECT 1 FROM entities WHERE id=?", (cand,)).fetchone():
            return cand
    r = c.execute("SELECT id FROM entities WHERE lower(name) LIKE ? ORDER BY mention_count DESC LIMIT 1",
                  (f"%{ql}%",)).fetchone()
    return r[0] if r else None


def explain(q):
    c = sqlite3.connect(FEAT)
    tid = resolve(c, q)
    if not tid:
        print(f"No entity matches '{q}'. (Try a ticker, theme, regime, or event keyword.)")
        return
    typ, name = c.execute("SELECT type,name FROM entities WHERE id=?", (tid,)).fetchone()
    print(f"WHY  ·  {name}   [{typ}]\n")

    inc = c.execute("SELECT src_id,rel,status,confidence,corroboration,evidence_json FROM causal_edges "
                    "WHERE dst_id=?", (tid,)).fetchall()
    out = c.execute("SELECT dst_id,rel,status,confidence,corroboration,evidence_json FROM causal_edges "
                    "WHERE src_id=?", (tid,)).fetchall()
    # symmetric links (co_moves/co_occurs) can sit in either direction
    val_links = sorted(
        [e for e in inc + out if e[2] == "association_validated"],
        key=lambda e: -(e[3] or 0),
    )
    hypotheses = sorted([e for e in inc if e[2] == "hypothesis"], key=lambda e: -e[4])

    if val_links:
        print("  VALIDATED ASSOCIATIONS  (predictive or correlational; confidence shown):")
        for nid, rel, st, conf, corr, ev in val_links[:8]:
            print(f"    [{(conf or 0):.2f}]  {_name(c, nid)[:46]:46}  ({rel})")
    if hypotheses:
        print("\n  HYPOTHESES / OBSERVATIONS  (not causally identified):")
        for nid, rel, st, conf, corr, ev in hypotheses[:10]:
            e0 = (json.loads(ev or "[]")[-1] or {}).get("src", "")
            print(f"    (x{corr})  {_name(c, nid)[:40]:40}  --{rel}-->   [{e0[:52]}]")
    if out:
        drives = sorted([e for e in out if e not in val_links], key=lambda e: -(e[4] or 0))[:6]
        if drives:
            print("\n  outgoing links  (direction is a stored relation, not proof of effect):")
            for nid, rel, st, conf, corr, ev in drives:
                print(f"    --{rel}-->  {_name(c, nid)[:40]:40} ({st})")

    if not val_links:
        print("\n  GAP: no held-out validated association for this node. Any links above are "
              "hypotheses or observations, not an identified explanation.")
    else:
        print("\n  Note: validation here means a predictive/correlation test survived its evaluation rule. "
              "It does not establish causality.")
    c.close()


if __name__ == "__main__":
    explain(" ".join(sys.argv[1:]) or "NVDA")
