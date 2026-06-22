#!/usr/bin/env python3
"""WHY-engine (Phase 2) — answer "why did / does X happen" by traversing the symbolic CAUSAL GRAPH
(entities + causal_edges), presenting **VALIDATED causes first** (held-out confidence), **candidate
associations clearly labeled** (observed, not yet validated), and **honest GAP-flagging** when no
validated cause exists. Deterministic graph traversal, no embeddings. An LLM (later) only narrates the
retrieved chains — it never invents causality.

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
    val_links = sorted([e for e in inc + out if e[2] == "validated"], key=lambda e: -(e[3] or 0))
    cand_inc = sorted([e for e in inc if e[2] == "candidate"], key=lambda e: -e[4])

    if val_links:
        print("  VALIDATED causes / links  (survived held-out testing; confidence shown):")
        for nid, rel, st, conf, corr, ev in val_links[:8]:
            print(f"    [{(conf or 0):.2f}]  {_name(c, nid)[:46]:46}  ({rel})")
    if cand_inc:
        print("\n  OBSERVED associations  (candidate — repeatedly seen, NOT yet held-out-validated):")
        for nid, rel, st, conf, corr, ev in cand_inc[:10]:
            e0 = (json.loads(ev or "[]")[-1] or {}).get("src", "")
            print(f"    (x{corr})  {_name(c, nid)[:40]:40}  --{rel}-->   [{e0[:52]}]")
    if out:
        drives = sorted([e for e in out if e not in val_links], key=lambda e: -(e[4] or 0))[:6]
        if drives:
            print("\n  what it appears to drive  (outgoing):")
            for nid, rel, st, conf, corr, ev in drives:
                print(f"    --{rel}-->  {_name(c, nid)[:40]:40} ({st})")

    if not val_links:
        print("\n  GAP: no held-out-VALIDATED cause for this node yet — only the observed associations above. "
              "The honest answer is we have a hypothesis, not a proven cause.")
    else:
        print("\n  Note: 'VALIDATED' = survived out-of-sample testing; 'OBSERVED' = recurring association the "
              "desk has not yet been able to prove causal. We distinguish the two on purpose.")
    c.close()


if __name__ == "__main__":
    explain(" ".join(sys.argv[1:]) or "NVDA")
