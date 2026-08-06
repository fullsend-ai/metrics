#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

START_DATE="${1:-2026-04-06}"
END_DATE="${2:-$(date -d yesterday +%Y-%m-%d)}"

echo "Backfilling community metrics from ${START_DATE} to ${END_DATE}..."

# PR review activity is scanned once across the whole range up front rather
# than once per day, to avoid re-fetching the same PRs' review history on
# every iteration of the day loop below (see backfill-collect-reviews.sh).
REVIEW_COUNTS_FILE=$(mktemp)
trap 'rm -f "$REVIEW_COUNTS_FILE"' EXIT
echo "=== Scanning PR review activity (single pass) ==="
"${SCRIPT_DIR}/backfill-collect-reviews.sh" "$START_DATE" "$END_DATE" "$REVIEW_COUNTS_FILE"
export COMMUNITY_REVIEW_COUNTS_FILE="$REVIEW_COUNTS_FILE"

current="$START_DATE"
while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
  echo "=== ${current} ==="
  "${SCRIPT_DIR}/collect-community.sh" "$current"
  current=$(date -d "$current + 1 day" +%Y-%m-%d)
  sleep 3
done

row_count=$(tail -n +2 docs/community.csv | wc -l)
echo "Backfill complete. ${row_count} rows in docs/community.csv."
