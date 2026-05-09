#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

TARGET_DATE="${1:-$(date -d yesterday +%Y-%m-%d)}"
echo "Collecting failure metrics for ${TARGET_DATE}..."

ensure_failure_csv

# Skip if this date already has data.
if grep -q "^${TARGET_DATE}," "$FAILURE_FILE" 2>/dev/null; then
  echo "Data for ${TARGET_DATE} already exists in ${FAILURE_FILE}. Skipping."
  exit 0
fi

AGENT_REPO="fullsend-ai/.fullsend"

# Agent workflow names (exclude Dispatch, Repo Maintenance, Prioritize Scheduler).
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

# Fetch all completed workflow runs for the target date.
runs=$(gh api "/repos/${AGENT_REPO}/actions/runs?per_page=100&created=${TARGET_DATE}" \
  --paginate \
  --jq '.workflow_runs[] | select(.status == "completed") | [.name, .conclusion] | @tsv' 2>/dev/null || true)

if [[ -z "$runs" ]]; then
  echo "No workflow runs found for ${TARGET_DATE}."
  exit 0
fi

# Build an associative array of agent workflow names for fast lookup.
declare -A is_agent
for wf in "${AGENT_WORKFLOWS[@]}"; do
  is_agent["$wf"]=1
done

# Count runs and failures per workflow.
declare -A total_runs
declare -A failed_runs

while IFS=$'\t' read -r name conclusion; do
  [[ -z "${is_agent[$name]+x}" ]] && continue
  total_runs["$name"]=$(( ${total_runs["$name"]:-0} + 1 ))
  if [[ "$conclusion" == "failure" ]]; then
    failed_runs["$name"]=$(( ${failed_runs["$name"]:-0} + 1 ))
  fi
done <<< "$runs"

# Write rows.
for wf in "${AGENT_WORKFLOWS[@]}"; do
  count=${total_runs["$wf"]:-0}
  (( count == 0 )) && continue
  fails=${failed_runs["$wf"]:-0}
  rate=$(awk "BEGIN { printf \"%.3f\", ${fails} / ${count} }")
  append_failure_row "$TARGET_DATE" "$wf" "$count" "$fails" "$rate"
done

echo "Done. Failure metrics for ${TARGET_DATE} written."
