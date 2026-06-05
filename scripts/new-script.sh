#!/usr/bin/env bash
# new-script.sh — scaffold a new governed script with the wrapper guard baked in.
# Usage:
#   new-script.sh <name>.sh   [governed-dir-path-or-shortname]
#   new-script.sh <name>.py   [governed-dir-path-or-shortname]
#
# Default target dir = ~/.openclaw/scripts (the canonical ops dir).
# Pass an alternate dir (must already be listed in scripts/policy.json governedDirs).
#
# Intentionally NOT wrapper-guarded — this is a developer tool.

set -euo pipefail

POLICY_FILE="$HOME/.openclaw/scripts/policy.json"

usage() {
  cat <<EOF
Usage: $(basename "$0") <name.sh|name.py> [target-dir]

Creates a new script in a governed directory with the require-wrapper guard
already in place, makes it executable, and prints the run-with-trace invocation.

Examples:
  new-script.sh check-foo.sh
  new-script.sh report.py ~/.openclaw/workspaces/trader/scripts
EOF
}

if [[ $# -lt 1 || "$1" == "-h" || "$1" == "--help" ]]; then
  usage
  exit 2
fi

NAME="$1"
TARGET_DIR="${2:-$HOME/.openclaw/scripts}"
TARGET_DIR="${TARGET_DIR/#\~/$HOME}"

if ! command -v jq >/dev/null 2>&1; then
  echo "new-script: jq is required" >&2
  exit 2
fi

# Verify target dir is registered as governed.
registered=$(jq -r --arg p "$TARGET_DIR" --arg home "$HOME" '
  [.governedDirs[].path | sub("^~"; $home)] | index($p) // empty
' "$POLICY_FILE")
if [[ -z "$registered" ]]; then
  echo "new-script: target dir is not in governedDirs of $POLICY_FILE: $TARGET_DIR" >&2
  echo "Register it first or pick an existing governed dir:" >&2
  jq -r '.governedDirs[].path' "$POLICY_FILE" | sed 's/^/  - /' >&2
  exit 2
fi

if [[ ! -d "$TARGET_DIR" ]]; then
  echo "new-script: target dir does not exist: $TARGET_DIR" >&2
  exit 2
fi

DEST="$TARGET_DIR/$NAME"
if [[ -e "$DEST" ]]; then
  echo "new-script: refusing to overwrite existing file: $DEST" >&2
  exit 2
fi

case "$NAME" in
  *.sh)
    cat > "$DEST" <<'EOF'
#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail

# TODO: implement
echo "TODO: $(basename "$0")"
EOF
    ;;
  *.py)
    cat > "$DEST" <<'EOF'
#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()

"""TODO: describe this script."""


def main() -> int:
    # TODO: implement
    print("TODO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
EOF
    ;;
  *)
    echo "new-script: name must end in .sh or .py" >&2
    exit 2
    ;;
esac

chmod +x "$DEST"
echo "created: $DEST"
echo ""
echo "Run via:"
echo "  ~/.openclaw/scripts/run-with-trace.sh $DEST"
