#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

START_DATE="${1:-2026-04-06}"
END_DATE="${2:-$(date -d yesterday +%Y-%m-%d)}"

echo "Backfilling failure details from ${START_DATE} to ${END_DATE}..."

ensure_failure_details_csv
existing_lines=$(wc -l < "$FAILURE_DETAILS_FILE")

AGENT_REPO="fullsend-ai/.fullsend"

AGENT_WORKFLOWS=(
  "Code"
  "Fix"
  "Review"
  "Triage"
  "Prioritize"
  "Retro"
  "Scribe"
  "Classify GitHub Issues"
)

declare -A is_agent
for wf in "${AGENT_WORKFLOWS[@]}"; do
  is_agent["$wf"]=1
done

current="$START_DATE"
while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
  # Skip if this date already has data.
  if grep -q "^${current}," "$FAILURE_DETAILS_FILE" 2>/dev/null; then
    echo "  ${current}: already exists, skipping."
    current=$(date -d "${current} + 1 day" +%Y-%m-%d)
    continue
  fi

  echo "  ${current}: fetching workflow runs..."
  runs=$(gh api "/repos/${AGENT_REPO}/actions/runs?per_page=100&created=${current}" \
    --paginate \
    --jq '.workflow_runs[] | select(.status == "completed") | [.name, .conclusion, (.id | tostring), .html_url] | @tsv' 2>/dev/null || true)

  if [[ -n "$runs" ]]; then
    while IFS=$'\t' read -r name conclusion run_id run_url; do
      [[ -z "${is_agent[$name]+x}" ]] && continue
      if [[ "$conclusion" == "failure" ]]; then
        append_failure_detail "$current" "$name" ".fullsend" "$run_id" "failure" "$run_url"
      else
        append_failure_detail "$current" "$name" ".fullsend" "$run_id" "success" "$run_url"
      fi
    done <<< "$runs"
  fi

  sleep 2
  current=$(date -d "${current} + 1 day" +%Y-%m-%d)
done

# Merge the new rows in by date; existing rows stay where they are.
merge_appended_rows "$FAILURE_DETAILS_FILE" "$existing_lines" -k1,1

echo "Backfill complete. $(( $(wc -l < "$FAILURE_DETAILS_FILE") - 1 )) detail rows."
