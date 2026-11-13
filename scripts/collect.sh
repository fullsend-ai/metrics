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
ensure_metric_details_csv

# Stage this run's rows and append them to the real CSVs only once every API
# call has succeeded, so a failed call leaves no partial or zero-filled data.
STAGE_DIR=$(mktemp -d)
trap 'rm -rf "$STAGE_DIR"' EXIT
OUT_DATA_FILE="$DATA_FILE"
OUT_METRIC_DETAILS_FILE="$METRIC_DETAILS_FILE"
DATA_FILE="${STAGE_DIR}/metrics.csv"
METRIC_DETAILS_FILE="${STAGE_DIR}/metric-details.csv"
: > "$DATA_FILE"
: > "$METRIC_DETAILS_FILE"

# Print the total_count of a search query. Fails if the API call fails or
# does not return a non-negative integer: an error is never counted as zero.
search_count() {
  local query="$1" count
  if ! count=$(gh api "/search/issues" --method GET -f q="$query" --jq '.total_count'); then
    echo "search_count: API call failed (query: ${query})" >&2
    return 1
  fi
  if [[ ! "$count" =~ ^[0-9]+$ ]]; then
    echo "search_count: unexpected total_count '${count}' (query: ${query})" >&2
    return 1
  fi
  echo "$count"
}

repos=$(list_repos)

for repo in $repos; do
  full_repo="${ORG}/${repo}"
  echo "  ${full_repo}..."

  prs_opened=$(search_count "repo:${full_repo} is:pr created:${TARGET_DATE}")

  if (( prs_opened > 0 )); then
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:pr created:${TARGET_DATE}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$TARGET_DATE" "$repo" "pr" "opened" "$number" "$title" "$url"
    done
  fi

  prs_merged=$(search_count "repo:${full_repo} is:pr is:merged merged:${TARGET_DATE}")

  if (( prs_merged > 0 )); then
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:pr is:merged merged:${TARGET_DATE}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$TARGET_DATE" "$repo" "pr" "merged" "$number" "$title" "$url"
    done
  fi

  prs_closed_total=$(search_count "repo:${full_repo} is:pr is:closed closed:${TARGET_DATE}")
  prs_closed=$(( prs_closed_total - prs_merged ))
  if (( prs_closed < 0 )); then prs_closed=0; fi

  if (( prs_closed > 0 )); then
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:pr is:closed is:unmerged closed:${TARGET_DATE}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$TARGET_DATE" "$repo" "pr" "closed" "$number" "$title" "$url"
    done
  fi

  issues_opened=$(search_count "repo:${full_repo} is:issue created:${TARGET_DATE}")

  if (( issues_opened > 0 )); then
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:issue created:${TARGET_DATE}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$TARGET_DATE" "$repo" "issue" "opened" "$number" "$title" "$url"
    done
  fi

  issues_closed=$(search_count "repo:${full_repo} is:issue is:closed closed:${TARGET_DATE}")

  if (( issues_closed > 0 )); then
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:issue is:closed closed:${TARGET_DATE}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$TARGET_DATE" "$repo" "issue" "closed" "$number" "$title" "$url"
    done
  fi

  # --paginate applies the filter to each page, so sum the per-page counts.
  # Draft releases have no published_at; treat them as not published today.
  release_counts=$(gh api "/repos/${full_repo}/releases" \
    --paginate \
    --jq "[.[] | select((.published_at // \"\") | startswith(\"${TARGET_DATE}\"))] | length")
  releases=$(awk '{ total += $1 } END { print total + 0 }' <<< "$release_counts")

  prs_open=$(search_count "repo:${full_repo} is:pr is:open")

  lead_time_median="0"
  if (( prs_merged > 0 )); then
    lead_times=$(gh api "/search/issues" \
      --method GET \
      -f q="repo:${full_repo} is:pr is:merged merged:${TARGET_DATE}" \
      --jq '.items[].pull_request.url' | while read -r pr_url; do
        gh api "$pr_url" --jq '
          (((.merged_at | fromdateiso8601) - (.created_at | fromdateiso8601)) / 3600)
          | . * 10 | round / 10
        ' || exit 1
      done)

    if [[ -n "$lead_times" ]]; then
      lead_time_median=$(echo "$lead_times" | median)
    fi
  fi

  append_row "$TARGET_DATE" "$repo" "$prs_opened" "$prs_merged" "$prs_closed" \
    "$issues_opened" "$issues_closed" "$releases" "$lead_time_median" "$prs_open"
done

cat "$DATA_FILE" >> "$OUT_DATA_FILE"
cat "$METRIC_DETAILS_FILE" >> "$OUT_METRIC_DETAILS_FILE"

echo "Done. Metrics for ${TARGET_DATE} written to ${OUT_DATA_FILE}."
