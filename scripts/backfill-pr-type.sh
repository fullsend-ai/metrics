#!/usr/bin/env bash
# Backfill PR type + fix-filer metrics across a date range.
# Usage: ./scripts/backfill-pr-type.sh [START] [END]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

START_DATE="${1:-2026-05-14}"
END_DATE="${2:-$(date -u -d yesterday +%Y-%m-%d)}"

echo "Backfilling PR type metrics from ${START_DATE} to ${END_DATE}..."

ensure_pr_type_csv
export PR_TYPE_REPOS="${PR_TYPE_REPOS:-fullsend,agents}"

python3 "${SCRIPT_DIR}/collect-pr-type.py" --from "$START_DATE" --to "$END_DATE" --force

row_count=$(tail -n +2 "$PR_TYPE_FILE" | wc -l)
detail_count=$(tail -n +2 "$PR_TYPE_DETAILS_FILE" | wc -l)
echo "Backfill complete. ${row_count} aggregate rows, ${detail_count} detail rows."
