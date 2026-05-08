#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

START_DATE="${1:-2026-04-06}"
END_DATE="${2:-$(date -d yesterday +%Y-%m-%d)}"

echo "Backfilling rework metrics from ${START_DATE} to ${END_DATE}..."

ensure_rework_csv

current="$START_DATE"
while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
  # collect-rework.sh skips dates already present.
  echo "=== ${current} ==="
  "${SCRIPT_DIR}/collect-rework.sh" "$current"

  # Small delay to stay within API rate limits.
  sleep 5

  current=$(date -d "${current} + 1 day" +%Y-%m-%d)
done

# Sort rework.csv by date,bot (keep header first).
header=$(head -1 "$REWORK_FILE")
tail -n +2 "$REWORK_FILE" | sort -t, -k1,1 -k2,2 > /tmp/rework_sorted.csv
echo "$header" > "$REWORK_FILE"
cat /tmp/rework_sorted.csv >> "$REWORK_FILE"
rm -f /tmp/rework_sorted.csv

# Sort rework-details.csv by datetime (keep header first).
header=$(head -1 "$REWORK_DETAILS_FILE")
tail -n +2 "$REWORK_DETAILS_FILE" | sort -t, -k1,1 > /tmp/rework_details_sorted.csv
echo "$header" > "$REWORK_DETAILS_FILE"
cat /tmp/rework_details_sorted.csv >> "$REWORK_DETAILS_FILE"
rm -f /tmp/rework_details_sorted.csv

echo "Backfill complete. $(( $(wc -l < "$REWORK_FILE") - 1 )) summary rows in ${REWORK_FILE}."
