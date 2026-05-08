#!/usr/bin/env bash
set -euo pipefail

ORG="fullsend-ai"
DATA_FILE="docs/metrics.csv"
CSV_HEADER="date,repo,prs_opened,prs_merged,prs_closed,issues_opened,issues_closed,releases,pr_lead_time_median_hours"

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
  echo "$1,$2,$3,$4,$5,$6,$7,$8,$9" >> "$DATA_FILE"
}

REWORK_FILE="docs/rework.csv"
REWORK_DETAILS_FILE="docs/rework-details.csv"
REWORK_HEADER="date,bot,items_touched,items_reworked,rework_rate"
REWORK_DETAILS_HEADER="datetime,bot,repo,item,url"

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
  echo "$1,$2,$3,$4,$5" >> "$REWORK_DETAILS_FILE"
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
