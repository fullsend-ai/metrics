#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

START_DATE="${1:-2026-04-06}"
END_DATE="${2:-$(date -d yesterday +%Y-%m-%d)}"

echo "Backfilling failure details from ${START_DATE} to ${END_DATE}..."

ensure_failure_details_csv
existing_lines=$(wc -l < "$FAILURE_DETAILS_FILE")

current="$START_DATE"
while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
  # Skip if this date already has data.
  if grep -q "^${current}," "$FAILURE_DETAILS_FILE" 2>/dev/null; then
    echo "  ${current}: already exists, skipping."
    current=$(date -d "${current} + 1 day" +%Y-%m-%d)
    continue
  fi

  # Agents run in each repo's fullsend dispatch workflow (see lib.sh).
  echo "  ${current}: fetching agent runs..."
  if ! runs=$(collect_agent_runs "$current"); then
    echo "::error::Could not collect agent runs for ${current}; stopping. Rows for earlier dates are kept."
    exit 1
  fi

  if [[ -z "$runs" ]]; then
    echo "::warning::No agent jobs ran in any ${ORG} repo's ${AGENT_DISPATCH_WORKFLOW} on ${current}."
  else
    while IFS=$'\t' read -r role repo run_id status run_url; do
      append_failure_detail "$current" "$role" "$repo" "$run_id" "$status" "$run_url"
    done <<< "$runs"
  fi

  sleep 2
  current=$(date -d "${current} + 1 day" +%Y-%m-%d)
done

# Merge the new rows in by date; existing rows stay where they are.
merge_appended_rows "$FAILURE_DETAILS_FILE" "$existing_lines" -k1,1

echo "Backfill complete. $(( $(wc -l < "$FAILURE_DETAILS_FILE") - 1 )) detail rows."
