#!/usr/bin/env python3
"""Evidence graph — typed real-world entities plus predictive and associative links.

The historical filename is retained for compatibility. Backtests, correlations,
co-mentions, and event attribution do not identify causality, so the graph
labels them as validated associations or hypotheses. Symbolic, no embeddings.
Additive to the quant KG (kg_nodes/kg_edges);
lives in two new tables in features.sqlite:

  entities(id, type, name, attrs_json, mention_count, first_seen, last_seen)
    type ∈ market_state | event | catalyst_type | policy | theme | ticker | sector | regime
  causal_edges(id, src_id, dst_id, rel, evidence_json, corroboration, status, confidence, n_obs,
               first_seen, last_seen, created_at)
    status ∈ association_validated | hypothesis | deprecated

Calibrated mechanisms are out-of-sample predictive associations, not proof of a
transmission mechanism. Real-world edges enter as hypotheses. Correlation can
validate association, never causation. Re-running only increments corroboration
when genuinely new evidence is added.

  python3 causal_graph.py build      # clean rebuild from current state; print a report
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")
LIVE = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
MECH_STATUS = {
    "active": "association_validated",
    "candidate": "hypothesis",
    "deprecated": "deprecated",
    "crowded": "association_validated",
}


def _slug(s):
    return "".join(ch if ch.isalnum() else "_" for ch in (s or "").strip().lower())[:60]


def _eid(src, rel, dst):
    return hashlib.sha1(f"{src}|{rel}|{dst}".encode()).hexdigest()[:20]


def ensure_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS entities(
        id TEXT PRIMARY KEY, type TEXT, name TEXT, attrs_json TEXT,
        mention_count INTEGER DEFAULT 0, first_seen TEXT, last_seen TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS causal_edges(
        id TEXT PRIMARY KEY, src_id TEXT, dst_id TEXT, rel TEXT, evidence_json TEXT,
        corroboration INTEGER DEFAULT 0, status TEXT, confidence REAL, n_obs INTEGER DEFAULT 0,
        first_seen TEXT, last_seen TEXT, created_at TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_ce_src ON causal_edges(src_id)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_ce_dst ON causal_edges(dst_id)")


def ent(c, type_, name, attrs=None):
    eid = f"{type_}:{_slug(name)}"
    row = c.execute("SELECT mention_count FROM entities WHERE id=?", (eid,)).fetchone()
    if row:
        c.execute("UPDATE entities SET mention_count=mention_count+1, last_seen=? WHERE id=?", (NOW, eid))
    else:
        c.execute("INSERT INTO entities VALUES(?,?,?,?,?,?,?)",
                  (eid, type_, name, json.dumps(attrs or {}), 1, NOW, NOW))
    return eid


def edge(c, src, dst, rel, *, evidence, status, confidence=None, when=None):
    """UPSERT an edge; corroboration counts unique evidence, not reruns."""
    if not src or not dst or src == dst:
        return
    eid = _eid(src, rel, dst)
    prec = {"association_validated": 2, "hypothesis": 1, "deprecated": 0}
    ev_item = {"src": evidence, "at": when or NOW}
    row = c.execute("SELECT evidence_json, corroboration, status, confidence FROM causal_edges WHERE id=?",
                    (eid,)).fetchone()
    if row:
        evs = json.loads(row[0] or "[]")
        is_new_evidence = not any(e.get("src") == evidence for e in evs)
        if is_new_evidence:
            evs.append(ev_item)
        cur_status = row[2]
        new_status = status if prec.get(status, 0) >= prec.get(cur_status, 0) else cur_status
        new_conf = (
            confidence
            if (confidence is not None and new_status == "association_validated")
            else row[3]
        )
        c.execute("UPDATE causal_edges SET evidence_json=?, corroboration=corroboration+?, status=?, "
                  "confidence=?, last_seen=? WHERE id=?",
                  (json.dumps(evs[-25:]), int(is_new_evidence), new_status,
                   new_conf, NOW, eid))
    else:
        c.execute("INSERT INTO causal_edges VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (eid, src, dst, rel, json.dumps([ev_item]), 1, status, confidence, 0, NOW, NOW, NOW))


def build(rebuild=False):
    c = sqlite3.connect(FEAT)
    ensure_schema(c)
    if rebuild:
        c.execute("DELETE FROM causal_edges")
        c.execute("DELETE FROM entities")
    live = sqlite3.connect(LIVE)
    live.row_factory = sqlite3.Row

    # 1) OOS predictive backbone. This validates prediction, not causation.
    for m in live.execute("SELECT id,name,antecedent_class,consequent_class,transmission_chain_json,"
                          "posterior_mean,status,regime_context FROM mechanisms"):
        st = MECH_STATUS.get(m["status"], "hypothesis")
        a = ent(c, "market_state", m["antecedent_class"] or m["id"])
        z = ent(c, "market_state", m["consequent_class"] or "outcome")
        chain = json.loads(m["transmission_chain_json"] or "[]")
        nodes = [a] + [ent(c, "market_state", s) for s in chain] + [z]
        for i in range(len(nodes) - 1):
            edge(c, nodes[i], nodes[i + 1], "predicts",
                 evidence=f"mechanism:{m['id']} (posterior {round(m['posterior_mean'],3)}, {m['status']})",
                 status=st, confidence=m["posterior_mean"])

    # 2) REAL-WORLD EVENTS — occurrence and repricing are recorded without causal attribution.
    for e in live.execute("SELECT id,event_date,headline,catalyst_class,observed_moves_json,"
                          "attributed_mechanism_ids_json FROM market_events"):
        evname = (e["headline"] or e["id"])[:80]
        ev = ent(c, "event", evname, {"date": e["event_date"], "catalyst_class": e["catalyst_class"]})
        if e["catalyst_class"]:
            edge(c, ev, ent(c, "catalyst_type", e["catalyst_class"]), "is_a",
                 evidence=f"market_event:{e['id']}", status="hypothesis", when=e["event_date"])
        for tk, mv in (json.loads(e["observed_moves_json"] or "{}")).items():
            edge(c, ev, ent(c, "ticker", tk), "coincident_move",
                 evidence=f"{e['event_date']}: {evname} -> {tk} {mv:+.1f}%",
                 status="hypothesis", when=e["event_date"])
        for mid in json.loads(e["attributed_mechanism_ids_json"] or "[]"):
            edge(c, ev, ent(c, "market_state", mid), "attributed_to",
                 evidence=f"market_event:{e['id']} attributed", status="hypothesis",
                 when=e["event_date"])

    # 3) MACRO / POLICY — explicit desk linkage, retained as a hypothesis.
    try:
        for r in live.execute("SELECT id,label,series,surprise,rate_path_lean,linked_mechanism_ids_json,"
                              "release_date FROM macro_releases"):
            pol = ent(c, "policy", r["label"] or r["series"], {"series": r["series"]})
            for mid in json.loads(r["linked_mechanism_ids_json"] or "[]"):
                edge(c, pol, ent(c, "market_state", mid), "linked_to",
                     evidence=f"macro:{r['series']} lean={r['rate_path_lean']} surprise={r['surprise']}",
                     status="hypothesis", when=r["release_date"])
    except sqlite3.OperationalError:
        pass

    # 4) NEWS layer — co-tagging/co-mention is association evidence only.
    try:
        for src, dst, w in c.execute("SELECT src,dst,weight FROM kg_edges WHERE rel='catalyst_for'"):
            th = ent(c, "theme", src.split(":", 1)[1]); tk = ent(c, "ticker", dst.split(":", 1)[1])
            edge(c, th, tk, "mentioned_with", evidence=f"news co-tag (w={w})",
                 status="hypothesis")
        for src, dst, w in c.execute("SELECT src,dst,weight FROM kg_edges WHERE rel='co_mentioned_with'"):
            edge(c, ent(c, "ticker", src.split(":", 1)[1]), ent(c, "ticker", dst.split(":", 1)[1]),
                 "co_occurs", evidence=f"news co-mention PMI={w}", status="hypothesis")
    except sqlite3.OperationalError:
        pass

    # 5) Held-out regime-conditional predictive association.
    try:
        reg = {}
        for mid, hz, rgm, a in c.execute("SELECT mechanism_id,horizon,regime,alpha_pct FROM mechanism_regime"):
            reg.setdefault(mid, {})[rgm] = a
        for mid, rs in reg.items():
            base = rs.get("ALL")
            if base is None:
                continue
            for rgm, a in rs.items():
                if rgm != "ALL" and (a - base) > 1.0:
                    edge(c, ent(c, "regime", rgm), ent(c, "market_state", mid),
                         "conditional_predictor",
                         evidence=f"held-out alpha {round(a,2)}% vs ALL {round(base,2)}% in this regime",
                         status="association_validated",
                         confidence=min(0.72, 0.5 + (a - base) * 0.03))
    except sqlite3.OperationalError:
        pass
    # 6) Quant co-movement: validated association, explicitly non-causal.
    try:
        for s, d, w in c.execute("SELECT src,dst,weight FROM kg_edges WHERE rel='correlated_with' AND weight>=0.6"):
            edge(c, ent(c, "ticker", s.split(":", 1)[1]), ent(c, "ticker", d.split(":", 1)[1]), "co_moves",
                 evidence=f"126d return corr={w}", status="association_validated",
                 confidence=float(w))
    except sqlite3.OperationalError:
        pass

    c.commit()
    _report(c)
    live.close(); c.close()


def _report(c):
    print("=== EVIDENCE GRAPH built (historical table name: causal_edges) ===")
    for typ, n in c.execute("SELECT type,COUNT(*) FROM entities GROUP BY type ORDER BY 2 DESC"):
        print(f"  entities {typ:14} {n}")
    print()
    for rel, st, n in c.execute("SELECT rel,status,COUNT(*) FROM causal_edges GROUP BY rel,status ORDER BY 3 DESC"):
        print(f"  edges {rel:22} {st:22} {n}")
    print("\n  sample VALIDATED ASSOCIATIONS (predictive/correlational):")
    for s, d, conf, ev in c.execute("SELECT src_id,dst_id,confidence,evidence_json FROM causal_edges "
                                    "WHERE status='association_validated' ORDER BY confidence DESC LIMIT 5"):
        print(f"    {s.split(':',1)[1][:34]:34} --> {d.split(':',1)[1][:24]:24} conf={round(conf,3) if conf else '-'}")
    print("\n  sample HYPOTHESIS real-world edges (observed, not causally identified):")
    for s, d, ev in c.execute("SELECT src_id,dst_id,evidence_json FROM causal_edges WHERE status='hypothesis' "
                              "AND rel='coincident_move' ORDER BY corroboration DESC LIMIT 6"):
        e0 = (json.loads(ev)[-1] or {}).get("src", "")
        print(f"    {s.split(':',1)[1][:30]:30} --coincident--> {d.split(':',1)[1][:10]:10}  [{e0[:60]}]")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser(
        "build",
        help="rebuild the evidence graph deterministically from current source tables",
    )
    # Retained for wrapper/backward compatibility. A clean rebuild is now the
    # default because incremental upserts cannot reliably demote an association
    # after its source mechanism is deprecated.
    build_parser.add_argument("--rebuild", action="store_true", help=argparse.SUPPRESS)
    build_parser.add_argument(
        "--incremental",
        action="store_true",
        help="development-only additive build; may retain stale source status",
    )
    args = parser.parse_args(argv)
    if args.command == "build":
        build(rebuild=not args.incremental)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
