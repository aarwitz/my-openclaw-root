"""Sheets adapter — wraps the `gog` CLI for idempotent Sheets I/O.

Spreadsheet: 19LPX1xGCme4umn22GN4Z7WBQxGZBWWcysDjM6JEW-D4
Tabs: Holdings, Watchlist, Candidates, Outcomes
Idempotency keys: Candidates=(date,ticker), Outcomes=(date_added,ticker)
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Optional

from ..schema import CANDIDATES_HEADER, OUTCOMES_HEADER

SHEET_ID = "19LPX1xGCme4umn22GN4Z7WBQxGZBWWcysDjM6JEW-D4"


class SheetsError(RuntimeError):
    pass


def _gog() -> str:
    g = shutil.which("gog")
    if not g:
        raise SheetsError("`gog` CLI not on PATH")
    return g


def _run(args: list[str]) -> str:
    proc = subprocess.run(
        [_gog(), *args],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise SheetsError(f"gog {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def get_range(tab: str, a1: str) -> list[list[str]]:
    """Return raw rows from `<tab>!<a1>` via `gog sheets get`."""
    raw = _run(["sheets", "get", SHEET_ID, f"{tab}!{a1}", "--json", "--results-only"])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SheetsError(f"could not parse gog output: {e}") from e
    if isinstance(data, dict):
        return data.get("values") or []
    if isinstance(data, list):
        return data
    return []


def append_row(tab: str, row: list) -> None:
    _run([
        "sheets", "append",
        SHEET_ID,
        f"{tab}!A:A",
        "--values-json", json.dumps([row]),
    ])


def update_row(tab: str, row_index: int, row: list) -> None:
    """`row_index` is 1-based incl. header (i.e. row 1 = header)."""
    last_col_letter = _col_letter(len(row))
    _run([
        "sheets", "update",
        SHEET_ID,
        f"{tab}!A{row_index}:{last_col_letter}{row_index}",
        "--values-json", json.dumps([row]),
    ])


def _col_letter(n: int) -> str:
    """1 → A, 26 → Z, 27 → AA."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ---------- typed write helpers (idempotent) ----------

def _row_from(record: dict, header: list[str]) -> list:
    out: list = []
    for k in header:
        v = record.get(k)
        if isinstance(v, bool):
            out.append("TRUE" if v else "FALSE")
        elif v is None:
            out.append("")
        else:
            out.append(v)
    return out


def upsert_candidate(record: dict, *, verify: bool = True) -> dict:
    """Write a Candidates row keyed by (date, ticker).

    Returns {action: "appended"|"updated"|"noop", row_index, verified}.
    """
    date = record.get("date"); ticker = record.get("ticker")
    if not date or not ticker:
        raise SheetsError("record missing date/ticker")
    rows = get_range("Candidates", "A:B")  # 1-based, includes header
    target_row: Optional[int] = None
    for i, r in enumerate(rows[1:], start=2):
        if len(r) >= 2 and r[0] == date and r[1] == ticker:
            target_row = i
            break

    row = _row_from(record, CANDIDATES_HEADER)

    if target_row is None:
        append_row("Candidates", row)
        action = "appended"
        # find new row index
        new_rows = get_range("Candidates", "A:B")
        target_row = len(new_rows)
    else:
        update_row("Candidates", target_row, row)
        action = "updated"

    verified = True
    if verify:
        check = get_range("Candidates", f"A{target_row}:B{target_row}")
        if not check or check[0][:2] != [date, ticker]:
            verified = False
    return {"action": action, "row_index": target_row, "verified": verified}


def upsert_outcome(record: dict, *, verify: bool = True) -> dict:
    date_added = record.get("date_added"); ticker = record.get("ticker")
    if not date_added or not ticker:
        raise SheetsError("outcome missing date_added/ticker")
    rows = get_range("Outcomes", "A:B")
    target_row: Optional[int] = None
    for i, r in enumerate(rows[1:], start=2):
        if len(r) >= 2 and r[0] == date_added and r[1] == ticker:
            target_row = i
            break

    row = _row_from(record, OUTCOMES_HEADER)

    if target_row is None:
        append_row("Outcomes", row)
        action = "appended"
        new_rows = get_range("Outcomes", "A:B")
        target_row = len(new_rows)
    else:
        update_row("Outcomes", target_row, row)
        action = "updated"

    verified = True
    if verify:
        check = get_range("Outcomes", f"A{target_row}:B{target_row}")
        if not check or check[0][:2] != [date_added, ticker]:
            verified = False
    return {"action": action, "row_index": target_row, "verified": verified}


def read_holdings() -> list[dict]:
    rows = get_range("Holdings", "A:Z")
    if not rows:
        return []
    header = [c.strip() for c in rows[0]]
    out: list[dict] = []
    for r in rows[1:]:
        if not any(r):
            continue
        d = {header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
        out.append(d)
    return out


def read_candidates_since(date: str) -> list[dict]:
    rows = get_range("Candidates", "A:ZZ")
    if not rows:
        return []
    header = [c.strip() for c in rows[0]]
    out: list[dict] = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        if r[0] >= date:
            d = {header[i]: (r[i] if i < len(r) else "") for i in range(len(header))}
            out.append(d)
    return out
