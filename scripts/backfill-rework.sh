#!/usr/bin/env bash
set -euo pipefail

# Single-pass rework backfill: fetches ALL timelines once, then computes
# daily metrics from the complete data. This avoids the per-day search
# problem where GitHub's updated: qualifier misses items whose updated_at
# has moved past the target date.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

START_DATE="${1:-2026-04-06}"
END_DATE="${2:-$(date -d yesterday +%Y-%m-%d)}"

echo "Backfilling rework metrics from ${START_DATE} to ${END_DATE}..."
echo "Phase 1: Fetching all timelines (single pass)..."

ensure_rework_csv

# Load commit email-to-bot mapping from config.
CONFIG_FILE="docs/rework-config.json"
TMPDIR_WORK=$(mktemp -d)
trap 'rm -rf "$TMPDIR_WORK"' EXIT

EMAIL_MAP="$TMPDIR_WORK/email_map"
if [[ -f "$CONFIG_FILE" ]]; then
  jq -r '.commitEmailToBot // {} | to_entries[] | [.key, .value] | @tsv' "$CONFIG_FILE" > "$EMAIL_MAP" 2>/dev/null || true
fi

email_to_bot() {
  local email="$1"
  if [[ -s "$EMAIL_MAP" ]]; then
    awk -F'\t' -v e="$email" '$1==e { print $2; exit }' "$EMAIL_MAP"
  fi
}

# ALL_TOUCHES: every bot touch across all repos and all dates.
# Format: bot\trepo\tnumber\ttimestamp\titem_url
ALL_TOUCHES="$TMPDIR_WORK/all_touches"
touch "$ALL_TOUCHES"

repos=$(list_repos)

for repo in $repos; do
  full_repo="${ORG}/${repo}"

  # Find ALL issues/PRs updated since START_DATE (single broad search).
  echo "  ${full_repo}: finding items updated since ${START_DATE}..."
  items=$(gh api "/search/issues" \
    --method GET --paginate \
    -f q="repo:${full_repo} updated:>=${START_DATE}" \
    --jq '.items[] | [.number, .html_url] | @tsv' 2>/dev/null || true)

  [[ -z "$items" ]] && continue

  item_count=$(echo "$items" | wc -l)
  echo "  ${full_repo}: fetching timelines for ${item_count} items..."

  while IFS=$'\t' read -r number item_url; do
    timeline=$(gh api "/repos/${full_repo}/issues/${number}/timeline" \
      --paginate \
      -H "Accept: application/vnd.github.mockingbird-preview+json" \
      --jq '
        .[] |
        if .event == "committed" then
          {type: "commit", email: .committer.email, ts: .committer.date}
        elif .actor.type? == "Bot" then
          {type: "bot", bot: .actor.login, ts: (.created_at // null)}
        elif .user.type? == "Bot" then
          {type: "bot", bot: .user.login, ts: (.submitted_at // .created_at // null)}
        else empty
        end |
        select(.ts != null) |
        [.type, (.bot // .email), .ts] | @tsv
      ' 2>/dev/null || true)

    [[ -z "$timeline" ]] && continue

    while IFS=$'\t' read -r etype ident ts; do
      if [[ "$etype" == "commit" ]]; then
        bot=$(email_to_bot "$ident")
        [[ -z "$bot" ]] && continue
      else
        bot="$ident"
      fi

      event_date="${ts:0:10}"
      # Only record touches within the backfill range.
      # But also keep touches BEFORE START_DATE as prior-touch evidence.
      if [[ "$event_date" < "$END_DATE" || "$event_date" == "$END_DATE" ]]; then
        printf '%s\t%s\t%s\t%s\t%s\n' "$bot" "$repo" "$number" "$ts" "$item_url" >> "$ALL_TOUCHES"
      fi
    done <<< "$timeline"
  done <<< "$items"
done

# Fetch project board activity via GraphQL.
echo "  Fetching project board activity..."
project_items=$(gh api graphql -f org="$ORG" -f query='
  query($org: String!) {
    organization(login: $org) {
      projectsV2(first: 10) {
        nodes {
          items(first: 100) {
            nodes {
              updatedAt
              content {
                ... on Issue {
                  number
                  repository { name }
                  url
                  timelineItems(first: 100, itemTypes: [ADDED_TO_PROJECT_EVENT, MOVED_COLUMNS_IN_PROJECT_EVENT, REMOVED_FROM_PROJECT_EVENT]) {
                    nodes {
                      ... on AddedToProjectEvent {
                        createdAt
                        actor { login type: __typename }
                      }
                      ... on MovedColumnsInProjectEvent {
                        createdAt
                        actor { login type: __typename }
                      }
                      ... on RemovedFromProjectEvent {
                        createdAt
                        actor { login type: __typename }
                      }
                    }
                  }
                }
                ... on PullRequest {
                  number
                  repository { name }
                  url
                  timelineItems(first: 100, itemTypes: [ADDED_TO_PROJECT_EVENT, MOVED_COLUMNS_IN_PROJECT_EVENT, REMOVED_FROM_PROJECT_EVENT]) {
                    nodes {
                      ... on AddedToProjectEvent {
                        createdAt
                        actor { login type: __typename }
                      }
                      ... on MovedColumnsInProjectEvent {
                        createdAt
                        actor { login type: __typename }
                      }
                      ... on RemovedFromProjectEvent {
                        createdAt
                        actor { login type: __typename }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
' --jq '
  .data.organization.projectsV2.nodes[].items.nodes[] |
  select(.content != null) |
  .content as $c |
  .content.timelineItems.nodes[] |
  select(.actor.type == "Bot") |
  [$c.repository.name, $c.number, .actor.login, .createdAt, $c.url] |
  @tsv
' 2>/dev/null || true)

if [[ -n "$project_items" ]]; then
  while IFS=$'\t' read -r repo number bot ts item_url; do
    event_date="${ts:0:10}"
    if [[ "$event_date" < "$END_DATE" || "$event_date" == "$END_DATE" ]]; then
      printf '%s\t%s\t%s\t%s\t%s\n' "$bot" "$repo" "$number" "$ts" "$item_url" >> "$ALL_TOUCHES"
    fi
  done <<< "$project_items"
fi

if [[ ! -s "$ALL_TOUCHES" ]]; then
  echo "No bot touches found in the date range."
  exit 0
fi

# Sort all touches by bot, repo, item, timestamp for efficient processing.
sort -t$'\t' -k1,3 -k4,4 "$ALL_TOUCHES" > "$TMPDIR_WORK/all_sorted"
mv "$TMPDIR_WORK/all_sorted" "$ALL_TOUCHES"

total_touches=$(wc -l < "$ALL_TOUCHES")
echo "Phase 1 complete: ${total_touches} total bot touches found."
echo "Phase 2: Computing daily rework metrics..."

# Clear existing data for the date range so we can rewrite it.
if [[ -f "$REWORK_FILE" ]]; then
  header=$(head -1 "$REWORK_FILE")
  tail -n +2 "$REWORK_FILE" | awk -F, -v s="$START_DATE" -v e="$END_DATE" \
    '$1 < s || $1 > e' > "$TMPDIR_WORK/rework_keep"
  echo "$header" > "$REWORK_FILE"
  cat "$TMPDIR_WORK/rework_keep" >> "$REWORK_FILE"
fi

if [[ -f "$REWORK_DETAILS_FILE" ]]; then
  header=$(head -1 "$REWORK_DETAILS_FILE")
  tail -n +2 "$REWORK_DETAILS_FILE" | awk -F, -v s="$START_DATE" -v e="$END_DATE" \
    'substr($1,1,10) < s || substr($1,1,10) > e' > "$TMPDIR_WORK/details_keep"
  echo "$header" > "$REWORK_DETAILS_FILE"
  cat "$TMPDIR_WORK/details_keep" >> "$REWORK_DETAILS_FILE"
fi

# Process each date in the range.
current="$START_DATE"
while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
  # Extract today's touches (8-second dedup within same bot+item).
  TOUCHES_TODAY="$TMPDIR_WORK/today"
  awk -F'\t' -v d="$current" 'substr($4,1,10) == d' "$ALL_TOUCHES" \
    | sort -t$'\t' -k1,3 -k4,4 > "$TOUCHES_TODAY"

  if [[ ! -s "$TOUCHES_TODAY" ]]; then
    current=$(date -d "${current} + 1 day" +%Y-%m-%d)
    continue
  fi

  # 8-second dedup: keep events >8s apart for same bot+item.
  DEDUPED="$TMPDIR_WORK/deduped"
  : > "$DEDUPED"
  prev_key=""
  prev_epoch=0
  while IFS=$'\t' read -r bot repo number ts item_url; do
    key="${bot}	${repo}	${number}"
    cur_epoch=$(date -d "$ts" +%s 2>/dev/null || echo "0")
    if [[ "$key" == "$prev_key" ]]; then
      delta=$(( cur_epoch - prev_epoch ))
      if (( delta <= 8 )); then
        continue
      fi
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$bot" "$repo" "$number" "$ts" "$item_url" >> "$DEDUPED"
    prev_key="$key"
    prev_epoch=$cur_epoch
  done < "$TOUCHES_TODAY"

  if [[ ! -s "$DEDUPED" ]]; then
    current=$(date -d "${current} + 1 day" +%Y-%m-%d)
    continue
  fi

  # Determine rework: check ALL_TOUCHES for prior-day touches by same bot on same item.
  REWORK="$TMPDIR_WORK/rework"
  : > "$REWORK"
  while IFS=$'\t' read -r bot repo number ts item_url; do
    # Search the complete touch history for earlier touches by this bot on this item.
    prior_match=$(awk -F'\t' -v b="$bot" -v r="$repo" -v n="$number" -v d="$current" \
      '$1==b && $2==r && $3==n && substr($4,1,10) < d { found=1; exit } END { print found+0 }' "$ALL_TOUCHES")

    if (( prior_match > 0 )); then
      printf '%s\t%s\t%s\t%s\t%s\n' "$bot" "$repo" "$number" "$ts" "$item_url" >> "$REWORK"
    fi

    # Also check earlier same-day touches.
    if (( prior_match == 0 )); then
      earlier_same_day=$(awk -F'\t' -v b="$bot" -v r="$repo" -v n="$number" -v t="$ts" \
        '$1==b && $2==r && $3==n && $4 < t { found=1; exit } END { print found+0 }' "$DEDUPED")
      if (( earlier_same_day > 0 )); then
        printf '%s\t%s\t%s\t%s\t%s\n' "$bot" "$repo" "$number" "$ts" "$item_url" >> "$REWORK"
      fi
    fi
  done < "$DEDUPED"

  # Write rework-details.csv rows for ALL touches (not just rework).
  while IFS=$'\t' read -r bot repo number ts item_url; do
    if [[ -s "$REWORK" ]] && grep -qP "^${bot}\t${repo}\t${number}\t" "$REWORK" 2>/dev/null; then
      append_rework_detail "$ts" "$bot" "$repo" "$number" "$item_url" "true"
    else
      append_rework_detail "$ts" "$bot" "$repo" "$number" "$item_url" "false"
    fi
  done < "$DEDUPED"

  # Build rework.csv summary rows.
  bots=$(cut -f1 "$DEDUPED" | sort -u)
  total_touched=0
  total_reworked=0

  while IFS= read -r bot; do
    touched=$(awk -F'\t' -v b="$bot" '$1==b { print $2 "\t" $3 }' "$DEDUPED" | sort -u | wc -l)
    reworked=0
    if [[ -s "$REWORK" ]]; then
      reworked=$(awk -F'\t' -v b="$bot" '$1==b { print $2 "\t" $3 }' "$REWORK" | sort -u | wc -l)
    fi

    if (( touched > 0 )); then
      rate=$(awk "BEGIN { printf \"%.3f\", ${reworked} / ${touched} }")
      append_rework_row "$current" "$bot" "$touched" "$reworked" "$rate"
      total_touched=$(( total_touched + touched ))
      total_reworked=$(( total_reworked + reworked ))
    fi
  done <<< "$bots"

  if (( total_touched > 0 )); then
    agg_rate=$(awk "BEGIN { printf \"%.3f\", ${total_reworked} / ${total_touched} }")
    append_rework_row "$current" "__aggregate__" "$total_touched" "$total_reworked" "$agg_rate"
    echo "  ${current}: ${total_touched} touched, ${total_reworked} reworked (${agg_rate})"
  fi

  current=$(date -d "${current} + 1 day" +%Y-%m-%d)
done

# Sort output files by date.
header=$(head -1 "$REWORK_FILE")
tail -n +2 "$REWORK_FILE" | sort -t, -k1,1 -k2,2 > "$TMPDIR_WORK/rework_sorted"
echo "$header" > "$REWORK_FILE"
cat "$TMPDIR_WORK/rework_sorted" >> "$REWORK_FILE"

header=$(head -1 "$REWORK_DETAILS_FILE")
tail -n +2 "$REWORK_DETAILS_FILE" | sort -t, -k1,1 > "$TMPDIR_WORK/details_sorted"
echo "$header" > "$REWORK_DETAILS_FILE"
cat "$TMPDIR_WORK/details_sorted" >> "$REWORK_DETAILS_FILE"

echo "Backfill complete. $(( $(wc -l < "$REWORK_FILE") - 1 )) summary rows in ${REWORK_FILE}."
