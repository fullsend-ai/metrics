#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

START_DATE="${1:-2025-04-01}"
END_DATE="${2:-$(date -d yesterday +%Y-%m-%d)}"

echo "Backfilling metrics from ${START_DATE} to ${END_DATE}..."

ensure_csv

current="$START_DATE"
while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
  # Skip dates already in the CSV (checkpoint/resume support).
  if grep -q "^${current}," "$DATA_FILE" 2>/dev/null; then
    echo "Skipping ${current} (already collected)"
    current=$(date -d "${current} + 1 day" +%Y-%m-%d)
    continue
  fi

  "${SCRIPT_DIR}/collect.sh" "$current"

  # Rate limit: GitHub search API allows 30 req/min for authenticated users.
  # Each repo needs ~6 search queries + N PR detail queries.
  # Sleep between days to stay under limits.
  echo "Sleeping 60s for rate limits..."
  sleep 60

  current=$(date -d "${current} + 1 day" +%Y-%m-%d)
done

echo "Backfill complete."
