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

# List all public, non-archived, non-fork repos in the org.
# Returns one repo name per line (e.g. "fullsend").
# The visibility filter keeps private and internal repos out of the public
# CSVs when a token that can see them (e.g. a maintainer's login) runs this.
list_repos() {
  gh api "/orgs/${ORG}/repos" \
    --paginate \
    --jq '.[] | select(.visibility == "public" and .archived == false and .fork == false) | .name'
}

# Append a row to the CSV. Arguments: all column values in order.
append_row() {
  echo "$1,$2,$3,$4,$5,$6,$7,$8,$9,${10}" >> "$DATA_FILE"
}

# Merge the rows a backfill appended to a CSV into the rows that were already
# there, without moving or changing any existing row.
#
# Usage: merge_appended_rows FILE EXISTING_LINES KEY...
#   FILE            CSV file whose first line is the header.
#   EXISTING_LINES  Line count of FILE (header included) before the backfill
#                   started appending. Lines after it are the new rows.
#   KEY...          sort(1) key options on comma-separated fields,
#                   e.g. -k1,1 -k2,2 for date then repo.
#
# Order: the new rows are sorted among themselves by KEY, then each one is
# inserted before the first existing row whose key sorts after it. Existing
# rows keep their order, even if the file is not fully sorted. Rows with equal
# keys keep their input order, existing rows first. Keys compare byte by byte
# (LC_ALL=C), so the result is the same in every locale.
merge_appended_rows() {
  local file="$1" existing_lines="$2"
  shift 2
  local tmp
  tmp=$(mktemp -d)
  head -n "$existing_lines" "$file" | tail -n +2 > "$tmp/existing"
  tail -n +"$(( existing_lines + 1 ))" "$file" | LC_ALL=C sort -s -t, "$@" > "$tmp/new"
  {
    head -n 1 "$file"
    LC_ALL=C sort -m -s -t, "$@" "$tmp/existing" "$tmp/new"
  } > "$tmp/merged"
  cat "$tmp/merged" > "$file"
  rm -rf "$tmp"
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

# Agents run inline in each repo's fullsend shim workflow, which calls the
# reusable dispatch workflow. Each agent role is a job named
# "<shim job> / <Role>" (e.g. "dispatch / Code"); harness agents run as
# "<shim job> / Harness run (<agent>)". Jobs for roles that did not run are
# reported with conclusion "skipped".
AGENT_DISPATCH_WORKFLOW=".github/workflows/fullsend.yaml"
AGENT_ROLES=(
  "Code"
  "Fix"
  "Review"
  "Triage"
  "Prioritize"
  "Retro"
)
# The runs API returns at most this many results per query.
RUNS_RESULT_CAP=1000
GH_RETRY_SLEEP="${GH_RETRY_SLEEP:-10}"

# Run `gh api` with retries. Prints the output on success; after the last
# failed attempt returns 1, so an API error is never read as an empty result.
gh_api_retry() {
  local attempt output err
  err=$(mktemp)
  for attempt in 1 2 3; do
    if output=$(gh api "$@" 2>"$err"); then
      rm -f "$err"
      [[ -n "$output" ]] && printf '%s\n' "$output"
      return 0
    fi
    echo "gh api $1: failed (attempt ${attempt}/3): $(cat "$err")" >&2
    (( attempt < 3 )) && sleep $(( attempt * GH_RETRY_SLEEP ))
  done
  rm -f "$err"
  return 1
}

# Print the agent role for a dispatch job name, or return 1 if the job is
# not an agent job (Route, Harness dispatch, stop-fix, ...).
agent_role_for_job() {
  local short="${1##* / }" role
  for role in "${AGENT_ROLES[@]}"; do
    if [[ "$short" == "$role" ]]; then
      echo "$role"
      return 0
    fi
  done
  if [[ "$short" =~ ^Harness\ run\ \(([a-z][a-z0-9_-]*)\)$ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

# Print one TSV line per agent job that finished in a repo's fullsend
# dispatch workflow for runs created on DATE (UTC):
#   role, repo, run_id, status (success|failure), run_url
# Returns 1 if any API call fails, if no repo has the dispatch workflow, or
# if a day's runs cannot all be listed, so the caller never records a
# partial or failed lookup as data.
collect_agent_runs() {
  local date="$1"
  local repos repo wf_id total fetched window runs jobs role status hour
  local run_id conclusion run_url job_name job_conclusion
  local found_workflow=0
  local -a windows

  repos=$(list_repos) || { echo "collect_agent_runs: could not list ${ORG} repos" >&2; return 1; }

  while IFS= read -r repo; do
    [[ -z "$repo" ]] && continue
    wf_id=$(gh_api_retry "/repos/${ORG}/${repo}/actions/workflows" --paginate \
      --jq ".workflows[] | select(.path == \"${AGENT_DISPATCH_WORKFLOW}\") | .id") || return 1
    [[ -z "$wf_id" ]] && continue
    found_workflow=1

    total=$(gh_api_retry "/repos/${ORG}/${repo}/actions/workflows/${wf_id}/runs?per_page=1&status=completed&created=${date}" \
      --jq '.total_count') || return 1
    (( total == 0 )) && continue

    # Split busy days into hourly windows to stay under the result cap.
    windows=("$date")
    if (( total > RUNS_RESULT_CAP )); then
      windows=()
      for hour in $(seq -w 0 23); do
        windows+=("${date}T${hour}:00:00Z..${date}T${hour}:59:59Z")
      done
    fi

    fetched=0
    for window in "${windows[@]}"; do
      runs=$(gh_api_retry "/repos/${ORG}/${repo}/actions/workflows/${wf_id}/runs?per_page=100&status=completed&created=${window}" \
        --paginate --jq '.workflow_runs[] | [(.id | tostring), .conclusion, .html_url] | @tsv') || return 1

      while IFS=$'\t' read -r run_id conclusion run_url; do
        [[ -z "$run_id" ]] && continue
        fetched=$(( fetched + 1 ))
        # Skipped runs never started a job; action_required runs await approval.
        case "$conclusion" in skipped|action_required) continue ;; esac

        jobs=$(gh_api_retry "/repos/${ORG}/${repo}/actions/runs/${run_id}/jobs?per_page=100" \
          --jq '.jobs[] | select(.status == "completed" and .conclusion != "skipped") | [.name, .conclusion] | @tsv') || return 1

        while IFS=$'\t' read -r job_name job_conclusion; do
          [[ -z "$job_name" ]] && continue
          role=$(agent_role_for_job "$job_name") || continue
          # Cancelled jobs (usually superseded by a newer run of the same
          # role) neither succeeded nor failed, so they are not counted.
          case "$job_conclusion" in
            success) status="success" ;;
            failure|timed_out|startup_failure) status="failure" ;;
            *) continue ;;
          esac
          printf '%s\t%s\t%s\t%s\t%s\n' "$role" "$repo" "$run_id" "$status" "$run_url"
        done <<< "$jobs"
      done <<< "$runs"
    done

    if (( fetched < total )); then
      echo "collect_agent_runs: listed ${fetched} of ${total} ${repo} runs for ${date}; an hourly window may exceed the ${RUNS_RESULT_CAP}-result cap" >&2
      return 1
    fi
  done <<< "$repos"

  if (( found_workflow == 0 )); then
    echo "collect_agent_runs: no ${ORG} repo has ${AGENT_DISPATCH_WORKFLOW}; agent runs cannot be found" >&2
    return 1
  fi
}

METRIC_DETAILS_FILE="docs/metric-details.csv"
METRIC_DETAILS_HEADER="date,repo,type,event,number,title,url"

QUALITY_FILE="docs/quality.csv"
QUALITY_HEADER="date,repo,merged_prs,fix_prs,defect_prs,revert_prs,revert_commits,defect_rate,revert_rate"

ensure_quality_csv() {
  mkdir -p docs
  if [[ ! -f "$QUALITY_FILE" ]]; then
    echo "$QUALITY_HEADER" > "$QUALITY_FILE"
  fi
}

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
