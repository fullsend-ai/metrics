#!/usr/bin/env bash
# Daily collector for conventional-commit PR type + fix-filer metrics.
# Usage: ./scripts/collect-pr-type.sh [YYYY-MM-DD]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

TARGET_DATE="${1:-$(date -d yesterday +%Y-%m-%d)}"
echo "Collecting PR type metrics for ${TARGET_DATE}..."

ensure_pr_type_csv

# Default to the two delivery repos; override with PR_TYPE_REPOS=repo1,repo2
export PR_TYPE_REPOS="${PR_TYPE_REPOS:-fullsend,agents}"

python3 "${SCRIPT_DIR}/collect-pr-type.py" --date "$TARGET_DATE"
