#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# rotate-logs.sh — bound disk growth of ~/.openclaw/logs.
#
# logrotate-style **copytruncate**: gzip a snapshot of each oversized log into
# logs/archive/, then truncate the original IN PLACE (`: > file`) so any process
# still holding the file open (availability-watchdog, jerry, run-with-trace)
# keeps appending to the same inode — no missed writes, no SIGHUP needed.
# Nothing is lost: the full history lives in the gzipped archive. Archives are
# pruned by age and by a total-size cap.
#
# Tunables (env): ROTATE_MAX_BYTES (per-file trigger), ROTATE_RETAIN_DAYS,
# ROTATE_MAX_ARCHIVE_MB (archive dir cap), ROTATE_LOG_DIR (override, for tests).

ROOT="$HOME/.openclaw"
LOG_DIR="${ROTATE_LOG_DIR:-$ROOT/logs}"
ARCHIVE_DIR="$LOG_DIR/archive"
MAX_BYTES="${ROTATE_MAX_BYTES:-5242880}"        # 5 MiB
RETAIN_DAYS="${ROTATE_RETAIN_DAYS:-14}"
MAX_ARCHIVE_MB="${ROTATE_MAX_ARCHIVE_MB:-200}"

mkdir -p "$ARCHIVE_DIR"
shopt -s nullglob

rotated=0
for f in "$LOG_DIR"/*.log "$LOG_DIR"/*.jsonl; do
  [ -f "$f" ] || continue
  sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
  if [ "$sz" -gt "$MAX_BYTES" ]; then
    ts=$(date -u +%Y%m%dT%H%M%SZ)
    base=$(basename "$f")
    if gzip -c "$f" > "$ARCHIVE_DIR/${base}.${ts}.gz"; then
      : > "$f"                                   # copytruncate (keep the inode)
      rotated=$((rotated + 1))
      echo "rotated $base (${sz}B) -> archive/${base}.${ts}.gz"
    else
      echo "WARN: failed to archive $base; left intact" >&2
    fi
  fi
done

# Retention: prune by age, then enforce a total archive-size cap (oldest first).
find "$ARCHIVE_DIR" -type f -name '*.gz' -mtime +"$RETAIN_DAYS" -delete 2>/dev/null || true
cap_bytes=$((MAX_ARCHIVE_MB * 1024 * 1024))
while :; do
  total=$(du -sb "$ARCHIVE_DIR" 2>/dev/null | cut -f1 || echo 0)
  [ "${total:-0}" -le "$cap_bytes" ] && break
  oldest=$(ls -1tr "$ARCHIVE_DIR"/*.gz 2>/dev/null | head -1 || true)
  [ -z "$oldest" ] && break
  rm -f "$oldest" && echo "pruned (size cap) $(basename "$oldest")"
done

echo "rotate-logs: rotated=$rotated archive_total=$(du -sh "$ARCHIVE_DIR" 2>/dev/null | cut -f1 || echo '0')"
