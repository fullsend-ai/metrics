#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

# Accept a date argument, default to yesterday.
if [[ $# -ge 1 ]]; then
  TARGET_DATE="$1"
else
  TARGET_DATE="$(date -d yesterday +%Y-%m-%d)"
fi

echo "Collecting metrics for ${TARGET_DATE}..."

ensure_csv

# Ensure result is a non-negative integer; default to 0 on any error.
to_int() {
  local val="$1"
  if [[ "$val" =~ ^[0-9]+$ ]]; then
    echo "$val"
  else
    echo "0"
  fi
}

repos=$(list_repos)

for repo in $repos; do
  full_repo="${ORG}/${repo}"
  echo "  ${full_repo}..."

  prs_opened=$(to_int "$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:pr created:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || true)")

  prs_merged=$(to_int "$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:pr is:merged merged:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || true)")

  prs_closed_total=$(to_int "$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:pr is:closed closed:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || true)")
  prs_closed=$(( prs_closed_total - prs_merged ))
  if (( prs_closed < 0 )); then prs_closed=0; fi

  issues_opened=$(to_int "$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:issue created:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || true)")

  issues_closed=$(to_int "$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:issue is:closed closed:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || true)")

  releases=$(to_int "$(gh api "/repos/${full_repo}/releases" \
    --paginate \
    --jq "[.[] | select(.published_at | startswith(\"${TARGET_DATE}\"))] | length" 2>/dev/null || true)")

  lead_time_median="0"
  if (( prs_merged > 0 )); then
    lead_times=$(gh api "/search/issues" \
      --method GET \
      -f q="repo:${full_repo} is:pr is:merged merged:${TARGET_DATE}" \
      --jq '.items[].pull_request.url' 2>/dev/null | while read -r pr_url; do
        gh api "$pr_url" --jq '
          (((.merged_at | fromdateiso8601) - (.created_at | fromdateiso8601)) / 3600)
          | . * 10 | round / 10
        ' 2>/dev/null || true
      done)

    if [[ -n "$lead_times" ]]; then
      lead_time_median=$(echo "$lead_times" | median)
    fi
  fi

  append_row "$TARGET_DATE" "$repo" "$prs_opened" "$prs_merged" "$prs_closed" \
    "$issues_opened" "$issues_closed" "$releases" "$lead_time_median"
done

echo "Done. Metrics for ${TARGET_DATE} written to ${DATA_FILE}."
