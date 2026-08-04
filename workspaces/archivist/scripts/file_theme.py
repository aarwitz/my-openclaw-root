#!/usr/bin/env python3
"""File/update a durable theme with optional evidence; no trading authority."""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
import theme_model as tm  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--id", required=True)
    p.add_argument("--statement", required=True)
    p.add_argument("--beneficiaries", default="[]")
    p.add_argument("--victims", default="[]")
    p.add_argument("--status", choices=sorted(tm.VALID_STATUS), default="watch")
    p.add_argument("--source", choices=sorted(tm.VALID_SOURCE), default="operator")
    p.add_argument("--created-by", default="human")
    p.add_argument("--created-at")
    p.add_argument("--evidence-ref", help="JSON object")
    p.add_argument("--source-id")
    p.add_argument("--source-type", default="operator")
    p.add_argument("--outcome", choices=sorted(tm.VALID_OUTCOME), default="context")
    p.add_argument("--ticker")
    p.add_argument("--move-pct", type=float)
    p.add_argument("--as-of")
    p.add_argument("--note")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    evidence = json.loads(args.evidence_ref) if args.evidence_ref else None
    observation = None
    if evidence or args.source_id or args.note:
        observation = {
            "source_type": args.source_type,
            "source_id": args.source_id,
            "outcome": args.outcome,
            "ticker": args.ticker,
            "move_pct": args.move_pct,
            "as_of": args.as_of,
            "evidence": evidence,
            "notes": args.note,
        }
    conn = tm.connect()
    try:
        result = tm.file_theme(
            conn, theme_id=args.id, statement=args.statement,
            beneficiaries=tm.parse_list(args.beneficiaries),
            victims=tm.parse_list(args.victims), status=args.status,
            source=args.source, created_by=args.created_by,
            created_at=args.created_at, observation=observation,
        )
        if args.dry_run:
            conn.rollback()
            result["dry_run"] = True
        else:
            conn.commit()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        conn.rollback()
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
