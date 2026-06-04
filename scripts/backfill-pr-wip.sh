#!/usr/bin/env bash
set -euo pipefail

# Backfill the prs_open column in metrics.csv by querying GitHub for the
# actual open PR count on each date. For each repo+date, counts PRs that
# were created on or before that date and not yet closed.
#
# Skips rows that already have a non-zero prs_open value (resumable).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

if [[ ! -f "$DATA_FILE" ]]; then
  echo "Error: ${DATA_FILE} not found. Run collect.sh first."
  exit 1
fi

query_open_prs() {
  local full_repo="$1" date="$2"
  local response
  response=$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:pr created:<=${date} -closed:<=${date}" \
    --jq '.total_count' 2>&1) || true

  if [[ "$response" =~ ^[0-9]+$ ]]; then
    echo "$response"
    return 0
  fi

  # Rate limited or other error.
  return 1
}

repos=$(list_repos)

for repo in $repos; do
  full_repo="${ORG}/${repo}"
  echo "Backfilling prs_open for ${full_repo}..."

  mapfile -t dates < <(
    tail -n +2 "$DATA_FILE" | awk -F, -v r="$repo" '$2 == r {print $1}' | sort
  )

  if [[ ${#dates[@]} -eq 0 ]]; then
    echo "  No rows found for ${repo}, skipping."
    continue
  fi

  for date in "${dates[@]}"; do
    # Skip dates already backfilled (prs_open != 0).
    existing=$(grep "^${date},${repo}," "$DATA_FILE" | head -1 | rev | cut -d, -f1 | rev)
    if [[ "$existing" != "0" && -n "$existing" ]]; then
      continue
    fi

    # Query with retry on rate limit.
    if open=$(query_open_prs "$full_repo" "$date"); then
      :
    else
      echo "  Rate limited at ${date}, waiting 90s..." >&2
      sleep 90
      if open=$(query_open_prs "$full_repo" "$date"); then
        :
      else
        echo "  FATAL: retry failed on ${date}" >&2
        exit 1
      fi
    fi

    escaped_date=$(printf '%s' "$date" | sed 's/[.[\*^$()+?{|]/\\&/g')
    escaped_repo=$(printf '%s' "$repo" | sed 's/[.[\*^$()+?{|]/\\&/g')
    sed -i "s/^\(${escaped_date},${escaped_repo},.*,\)[^,]*$/\1${open}/" "$DATA_FILE"

    echo "  ${date}: ${open} open"

    # Respect GitHub search API rate limit (30 req/min).
    sleep 2
  done

  echo "  Done: ${full_repo}"
done

echo "Backfill complete."
