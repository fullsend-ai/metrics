#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

START_DATE="${1:?Usage: $0 START_DATE [END_DATE]}"
END_DATE="${2:-$(date -d yesterday +%Y-%m-%d)}"

echo "Backfilling failure metrics from ${START_DATE} to ${END_DATE}..."

current="$START_DATE"
while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
  echo "=== ${current} ==="
  "${SCRIPT_DIR}/collect-failures.sh" "$current"
  current=$(date -d "$current + 1 day" +%Y-%m-%d)
  sleep 2
done

row_count=$(tail -n +2 docs/failures.csv | wc -l)
echo "Backfill complete. ${row_count} rows in docs/failures.csv."
