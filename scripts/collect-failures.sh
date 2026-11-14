#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

TARGET_DATE="${1:-$(date -d yesterday +%Y-%m-%d)}"
echo "Collecting failure metrics for ${TARGET_DATE}..."

ensure_failure_csv
ensure_failure_details_csv

# Skip if this date already has data.
if grep -q "^${TARGET_DATE}," "$FAILURE_FILE" 2>/dev/null; then
  echo "Data for ${TARGET_DATE} already exists in ${FAILURE_FILE}. Skipping."
  exit 0
fi

# Agents run in each repo's fullsend dispatch workflow (see lib.sh).
# Collect everything before writing, so a failed lookup writes nothing.
if ! runs=$(collect_agent_runs "$TARGET_DATE"); then
  echo "::error::Could not collect agent runs for ${TARGET_DATE}; nothing was written."
  exit 1
fi

if [[ -z "$runs" ]]; then
  echo "::warning::No agent jobs ran in any ${ORG} repo's ${AGENT_DISPATCH_WORKFLOW} on ${TARGET_DATE}. Agents normally run every day: check whether the dispatch workflow or its job names changed."
  exit 0
fi

# Count runs and failures per role.
declare -A total_runs
declare -A failed_runs
roles=("${AGENT_ROLES[@]}")

while IFS=$'\t' read -r role repo run_id status run_url; do
  if [[ -z "${total_runs["$role"]+x}" ]]; then
    total_runs["$role"]=0
    failed_runs["$role"]=0
    # Harness agents are not in AGENT_ROLES; report them after the roles.
    [[ " ${AGENT_ROLES[*]} " == *" ${role} "* ]] || roles+=("$role")
  fi
  total_runs["$role"]=$(( ${total_runs["$role"]} + 1 ))
  if [[ "$status" == "failure" ]]; then
    failed_runs["$role"]=$(( ${failed_runs["$role"]} + 1 ))
  fi
  append_failure_detail "$TARGET_DATE" "$role" "$repo" "$run_id" "$status" "$run_url"
done <<< "$runs"

# Write rows.
for role in "${roles[@]}"; do
  count=${total_runs["$role"]:-0}
  (( count == 0 )) && continue
  fails=${failed_runs["$role"]}
  rate=$(awk "BEGIN { printf \"%.3f\", ${fails} / ${count} }")
  append_failure_row "$TARGET_DATE" "$role" "$count" "$fails" "$rate"
done

echo "Done. Failure metrics for ${TARGET_DATE} written."
