#!/usr/bin/env bash
set -euo pipefail

ORG="fullsend-ai"
DATA_FILE="docs/metrics.csv"
CSV_HEADER="date,repo,prs_opened,prs_merged,prs_closed,issues_opened,issues_closed,releases,pr_lead_time_median_hours,prs_open"

ensure_csv() {
  mkdir -p docs
  if [[ ! -f "$DATA_FILE" ]]; then
    echo "$CSV_HEADER" > "$DATA_FILE"
  fi
}

# List all non-archived, non-fork repos in the org.
# Returns one repo name per line (e.g. "fullsend").
list_repos() {
  gh api "/orgs/${ORG}/repos" \
    --paginate \
    --jq '.[] | select(.archived == false and .fork == false) | .name'
}

# Append a row to the CSV. Arguments: all column values in order.
append_row() {
  echo "$1,$2,$3,$4,$5,$6,$7,$8,$9,${10}" >> "$DATA_FILE"
}

REWORK_FILE="docs/rework.csv"
REWORK_DETAILS_FILE="docs/rework-details.csv"
REWORK_HEADER="date,bot,items_touched,items_reworked,rework_rate"
REWORK_DETAILS_HEADER="datetime,bot,repo,item,url,is_rework"

ensure_rework_csv() {
  mkdir -p docs
  if [[ ! -f "$REWORK_FILE" ]]; then
    echo "$REWORK_HEADER" > "$REWORK_FILE"
  fi
  if [[ ! -f "$REWORK_DETAILS_FILE" ]]; then
    echo "$REWORK_DETAILS_HEADER" > "$REWORK_DETAILS_FILE"
  fi
}

append_rework_row() {
  echo "$1,$2,$3,$4,$5" >> "$REWORK_FILE"
}

append_rework_detail() {
  echo "$1,$2,$3,$4,$5,$6" >> "$REWORK_DETAILS_FILE"
}

FAILURE_FILE="docs/failures.csv"
FAILURE_HEADER="date,workflow,runs,failures,failure_rate"

ensure_failure_csv() {
  mkdir -p docs
  if [[ ! -f "$FAILURE_FILE" ]]; then
    echo "$FAILURE_HEADER" > "$FAILURE_FILE"
  fi
}

append_failure_row() {
  echo "$1,$2,$3,$4,$5" >> "$FAILURE_FILE"
}

FAILURE_DETAILS_FILE="docs/failure-details.csv"
FAILURE_DETAILS_HEADER="date,workflow,repo,run_id,status,url"

ensure_failure_details_csv() {
  mkdir -p docs
  if [[ ! -f "$FAILURE_DETAILS_FILE" ]]; then
    echo "$FAILURE_DETAILS_HEADER" > "$FAILURE_DETAILS_FILE"
  fi
}

append_failure_detail() {
  echo "$1,$2,$3,$4,$5,$6" >> "$FAILURE_DETAILS_FILE"
}

METRIC_DETAILS_FILE="docs/metric-details.csv"
METRIC_DETAILS_HEADER="date,repo,type,event,number,title,url"

ensure_metric_details_csv() {
  mkdir -p docs
  if [[ ! -f "$METRIC_DETAILS_FILE" ]]; then
    echo "$METRIC_DETAILS_HEADER" > "$METRIC_DETAILS_FILE"
  fi
}

# Handles CSV quoting for the title field (may contain commas).
append_metric_detail() {
  local date="$1" repo="$2" type="$3" event="$4" number="$5" title="$6" url="$7"
  # Escape double quotes in title by doubling them, then wrap in quotes.
  title="${title//\"/\"\"}"
  echo "${date},${repo},${type},${event},${number},\"${title}\",${url}" >> "$METRIC_DETAILS_FILE"
}

COMMUNITY_FILE="docs/community.csv"
COMMUNITY_DETAILS_FILE="docs/community-details.csv"
COMMUNITY_CONFIG_FILE="docs/community-config.json"
COMMUNITY_HEADER="date,repo,issues_opened_external,issues_closed_via_pr_external,issues_closed_other_external,prs_opened_core,pr_reviews_external"
COMMUNITY_DETAILS_HEADER="date,repo,type,event,number,actor,title,url"

ensure_community_csv() {
  mkdir -p docs
  if [[ ! -f "$COMMUNITY_FILE" ]]; then
    echo "$COMMUNITY_HEADER" > "$COMMUNITY_FILE"
  fi
  if [[ ! -f "$COMMUNITY_DETAILS_FILE" ]]; then
    echo "$COMMUNITY_DETAILS_HEADER" > "$COMMUNITY_DETAILS_FILE"
  fi
}

append_community_row() {
  echo "$1,$2,$3,$4,$5,$6,$7" >> "$COMMUNITY_FILE"
}

# Handles CSV quoting for the title field (may contain commas).
append_community_detail() {
  local date="$1" repo="$2" type="$3" event="$4" number="$5" actor="$6" title="$7" url="$8"
  title="${title//\"/\"\"}"
  echo "${date},${repo},${type},${event},${number},${actor},\"${title}\",${url}" >> "$COMMUNITY_DETAILS_FILE"
}

# Print the configured core-team usernames, one per line.
core_team_list() {
  if [[ -f "$COMMUNITY_CONFIG_FILE" ]]; then
    jq -r '.coreTeam[]?' "$COMMUNITY_CONFIG_FILE" 2>/dev/null
  fi
}

# Print manually-flagged bot usernames (accounts whose GitHub account type
# is "User" but which are actually automation), one per line.
ignored_bots_list() {
  if [[ -f "$COMMUNITY_CONFIG_FILE" ]]; then
    jq -r '.ignoreBots[]?' "$COMMUNITY_CONFIG_FILE" 2>/dev/null
  fi
}

# True if $1 is a configured core-team username. Requires CORE_TEAM to be
# set by the caller (see core_team_list).
is_core() {
  grep -qxF "$1" <<< "$CORE_TEAM"
}

# True if $1 is a manually-flagged bot account (see community-config.json).
# Requires IGNORED_BOTS to be set by the caller (see ignored_bots_list).
is_ignored_bot() {
  [[ -n "$IGNORED_BOTS" ]] && grep -qxF "$1" <<< "$IGNORED_BOTS"
}

# Print "login\ttimestamp" for every review, inline review comment, and
# conversation comment on a PR, excluding bot authors.
pr_review_events() {
  local repo="$1" number="$2"
  gh api graphql \
    -f owner="${ORG}" -f name="${repo}" -F number="${number}" \
    -f query='
      query($owner: String!, $name: String!, $number: Int!) {
        repository(owner: $owner, name: $name) {
          pullRequest(number: $number) {
            reviews(first: 100) {
              nodes { author { login __typename } submittedAt }
            }
            reviewThreads(first: 100) {
              nodes {
                comments(first: 100) {
                  nodes { author { login __typename } createdAt }
                }
              }
            }
            comments(first: 100) {
              nodes { author { login __typename } createdAt }
            }
          }
        }
      }
    ' \
    --jq '
      [ .data.repository.pullRequest.reviews.nodes[]
        | select(.author != null and .author.__typename != "Bot")
        | {login: .author.login, ts: .submittedAt} ]
      + [ .data.repository.pullRequest.reviewThreads.nodes[].comments.nodes[]
          | select(.author != null and .author.__typename != "Bot")
          | {login: .author.login, ts: .createdAt} ]
      + [ .data.repository.pullRequest.comments.nodes[]
          | select(.author != null and .author.__typename != "Bot")
          | {login: .author.login, ts: .createdAt} ]
      | .[] | select(.ts != null) | [.login, .ts] | @tsv
    ' 2>/dev/null || true
}

# Run a GitHub issue/PR search query, printing matching rows via the given
# jq filter. Retries with backoff on failure (e.g. the search API's
# secondary rate limit of 30 req/min, which is separate from and much
# stricter than the 5000/hr core limit) instead of silently treating an
# error response as if it were real search results.
search_issues() {
  local query="$1" jq_filter="$2"
  local attempt output
  for attempt in 1 2 3 4 5; do
    if output=$(gh api "/search/issues" --method GET --paginate -f q="$query" --jq "$jq_filter" 2>&1); then
      printf '%s\n' "$output"
      return 0
    fi
    echo "search_issues: query failed (attempt ${attempt}/5): ${output}" >&2
    sleep $(( attempt * 20 ))
  done
  echo "search_issues: giving up after 5 attempts (query: ${query})" >&2
  return 1
}

# Compute median from a newline-separated list of numbers on stdin.
# Outputs "0" if input is empty.
median() {
  local nums
  nums=$(sort -n)
  if [[ -z "$nums" ]]; then
    echo "0"
    return
  fi
  local count mid
  count=$(echo "$nums" | wc -l)
  mid=$(( (count + 1) / 2 ))
  echo "$nums" | sed -n "${mid}p"
}

PR_TYPE_FILE="docs/pr-type.csv"
PR_TYPE_DETAILS_FILE="docs/pr-type-details.csv"
PR_TYPE_HEADER="date,repo,feat,fix,docs,ci,chore,test,perf,other,fix_core,fix_external,fix_bot,fix_unlinked"
PR_TYPE_DETAILS_HEADER="date,repo,number,title,pr_type,fix_source,issue_number,issue_author,url"

ensure_pr_type_csv() {
  mkdir -p docs
  if [[ ! -f "$PR_TYPE_FILE" ]]; then
    echo "$PR_TYPE_HEADER" > "$PR_TYPE_FILE"
  fi
  if [[ ! -f "$PR_TYPE_DETAILS_FILE" ]]; then
    echo "$PR_TYPE_DETAILS_HEADER" > "$PR_TYPE_DETAILS_FILE"
  fi
}

append_pr_type_row() {
  echo "$1,$2,$3,$4,$5,$6,$7,$8,$9,${10},${11},${12},${13},${14}" >> "$PR_TYPE_FILE"
}

append_pr_type_detail() {
  local date="$1" repo="$2" number="$3" title="$4" pr_type="$5" fix_source="$6" issue_number="$7" issue_author="$8" url="$9"
  title="${title//\"/\"\"}"
  echo "${date},${repo},${number},\"${title}\",${pr_type},${fix_source},${issue_number},${issue_author},${url}" >> "$PR_TYPE_DETAILS_FILE"
}
