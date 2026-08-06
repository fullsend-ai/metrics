#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

START_DATE="$1"
END_DATE="$2"
COUNTS_FILE="$3"

ensure_community_csv

CORE_TEAM=$(core_team_list)
IGNORED_BOTS=$(ignored_bots_list)

repos=$(list_repos)
declare -A review_counts

for repo in $repos; do
  full_repo="${ORG}/${repo}"
  echo "  ${full_repo}: scanning all PRs for review activity..."

  while IFS=$'\t' read -r number pr_author title url; do
    [[ -z "$number" ]] && continue

    events=$(pr_review_events "$repo" "$number")
    [[ -z "$events" ]] && continue

    while IFS=$'\t' read -r reviewer ts; do
      [[ -z "$reviewer" ]] && continue
      event_date="${ts:0:10}"
      [[ "$event_date" < "$START_DATE" || "$event_date" > "$END_DATE" ]] && continue
      [[ "$reviewer" == "$pr_author" ]] && continue
      is_ignored_bot "$reviewer" && continue
      is_core "$reviewer" && continue
      append_community_detail "$event_date" "$repo" "pr" "reviewed_external" "$number" "$reviewer" "$title" "$url"
      key="${event_date},${repo}"
      review_counts["$key"]=$(( ${review_counts["$key"]:-0} + 1 ))
    done <<< "$events"
  done < <(gh api "/repos/${full_repo}/pulls" --method GET --paginate \
    -f state=all -f per_page=100 \
    --jq '.[] | [.number, .user.login, .title, .html_url] | @tsv' 2>/dev/null || true)
done

: > "$COUNTS_FILE"
for key in "${!review_counts[@]}"; do
  echo "${key},${review_counts[$key]}" >> "$COUNTS_FILE"
done

echo "Review scan complete: ${#review_counts[@]} date/repo pairs with external review activity."
