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

repos=$(list_repos)

for repo in $repos; do
  full_repo="${ORG}/${repo}"
  echo "  ${full_repo}..."

  # PRs opened on this date
  prs_opened=$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:pr created:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || echo "0")

  # PRs merged on this date
  prs_merged=$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:pr is:merged merged:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || echo "0")

  # PRs closed (not merged) on this date
  prs_closed_total=$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:pr is:closed closed:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || echo "0")
  prs_closed=$(( prs_closed_total - prs_merged ))
  if (( prs_closed < 0 )); then prs_closed=0; fi

  # Issues opened on this date (exclude PRs)
  issues_opened=$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:issue created:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || echo "0")

  # Issues closed on this date (exclude PRs)
  issues_closed=$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:issue is:closed closed:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || echo "0")

  # Releases published on this date
  releases=$(gh api "/repos/${full_repo}/releases" \
    --paginate \
    --jq "[.[] | select(.published_at | startswith(\"${TARGET_DATE}\"))] | length" 2>/dev/null || echo "0")

  # PR lead time: median hours from open to merge for PRs merged on this date.
  # Fetch the actual PR data to get created_at and merged_at timestamps.
  lead_time_median="0"
  if (( prs_merged > 0 )); then
    lead_times=$(gh api "/search/issues" \
      --method GET \
      -f q="repo:${full_repo} is:pr is:merged merged:${TARGET_DATE}" \
      --jq '.items[].pull_request.url' 2>/dev/null | while read -r pr_url; do
        # pr_url is the full API URL for the PR
        gh api "$pr_url" --jq '
          (((.merged_at | fromdateiso8601) - (.created_at | fromdateiso8601)) / 3600)
          | . * 10 | round / 10
        ' 2>/dev/null
      done)

    if [[ -n "$lead_times" ]]; then
      lead_time_median=$(echo "$lead_times" | median)
    fi
  fi

  append_row "$TARGET_DATE" "$repo" "$prs_opened" "$prs_merged" "$prs_closed" \
    "$issues_opened" "$issues_closed" "$releases" "$lead_time_median"
done

copy_csv_to_docs
echo "Done. Metrics for ${TARGET_DATE} written to ${DATA_FILE}."
