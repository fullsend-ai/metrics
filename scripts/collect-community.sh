#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

TARGET_DATE="${1:-$(date -d yesterday +%Y-%m-%d)}"
echo "Collecting community metrics for ${TARGET_DATE}..."

ensure_community_csv

# Skip if this date already has data.
if grep -q "^${TARGET_DATE}," "$COMMUNITY_FILE" 2>/dev/null; then
  echo "Data for ${TARGET_DATE} already exists in ${COMMUNITY_FILE}. Skipping."
  exit 0
fi

CORE_TEAM=$(core_team_list)
IGNORED_BOTS=$(ignored_bots_list)

# Classify how a closed issue was resolved: "pr" if the closing event's
# closer was a merged pull request, "other" otherwise (manual close,
# rejected, duplicate, etc).
classify_close() {
  local repo="$1" number="$2"
  gh api graphql \
    -f owner="${ORG}" -f name="${repo}" -F number="${number}" \
    -f query='
      query($owner: String!, $name: String!, $number: Int!) {
        repository(owner: $owner, name: $name) {
          issue(number: $number) {
            timelineItems(itemTypes: [CLOSED_EVENT], last: 1) {
              nodes {
                ... on ClosedEvent {
                  closer {
                    __typename
                    ... on PullRequest { merged }
                  }
                }
              }
            }
          }
        }
      }
    ' \
    --jq '
      (.data.repository.issue.timelineItems.nodes[0].closer // null) as $c
      | if ($c != null and $c.__typename == "PullRequest" and ($c.merged // false))
        then "pr" else "other" end
    ' 2>/dev/null || echo "other"
}

repos=$(list_repos)

for repo in $repos; do
  full_repo="${ORG}/${repo}"
  echo "  ${full_repo}..."

  issues_opened_external=0
  while IFS=$'\t' read -r number login user_type title url; do
    [[ -z "$number" ]] && continue
    [[ "$number" =~ ^[0-9]+$ ]] || continue
    [[ "$user_type" == "Bot" ]] && continue
    is_ignored_bot "$login" && continue
    is_core "$login" && continue
    issues_opened_external=$(( issues_opened_external + 1 ))
    append_community_detail "$TARGET_DATE" "$repo" "issue" "opened_external" "$number" "$login" "$title" "$url"
  done < <(search_issues "repo:${full_repo} is:issue created:${TARGET_DATE}" \
    '.items[] | [.number, .user.login, .user.type, .title, .html_url] | @tsv')
  sleep 2

  issues_closed_via_pr_external=0
  issues_closed_other_external=0
  while IFS=$'\t' read -r number login user_type title url; do
    [[ -z "$number" ]] && continue
    [[ "$number" =~ ^[0-9]+$ ]] || continue
    [[ "$user_type" == "Bot" ]] && continue
    is_ignored_bot "$login" && continue
    is_core "$login" && continue

    if [[ "$(classify_close "$repo" "$number")" == "pr" ]]; then
      issues_closed_via_pr_external=$(( issues_closed_via_pr_external + 1 ))
      append_community_detail "$TARGET_DATE" "$repo" "issue" "closed_via_pr_external" "$number" "$login" "$title" "$url"
    else
      issues_closed_other_external=$(( issues_closed_other_external + 1 ))
      append_community_detail "$TARGET_DATE" "$repo" "issue" "closed_other_external" "$number" "$login" "$title" "$url"
    fi
  done < <(search_issues "repo:${full_repo} is:issue is:closed closed:${TARGET_DATE}" \
    '.items[] | [.number, .user.login, .user.type, .title, .html_url] | @tsv')
  sleep 2

  prs_opened_core=0
  while IFS=$'\t' read -r number login title url; do
    [[ -z "$number" ]] && continue
    [[ "$number" =~ ^[0-9]+$ ]] || continue
    is_core "$login" || continue
    prs_opened_core=$(( prs_opened_core + 1 ))
    append_community_detail "$TARGET_DATE" "$repo" "pr" "opened_core" "$number" "$login" "$title" "$url"
  done < <(search_issues "repo:${full_repo} is:pr created:${TARGET_DATE}" \
    '.items[] | [.number, .user.login, .title, .html_url] | @tsv')
  sleep 2

  # PR review activity: reviews can land on a PR long after it was opened.
  # During backfill this is precomputed once across the whole date range
  # (see backfill-collect-reviews.sh) rather than re-scanned per day; use
  # that lookup if available, otherwise scan inline as usual.
  if [[ -n "${COMMUNITY_REVIEW_COUNTS_FILE:-}" ]]; then
    pr_reviews_external=$(awk -F, -v d="$TARGET_DATE" -v r="$repo" \
      '$1 == d && $2 == r { print $3 }' "$COMMUNITY_REVIEW_COUNTS_FILE")
    pr_reviews_external="${pr_reviews_external:-0}"
  else
    pr_reviews_external=0
    while IFS=$'\t' read -r number pr_author title url; do
      [[ -z "$number" ]] && continue
      [[ "$number" =~ ^[0-9]+$ ]] || continue

      events=$(pr_review_events "$repo" "$number")
      [[ -z "$events" ]] && continue

      while IFS=$'\t' read -r reviewer ts; do
        [[ -z "$reviewer" ]] && continue
        [[ "${ts:0:10}" != "$TARGET_DATE" ]] && continue
        [[ "$reviewer" == "$pr_author" ]] && continue
        is_ignored_bot "$reviewer" && continue
        is_core "$reviewer" && continue
        pr_reviews_external=$(( pr_reviews_external + 1 ))
        append_community_detail "$TARGET_DATE" "$repo" "pr" "reviewed_external" "$number" "$reviewer" "$title" "$url"
      done <<< "$events"
    done < <(search_issues "repo:${full_repo} is:pr updated:>=${TARGET_DATE}" \
      '.items[] | [.number, .user.login, .title, .html_url] | @tsv')
  fi

  append_community_row "$TARGET_DATE" "$repo" "$issues_opened_external" \
    "$issues_closed_via_pr_external" "$issues_closed_other_external" \
    "$prs_opened_core" "$pr_reviews_external"
done

echo "Done. Community metrics for ${TARGET_DATE} written to ${COMMUNITY_FILE}."
