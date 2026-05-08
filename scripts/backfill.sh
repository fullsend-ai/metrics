#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

START_DATE="${1:-2026-04-06}"
END_DATE="${2:-$(date -d yesterday +%Y-%m-%d)}"

echo "Backfilling metrics from ${START_DATE} to ${END_DATE}..."

ensure_csv

repos=$(list_repos)

for repo in $repos; do
  full_repo="${ORG}/${repo}"
  echo "Fetching bulk data for ${full_repo}..."

  # Fetch all merged PRs in the date range (for merge count + lead time).
  merged_prs=$(gh api "/search/issues" \
    --method GET --paginate \
    -f q="repo:${full_repo} is:pr is:merged merged:${START_DATE}..${END_DATE}" \
    --jq '.items[] | {date: (.pull_request.merged_at[:10]), created: .created_at, merged: .pull_request.merged_at}' 2>/dev/null || true)

  # Fetch all opened PRs in the date range.
  opened_prs=$(gh api "/search/issues" \
    --method GET --paginate \
    -f q="repo:${full_repo} is:pr created:${START_DATE}..${END_DATE}" \
    --jq '.items[] | .created_at[:10]' 2>/dev/null || true)

  # Fetch all closed PRs in the date range (includes merged).
  closed_prs=$(gh api "/search/issues" \
    --method GET --paginate \
    -f q="repo:${full_repo} is:pr is:closed closed:${START_DATE}..${END_DATE}" \
    --jq '.items[] | .closed_at[:10]' 2>/dev/null || true)

  # Fetch all opened issues in the date range.
  opened_issues=$(gh api "/search/issues" \
    --method GET --paginate \
    -f q="repo:${full_repo} is:issue created:${START_DATE}..${END_DATE}" \
    --jq '.items[] | .created_at[:10]' 2>/dev/null || true)

  # Fetch all closed issues in the date range.
  closed_issues=$(gh api "/search/issues" \
    --method GET --paginate \
    -f q="repo:${full_repo} is:issue is:closed closed:${START_DATE}..${END_DATE}" \
    --jq '.items[] | .closed_at[:10]' 2>/dev/null || true)

  # Fetch all releases in the date range.
  all_releases=$(gh api "/repos/${full_repo}/releases" \
    --paginate \
    --jq ".[] | select(.published_at >= \"${START_DATE}\" and .published_at <= \"${END_DATE}T23:59:59Z\") | .published_at[:10]" 2>/dev/null || true)

  # Iterate each day and bucket the results.
  current="$START_DATE"
  while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
    # Skip dates already in the CSV.
    if grep -q "^${current},${repo}," "$DATA_FILE" 2>/dev/null; then
      current=$(date -d "${current} + 1 day" +%Y-%m-%d)
      continue
    fi

    prs_o=$(echo "$opened_prs" | grep -c "^${current}$" || true)
    prs_m=$(echo "$merged_prs" | grep -c "\"date\":\"${current}\"" || true)
    prs_c_total=$(echo "$closed_prs" | grep -c "^${current}$" || true)
    prs_c=$(( prs_c_total - prs_m ))
    if (( prs_c < 0 )); then prs_c=0; fi

    iss_o=$(echo "$opened_issues" | grep -c "^${current}$" || true)
    iss_c=$(echo "$closed_issues" | grep -c "^${current}$" || true)

    rels=$(echo "$all_releases" | grep -c "^${current}$" || true)

    # Compute lead time for PRs merged on this day.
    lt_median="0"
    if (( prs_m > 0 )); then
      lead_times=$(echo "$merged_prs" | grep "\"date\":\"${current}\"" | while IFS= read -r line; do
        created=$(echo "$line" | sed 's/.*"created":"\([^"]*\)".*/\1/')
        merged=$(echo "$line" | sed 's/.*"merged":"\([^"]*\)".*/\1/')
        created_epoch=$(date -d "$created" +%s 2>/dev/null || echo "0")
        merged_epoch=$(date -d "$merged" +%s 2>/dev/null || echo "0")
        if (( merged_epoch > 0 && created_epoch > 0 )); then
          hours=$(( (merged_epoch - created_epoch) * 10 / 3600 ))
          echo "${hours%.*}"
        fi
      done)

      if [[ -n "$lead_times" ]]; then
        # Convert back from tenths-of-hours to decimal.
        raw_median=$(echo "$lead_times" | median)
        if [[ "$raw_median" != "0" ]]; then
          whole=$(( raw_median / 10 ))
          frac=$(( raw_median % 10 ))
          lt_median="${whole}.${frac}"
        fi
      fi
    fi

    append_row "$current" "$repo" "$prs_o" "$prs_m" "$prs_c" "$iss_o" "$iss_c" "$rels" "$lt_median"
    current=$(date -d "${current} + 1 day" +%Y-%m-%d)
  done

  echo "  Done: ${full_repo}"
done

# Sort the CSV by date,repo (keep header first).
header=$(head -1 "$DATA_FILE")
tail -n +2 "$DATA_FILE" | sort -t, -k1,1 -k2,2 > /tmp/metrics_sorted.csv
echo "$header" > "$DATA_FILE"
cat /tmp/metrics_sorted.csv >> "$DATA_FILE"
rm -f /tmp/metrics_sorted.csv

echo "Backfill complete. $(( $(wc -l < "$DATA_FILE") - 1 )) rows in ${DATA_FILE}."
