#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -uo pipefail

# backup-ledger.sh — WAL-safe daily backups of the desk's canonical state (D54).
#
# Since the D52 cutover the internal ledger IS the brokerage: positions, cash,
# fills, and the equity curve live in state/trading-intel.sqlite. Losing it is
# no longer "re-sync from Alpaca" — it is the account ceasing to exist.
#
# - sqlite3 online-backup API via python (safe under WAL, no locking games)
# - integrity_check on the COPY before it counts as a backup
# - retention: 14 daily + first-of-month kept 12 months
# - features.sqlite (5.5GB, reproducible from vendors) gets weekly VACUUM INTO

OC="$HOME/.openclaw"
DST="$OC/backups/ledger"
mkdir -p "$DST"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

python3 - "$OC/state/trading-intel.sqlite" "$DST/trading-intel-$STAMP.sqlite" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src)
d = sqlite3.connect(dst)
s.backup(d)
d.close()
chk = sqlite3.connect(dst).execute("PRAGMA integrity_check").fetchone()[0]
if chk != "ok":
    print(f"FATAL: backup integrity_check={chk}", file=sys.stderr)
    sys.exit(1)
rows = sqlite3.connect(dst).execute("SELECT COUNT(*) FROM sim_positions").fetchone()[0]
print(f"backup ok: {dst} (integrity ok, sim_positions={rows})")
PY
rc=$?
[[ $rc -ne 0 ]] && exit 1

# retention: keep 14 newest dailies; keep any *01T* (first-of-month) up to 365d
cd "$DST"
ls -t trading-intel-*.sqlite 2>/dev/null | tail -n +15 | grep -v "^trading-intel-....01T" | xargs -r rm -f
find . -name "trading-intel-*01T*.sqlite" -mtime +365 -delete 2>/dev/null

# weekly features.sqlite snapshot (Sundays)
if [[ "$(date +%u)" == "7" ]]; then
  python3 - "$OC/state/features.sqlite" "$DST/features-$STAMP.sqlite" <<'PY'
import sqlite3, sys
s = sqlite3.connect(sys.argv[1])
s.execute(f"VACUUM INTO '{sys.argv[2]}'")
print(f"features snapshot ok: {sys.argv[2]}")
PY
  ls -t features-*.sqlite 2>/dev/null | tail -n +3 | xargs -r rm -f
fi
echo "ledger backup complete $STAMP"
