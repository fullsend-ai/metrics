#!/usr/bin/env bash
# Daily escaped-fix candidates + 30-day rate, after collect-pr-type.sh has run.
# Usage: ./scripts/collect-escaped-fixes.sh [YYYY-MM-DD]
# With no date it collects every day since docs/fix-candidates-through.txt, so a failed day is picked up next time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${1:-}" ]]; then
  echo "Collecting escaped-fix candidates for ${1}..."
  python3 "${SCRIPT_DIR}/collect-escaped-fixes.py" --date "$1"
else
  echo "Collecting escaped-fix candidates since the last collected day..."
  python3 "${SCRIPT_DIR}/collect-escaped-fixes.py" --catch-up
fi
