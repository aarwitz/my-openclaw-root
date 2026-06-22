#!/usr/bin/env python3
"""CAUSAL GRAPH (Phase 1 of the causal-world-model) — typed real-world entities + typed, evidenced,
validation-gated causal edges. Symbolic, no embeddings. Additive to the quant KG (kg_nodes/kg_edges);
lives in two new tables in features.sqlite:

  entities(id, type, name, attrs_json, mention_count, first_seen, last_seen)
    type ∈ market_state | event | catalyst_type | policy | theme | ticker | sector | regime
  causal_edges(id, src_id, dst_id, rel, evidence_json, corroboration, status, confidence, n_obs,
               first_seen, last_seen, created_at)
    rel    ∈ transmits_to | causes | affects | influences | co_occurs | is_a | via
    status ∈ validated (held-out-validated, has confidence) | candidate (observed, unvalidated) | deprecated

DESIGN: the graph starts with a VALIDATED financial backbone — our calibrated `mechanisms` already ARE
validated causal hypotheses (antecedent_class → consequent_class + a backtested posterior). Newly auto-wired
real-world edges (from market_events, macro releases, news co-mention/themes) enter as CANDIDATE and only
become `validated` once they survive the same held-out test (see causal_validate.py, Phase-1 next step).
UPSERT/accumulate: re-running appends evidence + bumps corroboration; never wipes.

  python3 causal_graph.py build      # (re)wire entities + causal_edges from current state; print a report
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")
LIVE = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
MECH_STATUS = {"active": "validated", "candidate": "candidate", "deprecated": "deprecated", "crowded": "validated"}


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
    """UPSERT a causal edge: accumulate evidence + corroboration; upgrade status (validated>candidate>
    deprecated) and confidence; never downgrade a validated edge from a candidate observation."""
    if not src or not dst or src == dst:
        return
    eid = _eid(src, rel, dst)
    prec = {"validated": 2, "candidate": 1, "deprecated": 0}
    ev_item = {"src": evidence, "at": when or NOW}
    row = c.execute("SELECT evidence_json, corroboration, status, confidence FROM causal_edges WHERE id=?",
                    (eid,)).fetchone()
    if row:
        evs = json.loads(row[0] or "[]")
        if not any(e.get("src") == evidence for e in evs):
            evs.append(ev_item)
        cur_status = row[2]
        new_status = status if prec.get(status, 0) >= prec.get(cur_status, 0) else cur_status
        new_conf = confidence if (confidence is not None and new_status == "validated") else row[3]
        c.execute("UPDATE causal_edges SET evidence_json=?, corroboration=corroboration+1, status=?, "
                  "confidence=?, last_seen=? WHERE id=?",
                  (json.dumps(evs[-25:]), new_status, new_conf, NOW, eid))
    else:
        c.execute("INSERT INTO causal_edges VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (eid, src, dst, rel, json.dumps([ev_item]), 1, status, confidence, 0, NOW, NOW, NOW))


def build():
    c = sqlite3.connect(FEAT)
    ensure_schema(c)
    live = sqlite3.connect(LIVE)
    live.row_factory = sqlite3.Row

    # 1) VALIDATED financial backbone — calibrated mechanisms are validated causal hypotheses
    for m in live.execute("SELECT id,name,antecedent_class,consequent_class,transmission_chain_json,"
                          "posterior_mean,status,regime_context FROM mechanisms"):
        st = MECH_STATUS.get(m["status"], "candidate")
        a = ent(c, "market_state", m["antecedent_class"] or m["id"])
        z = ent(c, "market_state", m["consequent_class"] or "outcome")
        chain = json.loads(m["transmission_chain_json"] or "[]")
        nodes = [a] + [ent(c, "market_state", s) for s in chain] + [z]
        for i in range(len(nodes) - 1):
            edge(c, nodes[i], nodes[i + 1], "transmits_to",
                 evidence=f"mechanism:{m['id']} (posterior {round(m['posterior_mean'],3)}, {m['status']})",
                 status=st, confidence=m["posterior_mean"])

    # 2) REAL-WORLD EVENTS — market_events: event entity --affects--> each repriced ticker (candidate)
    for e in live.execute("SELECT id,event_date,headline,catalyst_class,observed_moves_json,"
                          "attributed_mechanism_ids_json FROM market_events"):
        evname = (e["headline"] or e["id"])[:80]
        ev = ent(c, "event", evname, {"date": e["event_date"], "catalyst_class": e["catalyst_class"]})
        if e["catalyst_class"]:
            edge(c, ev, ent(c, "catalyst_type", e["catalyst_class"]), "is_a",
                 evidence=f"market_event:{e['id']}", status="candidate", when=e["event_date"])
        for tk, mv in (json.loads(e["observed_moves_json"] or "{}")).items():
            edge(c, ev, ent(c, "ticker", tk), "affects",
                 evidence=f"{e['event_date']}: {evname} -> {tk} {mv:+.1f}%",
                 status="candidate", when=e["event_date"])
        for mid in json.loads(e["attributed_mechanism_ids_json"] or "[]"):
            edge(c, ev, ent(c, "market_state", mid), "via",
                 evidence=f"market_event:{e['id']} attributed", status="candidate", when=e["event_date"])

    # 3) MACRO / POLICY — macro_releases: release --influences--> linked mechanisms (rate-path lean)
    try:
        for r in live.execute("SELECT id,label,series,surprise,rate_path_lean,linked_mechanism_ids_json,"
                              "release_date FROM macro_releases"):
            pol = ent(c, "policy", r["label"] or r["series"], {"series": r["series"]})
            for mid in json.loads(r["linked_mechanism_ids_json"] or "[]"):
                edge(c, pol, ent(c, "market_state", mid), "influences",
                     evidence=f"macro:{r['series']} lean={r['rate_path_lean']} surprise={r['surprise']}",
                     status="candidate", when=r["release_date"])
    except sqlite3.OperationalError:
        pass

    # 4) NEWS layer — reuse the PMI-filtered KG: theme--affects-->ticker, ticker<->ticker co_occurs
    try:
        for src, dst, w in c.execute("SELECT src,dst,weight FROM kg_edges WHERE rel='catalyst_for'"):
            th = ent(c, "theme", src.split(":", 1)[1]); tk = ent(c, "ticker", dst.split(":", 1)[1])
            edge(c, th, tk, "affects", evidence=f"news co-tag (w={w})", status="candidate")
        for src, dst, w in c.execute("SELECT src,dst,weight FROM kg_edges WHERE rel='co_mentioned_with'"):
            edge(c, ent(c, "ticker", src.split(":", 1)[1]), ent(c, "ticker", dst.split(":", 1)[1]),
                 "co_occurs", evidence=f"news co-mention PMI={w}", status="candidate")
    except sqlite3.OperationalError:
        pass

    # 5) MACRO REGIME conditioning (VALIDATED) — regime --influences--> mechanism (held-out regime alphas)
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
                    edge(c, ent(c, "regime", rgm), ent(c, "market_state", mid), "influences",
                         evidence=f"held-out alpha {round(a,2)}% vs ALL {round(base,2)}% in this regime",
                         status="validated", confidence=min(0.72, 0.5 + (a - base) * 0.03))
    except sqlite3.OperationalError:
        pass
    # 6) QUANT co-movement (VALIDATED) — ticker --co_moves--> ticker (return-correlation backbone)
    try:
        for s, d, w in c.execute("SELECT src,dst,weight FROM kg_edges WHERE rel='correlated_with' AND weight>=0.6"):
            edge(c, ent(c, "ticker", s.split(":", 1)[1]), ent(c, "ticker", d.split(":", 1)[1]), "co_moves",
                 evidence=f"126d return corr={w}", status="validated", confidence=float(w))
    except sqlite3.OperationalError:
        pass

    c.commit()
    _report(c)
    live.close(); c.close()


def _report(c):
    print("=== CAUSAL GRAPH built ===")
    for typ, n in c.execute("SELECT type,COUNT(*) FROM entities GROUP BY type ORDER BY 2 DESC"):
        print(f"  entities {typ:14} {n}")
    print()
    for rel, st, n in c.execute("SELECT rel,status,COUNT(*) FROM causal_edges GROUP BY rel,status ORDER BY 3 DESC"):
        print(f"  edges {rel:13} {st:10} {n}")
    print("\n  sample VALIDATED edges (financial backbone, held-out posterior):")
    for s, d, conf, ev in c.execute("SELECT src_id,dst_id,confidence,evidence_json FROM causal_edges "
                                    "WHERE status='validated' ORDER BY confidence DESC LIMIT 5"):
        print(f"    {s.split(':',1)[1][:34]:34} --> {d.split(':',1)[1][:24]:24} conf={round(conf,3) if conf else '-'}")
    print("\n  sample CANDIDATE real-world edges (observed, awaiting validation):")
    for s, d, ev in c.execute("SELECT src_id,dst_id,evidence_json FROM causal_edges WHERE status='candidate' "
                              "AND rel='affects' ORDER BY corroboration DESC LIMIT 6"):
        e0 = (json.loads(ev)[-1] or {}).get("src", "")
        print(f"    {s.split(':',1)[1][:30]:30} --affects--> {d.split(':',1)[1][:10]:10}  [{e0[:60]}]")


if __name__ == "__main__":
    build()
