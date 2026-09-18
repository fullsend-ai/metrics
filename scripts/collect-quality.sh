#!/usr/bin/env bash
# Derive PR-based quality proxies after collect-pr-type.sh has run.
# Usage: ./scripts/collect-quality.sh [YYYY-MM-DD]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"
TARGET_DATE="${1:-$(date -d yesterday +%Y-%m-%d)}"
echo "Collecting quality signals for ${TARGET_DATE}..."
ensure_quality_csv
python3 "${SCRIPT_DIR}/collect-quality.py" --date "$TARGET_DATE"
