#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

TARGET_DATE="${1:-$(date -d yesterday +%Y-%m-%d)}"
echo "Collecting rework metrics for ${TARGET_DATE}..."

ensure_rework_csv

# Skip if this date already has data.
if grep -q "^${TARGET_DATE}," "$REWORK_FILE" 2>/dev/null; then
  echo "Data for ${TARGET_DATE} already exists in ${REWORK_FILE}. Skipping."
  exit 0
fi

# Temp files for intermediate data.
# Each line: bot\trepo\tnumber\ttimestamp\titem_url
TMPDIR_WORK=$(mktemp -d)
trap 'rm -rf "$TMPDIR_WORK"' EXIT
TOUCHES_TODAY="$TMPDIR_WORK/touches_today"
TOUCHES_PRIOR="$TMPDIR_WORK/touches_prior"
TOUCHES_DEDUPED="$TMPDIR_WORK/touches_deduped"
REWORK_ITEMS="$TMPDIR_WORK/rework_items"

repos=$(list_repos)

for repo in $repos; do
  full_repo="${ORG}/${repo}"
  echo "  ${full_repo}: searching for updated items..."

  # Find issues/PRs updated on the target date.
  items=$(gh api "/search/issues" \
    --method GET --paginate \
    -f q="repo:${full_repo} updated:${TARGET_DATE}" \
    --jq '.items[] | [.number, .html_url] | @tsv' 2>/dev/null || true)

  [[ -z "$items" ]] && continue

  item_count=$(echo "$items" | wc -l)
  echo "  ${full_repo}: fetching timelines for ${item_count} items..."

  while IFS=$'\t' read -r number item_url; do
    # Fetch full timeline for this issue/PR.
    # The timeline API returns different event shapes; normalize them.
    timeline=$(gh api "/repos/${full_repo}/issues/${number}/timeline" \
      --paginate \
      -H "Accept: application/vnd.github.mockingbird-preview+json" \
      --jq '
        .[] |
        (
          if .actor.type? == "Bot" then {bot: .actor.login, ts: (.created_at // null)}
          elif .user.type? == "Bot" then {bot: .user.login, ts: (.submitted_at // .created_at // null)}
          else null
          end
        ) //
        null |
        select(. != null and .bot != null and .ts != null) |
        [.bot, .ts] | @tsv
      ' 2>/dev/null || true)

    [[ -z "$timeline" ]] && continue

    while IFS=$'\t' read -r bot ts; do
      event_date="${ts:0:10}"
      if [[ "$event_date" == "$TARGET_DATE" ]]; then
        printf '%s\t%s\t%s\t%s\t%s\n' "$bot" "$repo" "$number" "$ts" "$item_url" >> "$TOUCHES_TODAY"
      elif [[ "$event_date" < "$TARGET_DATE" ]]; then
        printf '%s\t%s\t%s\t%s\n' "$bot" "$repo" "$number" "$ts" >> "$TOUCHES_PRIOR"
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
    if [[ "$event_date" == "$TARGET_DATE" ]]; then
      printf '%s\t%s\t%s\t%s\t%s\n' "$bot" "$repo" "$number" "$ts" "$item_url" >> "$TOUCHES_TODAY"
    elif [[ "$event_date" < "$TARGET_DATE" ]]; then
      printf '%s\t%s\t%s\t%s\n' "$bot" "$repo" "$number" "$ts" >> "$TOUCHES_PRIOR"
    fi
  done <<< "$project_items"
fi

# If no bot touches found today, nothing to report.
if [[ ! -s "$TOUCHES_TODAY" ]]; then
  echo "No bot touches found for ${TARGET_DATE}."
  exit 0
fi

# Deduplicate TOUCHES_TODAY: for each bot+repo+item, keep the earliest event.
# Apply 8-second dedup: if the earliest today-touch is within 8s of the latest
# prior-touch for the same bot+repo+item, remove it from today (same activity).
sort -t$'\t' -k1,3 -k4,4 "$TOUCHES_TODAY" | awk -F'\t' '
  !seen[$1 "\t" $2 "\t" $3]++ { print }
' > "$TOUCHES_DEDUPED"

# For each bot+repo+item in today's touches, check for prior-day touches.
# Apply 8-second cross-day dedup.
while IFS=$'\t' read -r bot repo number ts item_url; do
  # Find the latest prior touch by this bot on this item.
  latest_prior=$(awk -F'\t' -v b="$bot" -v r="$repo" -v n="$number" '$1==b && $2==r && $3==n' "$TOUCHES_PRIOR" \
    | sort -t$'\t' -k4,4r \
    | head -1 \
    | cut -f4 || true)

  if [[ -z "$latest_prior" ]]; then
    continue  # No prior touch — not rework.
  fi

  # 8-second dedup: compare earliest today vs latest prior.
  today_epoch=$(date -d "$ts" +%s 2>/dev/null || echo "0")
  prior_epoch=$(date -d "$latest_prior" +%s 2>/dev/null || echo "0")
  delta=$(( today_epoch - prior_epoch ))

  if (( delta > 8 )); then
    # This is rework. Record it.
    printf '%s\t%s\t%s\t%s\t%s\n' "$bot" "$repo" "$number" "$ts" "$item_url" >> "$REWORK_ITEMS"
  fi
done < "$TOUCHES_DEDUPED"

# Write rework-details.csv rows.
if [[ -s "$REWORK_ITEMS" ]]; then
  while IFS=$'\t' read -r bot repo number ts item_url; do
    append_rework_detail "$ts" "$bot" "$repo" "$number" "$item_url"
  done < "$REWORK_ITEMS"
fi

# Build rework.csv summary rows.
# Count distinct items touched per bot (from TOUCHES_DEDUPED).
# Count distinct items reworked per bot (from REWORK_ITEMS).
bots=$(cut -f1 "$TOUCHES_DEDUPED" | sort -u)
total_touched=0
total_reworked=0

while IFS= read -r bot; do
  touched=$(awk -F'\t' -v b="$bot" '$1==b' "$TOUCHES_DEDUPED" | wc -l)
  reworked=0
  if [[ -s "$REWORK_ITEMS" ]]; then
    reworked=$(awk -F'\t' -v b="$bot" '$1==b' "$REWORK_ITEMS" | wc -l)
  fi

  if (( touched > 0 )); then
    rate=$(awk "BEGIN { printf \"%.3f\", ${reworked} / ${touched} }")
    append_rework_row "$TARGET_DATE" "$bot" "$touched" "$reworked" "$rate"
    total_touched=$(( total_touched + touched ))
    total_reworked=$(( total_reworked + reworked ))
  fi
done <<< "$bots"

# Write aggregate row.
if (( total_touched > 0 )); then
  agg_rate=$(awk "BEGIN { printf \"%.3f\", ${total_reworked} / ${total_touched} }")
  append_rework_row "$TARGET_DATE" "__aggregate__" "$total_touched" "$total_reworked" "$agg_rate"
fi

echo "Done. Rework metrics for ${TARGET_DATE} written."
