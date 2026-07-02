#!/usr/bin/env python3
"""edgar_deltas.py — the "Lazy Prices" filing-change signal (Cohen–Malloy–Nguyen 2020).

Firms that materially CHANGE the language of their 10-K/10-Q underperform firms that
don't; the market is slow to read filings. The construction is deterministic and needs
no LLM: textual similarity of each filing vs the company's PRIOR filing of the same
form. We store, point-in-time at the SEC filingDate:

  filing_delta   = 1 - Jaccard(similarity of normalized 8-word shingles), MinHash-512
                   estimated. 0 = boilerplate copy of last time; high = big rewrite.
                   Expected sign per the paper: NEGATIVE (changers underperform).

Free EDGAR data, fair-use paced (<=8 req/s, proper UA). Per-accession MinHash
signatures cached (~4KB each) in state/edgar-minhash/ so each filing is fetched and
hashed exactly once, ever. The ml_ranker + FDR harness judge the column like any other.

  python3 edgar_deltas.py backfill --top-n 150 --since 2022-01-01
  python3 edgar_deltas.py daily --top-n 150            # new filings in last 5 days
"""
from __future__ import annotations

import argparse, hashlib, json, os, re, sqlite3, struct, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors import edgar  # noqa: E402  (cik_for, _UA)

FEAT_DB = os.path.expanduser("~/.openclaw/state/features.sqlite")
SIG_DIR = os.path.expanduser("~/.openclaw/state/edgar-minhash")
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"
N_HASH = 512
SHINGLE_W = 8
FORMS = ("10-K", "10-Q")
_PACING = 0.15          # ~6-7 req/s, inside SEC fair-use


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=edgar._UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    time.sleep(_PACING)
    return data


def _filings(cik: int, since: str) -> list[dict]:
    raw = json.loads(_get(SUBMISSIONS_URL.format(cik=cik)))
    rec = raw.get("filings", {}).get("recent", {})
    out = []
    for form, fdate, acc, doc in zip(rec.get("form", []), rec.get("filingDate", []),
                                     rec.get("accessionNumber", []), rec.get("primaryDocument", [])):
        if form in FORMS and fdate >= since and doc:
            out.append({"form": form, "date": fdate, "acc": acc, "doc": doc})
    return sorted(out, key=lambda f: f["date"])


def _normalize(html: bytes) -> str:
    txt = html.decode("utf-8", errors="ignore")
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", txt, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"&[a-z#0-9]+;", " ", txt)
    txt = txt.lower()
    txt = re.sub(r"[0-9][0-9,.\-%$()]*", " ", txt)   # numbers change every period; language is the signal
    return re.sub(r"[^a-z ]+", " ", txt)


def _minhash(text: str) -> list[int] | None:
    words = text.split()
    if len(words) < 200:
        return None
    mins = [2**64 - 1] * N_HASH
    step = N_HASH.bit_length()
    for i in range(len(words) - SHINGLE_W + 1):
        sh = " ".join(words[i:i + SHINGLE_W]).encode()
        h = int.from_bytes(hashlib.blake2b(sh, digest_size=8).digest(), "big")
        # cheap family of hash funcs: xor-shift the base hash with per-slot constants
        for k in range(N_HASH):
            hk = h ^ _SALTS[k]
            if hk < mins[k]:
                mins[k] = hk
    return mins


_SALTS = [int.from_bytes(hashlib.blake2b(str(k).encode(), digest_size=8).digest(), "big")
          for k in range(N_HASH)]


def _sig_path(acc: str) -> str:
    return os.path.join(SIG_DIR, acc.replace("-", "") + ".sig")


def _load_sig(acc: str) -> list[int] | None:
    p = _sig_path(acc)
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        data = f.read()
    if len(data) != N_HASH * 8:
        return None
    return list(struct.unpack(f">{N_HASH}Q", data))


def _save_sig(acc: str, sig: list[int]):
    os.makedirs(SIG_DIR, exist_ok=True)
    with open(_sig_path(acc), "wb") as f:
        f.write(struct.pack(f">{N_HASH}Q", *sig))


def _signature(cik: int, f: dict) -> list[int] | None:
    sig = _load_sig(f["acc"])
    if sig:
        return sig
    try:
        html = _get(DOC_URL.format(cik=cik, acc_nodash=f["acc"].replace("-", ""), doc=f["doc"]))
    except Exception as e:
        print(f"    fetch fail {f['acc']}: {str(e)[:60]}", file=sys.stderr)
        return None
    sig = _minhash(_normalize(html))
    if sig:
        _save_sig(f["acc"], sig)
    return sig


def process_ticker(conn, ticker: str, since: str) -> int:
    try:
        cik = edgar.cik_for(ticker)
        filings = _filings(cik, since)
    except Exception as e:
        print(f"  {ticker}: skip ({str(e)[:60]})", file=sys.stderr)
        return 0
    rows = []
    prev_by_form: dict[str, list[int]] = {}
    for f in filings:
        sig = _signature(cik, f)
        if not sig:
            continue
        prev = prev_by_form.get(f["form"])
        prev_by_form[f["form"]] = sig
        if not prev:
            continue
        sim = sum(1 for a, b in zip(sig, prev) if a == b) / N_HASH
        rows.append((ticker, f["date"], "filing_delta", round(1.0 - sim, 4), f["date"], "edgar"))
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO features(ticker,as_of,name,value,knowable_at,source) VALUES(?,?,?,?,?,?)", rows)
        conn.commit()
    return len(rows)


def _top_names(n):
    conn = sqlite3.connect(f"file:{FEAT_DB}?mode=ro", uri=True)
    names = [r[0] for r in conn.execute(
        "SELECT symbol FROM universe WHERE status='active' AND market_cap IS NOT NULL "
        "ORDER BY market_cap DESC LIMIT ?", (n,))]
    conn.close()
    return names


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--top-n", type=int, default=150)
    b.add_argument("--names")
    b.add_argument("--since", default="2022-01-01")
    d = sub.add_parser("daily")
    d.add_argument("--top-n", type=int, default=150)
    a = ap.parse_args()
    names = ([s.strip().upper() for s in a.names.split(",")] if getattr(a, "names", None)
             else _top_names(a.top_n))
    # NOTE: to compare vs a prior filing, daily mode still walks history — the signature
    # cache makes that nearly free after the first backfill.
    since = a.since if a.cmd == "backfill" else "2022-01-01"
    conn = sqlite3.connect(FEAT_DB)
    total = 0
    for i, t in enumerate(names):
        total += process_ticker(conn, t, since)
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(names)} names, {total} delta rows", flush=True)
    conn.close()
    print(f"done: {total} filing_delta rows across {len(names)} names")


if __name__ == "__main__":
    main()
