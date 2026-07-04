#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()

"""doc-lint — weekly documentation-rot check (run from the Sunday overseer audit).

Deterministic checks only; contradictions in *meaning* are the reviewing agent's job.
  1. path-refs   : repo-relative paths mentioned in living docs that no longer exist
  2. superseded  : living docs still citing archived docs (they were retired 2026-07-02)
  3. revalidate  : FINDINGS.md quantitative claims whose `revalidate-by:` date has passed
  4. doc-index   : DOC_INDEX.md rows pointing at missing files (covered by path-refs)

Report-only: always exits 0 unless the lint itself crashes. Output is one JSON object
on stdout plus human-readable lines on stderr, so the weekly audit can quote it.
"""

import json
import os
import re
from datetime import date
from pathlib import Path

ROOT = Path.home() / ".openclaw"
TI = ROOT / "workspaces" / "trading-intel"

# Living docs to scan for stale path references and superseded citations.
LIVING_DOCS = [
    ROOT / "CLAUDE.md",
    ROOT / "SYSTEM_ARCHITECTURE.md",
    ROOT / "TELEGRAM_EXECUTION_GUIDE.md",
    TI / "DOC_INDEX.md",
    TI / "DATA_SOURCES.md",
    TI / "DECISION_LOG.md",
    TI / "FINDINGS.md",
    TI / "OPERATOR_GUIDE.md",
    TI / "HUMAN_USE_GUIDE.md",
    *sorted((TI / "docs").glob("*.md")),
]

# Word-boundary patterns so ARCHITECTURE.md never matches inside SYSTEM_ARCHITECTURE.md.
SUPERSEDED = [re.compile(r"(?<![\w/])" + re.escape(n)) for n in
              ("ARCHITECTURE.md", "FULL_DESIGN_ASCII.md", "02_ARCHITECTURE.md")]

# Append-only history: never retro-edited, so stale refs there are expected.
HISTORY_DOCS = {"DECISION_LOG.md", "FINDINGS.md"}

ARCHIVED_CTX = re.compile(r"archiv|supersed|retired|formerly|historical", re.I)

# Repo-relative path patterns worth verifying. Deliberately conservative: only
# paths under known top-level dirs, with a file extension, no globs/placeholders.
PATH_RE = re.compile(
    r"(?<![\w/])((?:workspaces|scripts|sql|docs|cron|state|tools|credentials)"
    r"/[\w./-]+\.(?:py|sh|sql|md|json|ts|js|sqlite|jsonl))\b"
)
PLACEHOLDER_RE = re.compile(r"(NNNN|<[^>]+>|\{[^}]+\}|\*|\.\.\.|XXX)")

REVAL_RE = re.compile(r"revalidate-by:\s*(\d{4}-\d{2}-\d{2})")


def _resolve(ref: str, doc: Path) -> bool:
    """A ref counts as live if it exists relative to ~/.openclaw, the doc's own
    directory, or the trading-intel workspace (docs there cite sql/ and docs/
    relative to the workspace root)."""
    for base in (ROOT, doc.parent, TI):
        if (base / ref).exists():
            return True
    return False


def main() -> int:
    findings = []

    for doc in LIVING_DOCS:
        if not doc.exists():
            findings.append({"check": "living-doc-missing", "doc": str(doc), "detail": "listed in doc-lint but absent"})
            continue
        if doc.name in HISTORY_DOCS:
            continue
        text = doc.read_text(errors="replace")
        rel_doc = os.path.relpath(doc, ROOT)

        def marked_archived(pos: int) -> bool:
            # a mention is fine if the surrounding sentence marks it archived/superseded
            return bool(ARCHIVED_CTX.search(text[max(0, pos - 120):pos + 160]))

        seen = set()
        for m in PATH_RE.finditer(text):
            ref = m.group(1)
            if ref in seen or PLACEHOLDER_RE.search(ref):
                continue
            seen.add(ref)
            if not _resolve(ref, doc) and not marked_archived(m.start()):
                line = text.count("\n", 0, m.start()) + 1
                findings.append({"check": "path-ref", "doc": rel_doc, "line": line, "ref": ref,
                                 "detail": "referenced path does not exist"})

        for pat in SUPERSEDED:
            for m in pat.finditer(text):
                if marked_archived(m.start()):
                    continue
                line = text.count("\n", 0, m.start()) + 1
                findings.append({"check": "superseded", "doc": rel_doc, "line": line, "ref": m.group(0),
                                 "detail": "cites a retired doc without marking it archived"})

    fnd = TI / "FINDINGS.md"
    if fnd.exists():
        text = fnd.read_text(errors="replace")
        today = date.today().isoformat()
        for m in REVAL_RE.finditer(text):
            if m.group(1) < today:
                line = text.count("\n", 0, m.start()) + 1
                # nearest section heading above the tag names the finding
                head = re.findall(r"^## (.+)$", text[: m.start()], re.M)
                findings.append({"check": "revalidate", "doc": "workspaces/trading-intel/FINDINGS.md",
                                 "line": line, "ref": head[-1] if head else "?",
                                 "detail": f"revalidate-by {m.group(1)} has passed — re-verify or revise the claim"})

    out = {"ok": not findings, "as_of": date.today().isoformat(), "n": len(findings), "findings": findings}
    print(json.dumps(out, indent=2))
    for f in findings:
        print(f"[doc-lint] {f['check']}: {f['doc']}:{f.get('line','?')} {f.get('ref','')} — {f['detail']}", file=sys.stderr)
    if not findings:
        print("[doc-lint] clean", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
