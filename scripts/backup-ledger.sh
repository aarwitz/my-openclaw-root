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
# D57: offsite copy — one disk must never hold the account AND all its backups.
# mac-dev is a laptop (often asleep): soft-fail, stamp on success, sweep warns
# when the marker goes >48h stale.
OFFSITE="taylorolsen-vogt@100.125.133.123"
NEWEST=$(ls -t "$DST"/trading-intel-*.sqlite | head -1)
if timeout 120 rsync -az -e "ssh -o BatchMode=yes -o ConnectTimeout=8" \
    "$NEWEST" "$OFFSITE:openclaw-backups/ledger/" 2>/dev/null; then
  touch "$DST/.last-offsite"
  echo "offsite ok: $(basename "$NEWEST")"
else
  echo "offsite SKIPPED (mac unreachable) — sweep will warn at 48h"
fi

echo "ledger backup complete $STAMP"
