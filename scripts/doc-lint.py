#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()

"""doc-lint — weekly documentation-rot check (run from the Sunday overseer audit).

Deterministic checks over paths plus the small set of semantic contracts that
have repeatedly regressed in active agent memory.
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
AGENT_DOCS = sorted((ROOT / "workspaces").glob("*/AGENTS.md"))

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
    *AGENT_DOCS,
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
ABS_PATH_RE = re.compile(
    re.escape(str(ROOT))
    + r"/((?:workspaces|scripts|sql|docs|cron|state|tools|credentials)"
    + r"/[\w./-]+\.(?:py|sh|sql|md|json|ts|js|sqlite|jsonl))\b"
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
        path_matches = list(PATH_RE.finditer(text)) + list(ABS_PATH_RE.finditer(text))
        for m in sorted(path_matches, key=lambda item: item.start()):
            ref = m.group(1)
            if ref in seen or PLACEHOLDER_RE.search(ref):
                continue
            seen.add(ref)
            if not _resolve(ref, doc) and not marked_archived(m.start()):
                line = text.count("\n", 0, m.start()) + 1
                findings.append({"check": "path-ref", "doc": rel_doc, "line": line, "ref": ref,
                                 "detail": "referenced path does not exist"})

        if doc in AGENT_DOCS:
            version_pin = re.search(
                r"(?:topology|DB schema|schema)\s+v[0-9]+(?:/v[0-9]+)?",
                text,
                re.I,
            )
            if version_pin:
                line = text.count("\n", 0, version_pin.start()) + 1
                findings.append({
                    "check": "agent-version-pin",
                    "doc": rel_doc,
                    "line": line,
                    "ref": version_pin.group(0),
                    "detail": "agent memory must defer mutable topology/schema versions to SYSTEM_ARCHITECTURE.md",
                })

        for pat in SUPERSEDED:
            for m in pat.finditer(text):
                if marked_archived(m.start()):
                    continue
                line = text.count("\n", 0, m.start()) + 1
                findings.append({"check": "superseded", "doc": rel_doc, "line": line, "ref": m.group(0),
                                 "detail": "cites a retired doc without marking it archived"})

    # Decision ids must be unique — two agents appending on different days
    # re-used D52/D53 (caught 2026-07-15).
    dlog = TI / "DECISION_LOG.md"
    if dlog.exists():
        ids: dict[str, int] = {}
        text = dlog.read_text(errors="replace")
        for m in re.finditer(r"^- (D[\d.]+)[ :(]", text, re.M):
            ids[m.group(1)] = ids.get(m.group(1), 0) + 1
        for did, n in sorted(ids.items()):
            if n > 1:
                findings.append({"check": "dup-decision-id", "doc": "workspaces/trading-intel/DECISION_LOG.md",
                                 "ref": did, "detail": f"decision id used {n} times — renumber the later entry"})

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

    # Semantic architecture drift: these values have repeatedly remained stale
    # while code and migrations advanced, causing the chat agent to state old
    # limits and topology as current fact.
    architecture = ROOT / "SYSTEM_ARCHITECTURE.md"
    if architecture.exists():
        arch_text = architecture.read_text(errors="replace")
        migrations = sorted((TI / "sql" / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))
        latest_migration = migrations[-1].name[:4] if migrations else None
        migration_marker = re.search(r"migrations:\*\* through ([0-9]{4})", arch_text)
        if latest_migration and (
            not migration_marker or migration_marker.group(1) != latest_migration
        ):
            findings.append({
                "check": "architecture-migration-drift",
                "doc": "SYSTEM_ARCHITECTURE.md",
                "ref": migration_marker.group(1) if migration_marker else "missing",
                "detail": f"canonical marker must match latest migration {latest_migration}",
            })

        risk_source = (ROOT / "workspaces" / "risk" / "scripts" / "gate_risk_intents.py")
        risk_text = risk_source.read_text(errors="replace") if risk_source.exists() else ""
        code_cap = re.search(r"^MAX_POSITIONS\s*=\s*([0-9]+)", risk_text, re.M)
        doc_cap = re.search(r"Concurrent names:\*\* [^0-9]*([0-9]+)", arch_text)
        if code_cap and (not doc_cap or doc_cap.group(1) != code_cap.group(1)):
            findings.append({
                "check": "architecture-risk-cap-drift",
                "doc": "SYSTEM_ARCHITECTURE.md",
                "ref": doc_cap.group(1) if doc_cap else "missing",
                "detail": f"concurrent-name cap must match risk gate {code_cap.group(1)}",
            })

        required_shape = (
            "System shape — canonical short answer",
            "These are **not one unified graph**",
            "do not directly contain thesis, prediction",
        )
        for phrase in required_shape:
            if phrase not in arch_text:
                findings.append({
                    "check": "architecture-shape-contract",
                    "doc": "SYSTEM_ARCHITECTURE.md",
                    "ref": phrase,
                    "detail": "canonical short/graph boundary contract is missing",
                })

        topology = re.search(r"\*\*Topology:\*\* (v[0-9]+)", arch_text)
        identity = ROOT / "workspaces" / "overseer" / "IDENTITY.md"
        identity_text = identity.read_text(errors="replace") if identity.exists() else ""
        identity_topology = re.search(r"Topology version: (v[0-9]+)", identity_text)
        if topology and (
            not identity_topology or identity_topology.group(1) != topology.group(1)
        ):
            findings.append({
                "check": "architecture-topology-drift",
                "doc": "workspaces/overseer/IDENTITY.md",
                "ref": identity_topology.group(1) if identity_topology else "missing",
                "detail": f"overseer identity must match canonical {topology.group(1)}",
            })

    # Active vehicle policy must not contradict itself. The old implementation
    # policy simultaneously approved options in section 1 and deferred them in
    # section 2, which kept reviving unsupported expressions in agent prompts.
    implementation = TI / "docs" / "05_IMPLEMENTATION_POLICY.md"
    if implementation.exists():
        impl_text = implementation.read_text(errors="replace")
        approved = impl_text.split("## 2.", 1)[0]
        forbidden = re.search(r"\b(?:options?|LEAPS?|call spreads?)\b", approved, re.I)
        if forbidden:
            line = impl_text.count("\n", 0, forbidden.start()) + 1
            findings.append({
                "check": "unsupported-vehicle-policy",
                "doc": "workspaces/trading-intel/docs/05_IMPLEMENTATION_POLICY.md",
                "line": line,
                "ref": forbidden.group(0),
                "detail": "approved runtime actions may only include simulator-supported equity/ETF vehicles",
            })

    out = {"ok": not findings, "as_of": date.today().isoformat(), "n": len(findings), "findings": findings}
    print(json.dumps(out, indent=2))
    for f in findings:
        print(f"[doc-lint] {f['check']}: {f['doc']}:{f.get('line','?')} {f.get('ref','')} — {f['detail']}", file=sys.stderr)
    if not findings:
        print("[doc-lint] clean", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
