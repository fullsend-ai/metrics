#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

START_DATE="${1:-2026-04-06}"
END_DATE="${2:-$(date -d yesterday +%Y-%m-%d)}"

echo "Backfilling metric details from ${START_DATE} to ${END_DATE}..."

ensure_metric_details_csv

repos=$(list_repos)

current="$START_DATE"
while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
  # Skip if this date already has data.
  if grep -q "^${current}," "$METRIC_DETAILS_FILE" 2>/dev/null; then
    echo "  ${current}: already exists, skipping."
    current=$(date -d "${current} + 1 day" +%Y-%m-%d)
    continue
  fi

  echo "  ${current}: fetching PR/issue events..."

  for repo in $repos; do
    full_repo="${ORG}/${repo}"

    # PRs opened
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:pr created:${current}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$current" "$repo" "pr" "opened" "$number" "$title" "$url"
    done

    # PRs merged
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:pr is:merged merged:${current}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$current" "$repo" "pr" "merged" "$number" "$title" "$url"
    done

    # PRs closed (non-merge)
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:pr is:closed is:unmerged closed:${current}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$current" "$repo" "pr" "closed" "$number" "$title" "$url"
    done

    # Issues opened
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:issue created:${current}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$current" "$repo" "issue" "opened" "$number" "$title" "$url"
    done

    # Issues closed
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:issue is:closed closed:${current}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$current" "$repo" "issue" "closed" "$number" "$title" "$url"
    done
  done

  sleep 3
  current=$(date -d "${current} + 1 day" +%Y-%m-%d)
done

# Sort by date (keep header first).
header=$(head -1 "$METRIC_DETAILS_FILE")
tail -n +2 "$METRIC_DETAILS_FILE" | sort -t, -k1,1 > /tmp/md_sorted.csv
echo "$header" > "$METRIC_DETAILS_FILE"
cat /tmp/md_sorted.csv >> "$METRIC_DETAILS_FILE"
rm -f /tmp/md_sorted.csv

echo "Backfill complete. $(( $(wc -l < "$METRIC_DETAILS_FILE") - 1 )) detail rows."
