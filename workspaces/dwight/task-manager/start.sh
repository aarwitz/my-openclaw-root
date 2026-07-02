#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Starting Task Manager via Dwight container runtime..."
"$ROOT_DIR/scripts/tmctl.sh" start
