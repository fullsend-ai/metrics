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
