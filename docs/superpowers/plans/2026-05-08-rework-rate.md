# Rework Rate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "rework rate" metric that tracks how often bot agents re-touch work items they previously touched, per bot and in aggregate, displayed on the existing D3 dashboard.

**Architecture:** A new shell script (`collect-rework.sh`) queries the GitHub timeline events API and GraphQL Projects v2 API to discover bot touches on issues/PRs. It writes raw rework events to `rework-details.csv` and daily summaries to `rework.csv`. The D3 dashboard loads both CSVs and renders two new multi-line charts (rework rate and bot activity) plus a summary card.

**Tech Stack:** Bash, `gh` CLI, `jq`, GitHub REST + GraphQL APIs, D3.js v7

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `scripts/lib.sh` | Modify | Add rework CSV path constants, ensure/append helpers |
| `scripts/collect-rework.sh` | Create | Daily rework metric collection — discovers bot touches via timeline API + GraphQL, determines rework, writes both CSVs |
| `scripts/backfill-rework.sh` | Create | Iterates date range calling collect-rework logic per day |
| `docs/index.html` | Modify | Add two chart sections (rework rate, bot activity) |
| `docs/dashboard.js` | Modify | Load rework CSVs, render new charts and summary card |
| `.github/workflows/collect.yml` | Modify | Add rework collection step |

Runtime artifacts (not checked in, populated by scripts):
- `docs/rework.csv` — daily summary (append-only)
- `docs/rework-details.csv` — raw rework events (append-only)

---

### Task 1: Add rework CSV helpers to lib.sh

**Files:**
- Modify: `scripts/lib.sh`

- [ ] **Step 1: Add rework constants and helpers to lib.sh**

Add the following after the existing `append_row` function in `scripts/lib.sh`:

```bash
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
```

- [ ] **Step 2: Verify lib.sh still sources cleanly**

Run: `bash -n scripts/lib.sh`
Expected: no output (syntax OK)

- [ ] **Step 3: Commit**

```bash
git add scripts/lib.sh
git commit -m "feat: add rework CSV helpers to lib.sh"
```

---

### Task 2: Create collect-rework.sh

**Files:**
- Create: `scripts/collect-rework.sh`

- [ ] **Step 1: Create the collection script**

Create `scripts/collect-rework.sh` with the following content:

```bash
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
# Each line: bot\trepo\titem\ttimestamp\tevent_id\tevent_url
TOUCHES_TODAY=$(mktemp)
TOUCHES_PRIOR=$(mktemp)
trap 'rm -f "$TOUCHES_TODAY" "$TOUCHES_PRIOR"' EXIT

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
project_items=$(gh api graphql -f query='
  query {
    organization(login: "'"${ORG}"'") {
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
TOUCHES_DEDUPED=$(mktemp)
trap 'rm -f "$TOUCHES_TODAY" "$TOUCHES_PRIOR" "$TOUCHES_DEDUPED"' EXIT

sort -t$'\t' -k1,3 -k4,4 "$TOUCHES_TODAY" | awk -F'\t' '
  !seen[$1 "\t" $2 "\t" $3]++ { print }
' > "$TOUCHES_DEDUPED"

# For each bot+repo+item in today's touches, check for prior-day touches.
# Apply 8-second cross-day dedup.
REWORK_ITEMS=$(mktemp)
trap 'rm -f "$TOUCHES_TODAY" "$TOUCHES_PRIOR" "$TOUCHES_DEDUPED" "$REWORK_ITEMS"' EXIT

while IFS=$'\t' read -r bot repo number ts item_url; do
  # Find the latest prior touch by this bot on this item.
  latest_prior=$(grep -P "^${bot}\t${repo}\t${number}\t" "$TOUCHES_PRIOR" 2>/dev/null \
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
    # Build URL with best-effort event anchor.
    url="${item_url}"
    append_rework_detail "$ts" "$bot" "$repo" "$number" "$url"
  done < "$REWORK_ITEMS"
fi

# Build rework.csv summary rows.
# Count distinct items touched per bot (from TOUCHES_DEDUPED).
# Count distinct items reworked per bot (from REWORK_ITEMS).
bots=$(cut -f1 "$TOUCHES_DEDUPED" | sort -u)
total_touched=0
total_reworked=0

while IFS= read -r bot; do
  touched=$(grep -cP "^${bot}\t" "$TOUCHES_DEDUPED" || true)
  reworked=0
  if [[ -s "$REWORK_ITEMS" ]]; then
    reworked=$(grep -cP "^${bot}\t" "$REWORK_ITEMS" || true)
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
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/collect-rework.sh`

- [ ] **Step 3: Verify syntax**

Run: `bash -n scripts/collect-rework.sh`
Expected: no output (syntax OK)

- [ ] **Step 4: Commit**

```bash
git add scripts/collect-rework.sh
git commit -m "feat: add rework metric collection script"
```

---

### Task 3: Verify collect-rework.sh against live API

**Files:** (none modified)

- [ ] **Step 1: Run the script for a recent active day**

Run: `./scripts/collect-rework.sh 2026-05-07`

Expected: the script discovers repos, searches for updated items, fetches timelines, and writes to `docs/rework.csv` and `docs/rework-details.csv`. Watch for API errors or empty results.

- [ ] **Step 2: Inspect rework.csv**

Run: `cat docs/rework.csv`

Expected: a header row followed by per-bot rows and an `__aggregate__` row for 2026-05-07. Verify:
- `items_touched` is a positive integer for each bot
- `items_reworked` <= `items_touched`
- `rework_rate` is a decimal between 0 and 1
- There is exactly one `__aggregate__` row for the date

- [ ] **Step 3: Inspect rework-details.csv**

Run: `cat docs/rework-details.csv`

Expected: a header row followed by rework event rows. Verify:
- Each URL is a valid GitHub link
- Each datetime falls on 2026-05-07
- The number of distinct bot+item combinations matches the per-bot `items_reworked` counts in rework.csv

- [ ] **Step 4: Run again to verify idempotency**

Run: `./scripts/collect-rework.sh 2026-05-07`

Expected: "Data for 2026-05-07 already exists in docs/rework.csv. Skipping."

- [ ] **Step 5: Fix any issues found, re-run if needed**

If the script produces errors or incorrect output, debug and fix. Common issues:
- jq filter mismatches on timeline event shapes (some events lack `actor` or `created_at`)
- GraphQL query field mismatches (adjust based on actual org project schema)
- `grep -P` not available (switch to `grep -E` or use awk)

- [ ] **Step 6: Clean up test data and commit any fixes**

```bash
# Remove test data so it doesn't get committed as static content.
git checkout -- docs/rework.csv docs/rework-details.csv 2>/dev/null || true
rm -f docs/rework.csv docs/rework-details.csv
git add scripts/collect-rework.sh
git diff --staged --quiet || git commit -m "fix: adjust collect-rework.sh based on live API testing"
```

---

### Task 4: Create backfill-rework.sh

**Files:**
- Create: `scripts/backfill-rework.sh`

- [ ] **Step 1: Create the backfill script**

Create `scripts/backfill-rework.sh` with the following content:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

START_DATE="${1:-2026-04-06}"
END_DATE="${2:-$(date -d yesterday +%Y-%m-%d)}"

echo "Backfilling rework metrics from ${START_DATE} to ${END_DATE}..."

ensure_rework_csv

current="$START_DATE"
while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
  # collect-rework.sh skips dates already present.
  echo "=== ${current} ==="
  "${SCRIPT_DIR}/collect-rework.sh" "$current"

  # Small delay to stay within API rate limits.
  sleep 5

  current=$(date -d "${current} + 1 day" +%Y-%m-%d)
done

# Sort rework.csv by date,bot (keep header first).
header=$(head -1 "$REWORK_FILE")
tail -n +2 "$REWORK_FILE" | sort -t, -k1,1 -k2,2 > /tmp/rework_sorted.csv
echo "$header" > "$REWORK_FILE"
cat /tmp/rework_sorted.csv >> "$REWORK_FILE"
rm -f /tmp/rework_sorted.csv

# Sort rework-details.csv by datetime (keep header first).
header=$(head -1 "$REWORK_DETAILS_FILE")
tail -n +2 "$REWORK_DETAILS_FILE" | sort -t, -k1,1 > /tmp/rework_details_sorted.csv
echo "$header" > "$REWORK_DETAILS_FILE"
cat /tmp/rework_details_sorted.csv >> "$REWORK_DETAILS_FILE"
rm -f /tmp/rework_details_sorted.csv

echo "Backfill complete. $(( $(wc -l < "$REWORK_FILE") - 1 )) summary rows in ${REWORK_FILE}."
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/backfill-rework.sh`

- [ ] **Step 3: Verify syntax**

Run: `bash -n scripts/backfill-rework.sh`
Expected: no output (syntax OK)

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill-rework.sh
git commit -m "feat: add rework metric backfill script"
```

---

### Task 5: Add chart sections to index.html

**Files:**
- Modify: `docs/index.html`

- [ ] **Step 1: Add rework chart sections after the issue volume chart**

In `docs/index.html`, add the following two chart sections after the `Issue Volume` section (before the `Per-Repo Breakdown` section):

```html
  <div class="chart-section">
    <h2>Bot Rework Rate</h2>
    <div id="chart-rework-rate"></div>
    <div id="rework-details-panel" class="details-panel"></div>
  </div>

  <div class="chart-section">
    <h2>Bot Activity (items touched)</h2>
    <div id="chart-bot-activity"></div>
  </div>
```

- [ ] **Step 2: Add CSS for the details panel**

In `docs/style.css`, add the following at the end:

```css
.details-panel {
  max-height: 200px;
  overflow-y: auto;
  font-size: 0.75rem;
  margin-top: 0.5rem;
}

.details-panel a {
  color: var(--accent);
  text-decoration: none;
}

.details-panel a:hover {
  text-decoration: underline;
}

.details-panel table {
  font-size: 0.75rem;
}
```

- [ ] **Step 3: Verify the HTML is valid**

Open `docs/index.html` in a browser. The new sections should appear as empty containers (no data yet). No JavaScript errors in the console.

- [ ] **Step 4: Commit**

```bash
git add docs/index.html docs/style.css
git commit -m "feat: add rework rate and bot activity chart sections to dashboard"
```

---

### Task 6: Add rework data loading and chart rendering to dashboard.js

**Files:**
- Modify: `docs/dashboard.js`

- [ ] **Step 1: Load rework CSVs alongside existing data**

In `docs/dashboard.js`, after the existing `const raw = await d3.csv(...)` block (around line 108), add:

```javascript
  // --- Load rework CSVs ---
  let reworkData = [];
  let reworkDetails = [];
  try {
    reworkData = await d3.csv("rework.csv", d => ({
      date: d.date,
      bot: d.bot,
      items_touched: +d.items_touched,
      items_reworked: +d.items_reworked,
      rework_rate: +d.rework_rate,
    }));
  } catch (e) { /* rework.csv may not exist yet */ }
  try {
    reworkDetails = await d3.csv("rework-details.csv", d => ({
      datetime: d.datetime,
      bot: d.bot,
      repo: d.repo,
      item: +d.item,
      url: d.url,
    }));
  } catch (e) { /* rework-details.csv may not exist yet */ }
```

- [ ] **Step 2: Add the bot color scale**

After the rework CSV loading, add:

```javascript
  // --- Bot color scale ---
  const botColors = d3.scaleOrdinal(d3.schemeTableau10);
```

- [ ] **Step 3: Add helper to filter rework data by date range and weekends**

```javascript
  function filterReworkData() {
    let data = reworkData;
    if (rangeDays > 0) {
      const cutoff = d3.timeDay.offset(new Date(), -rangeDays);
      const cutoffStr = d3.timeFormat("%Y-%m-%d")(cutoff);
      data = data.filter(d => d.date >= cutoffStr);
    }
    if (hideWeekends) {
      data = data.filter(d => {
        const day = new Date(d.date + "T00:00:00").getDay();
        return day !== 0 && day !== 6;
      });
    }
    return data;
  }
```

- [ ] **Step 4: Add rework rate chart rendering function**

```javascript
  function renderReworkRateChart(data) {
    const container = d3.select("#chart-rework-rate");
    if (data.length === 0) { container.select("svg").remove(); container.prepend("p").text("No data"); return; }
    container.select("p").remove();
    const dims = chartDimensions(container);
    const g = createSvg(container, dims);

    const bots = [...new Set(data.map(d => d.bot))];
    const x = d3.scaleTime()
      .domain(d3.extent(data, d => new Date(d.date)))
      .range([0, dims.innerW]);
    const y = d3.scaleLinear()
      .domain([0, 1])
      .range([dims.innerH, 0]);

    drawGrid(g, y, dims.innerW);
    drawXAxis(g, x, dims.innerH);
    g.append("g").attr("class", "axis").call(
      d3.axisLeft(y).ticks(5).tickFormat(d3.format(".0%"))
    );

    bots.forEach(bot => {
      const botData = data.filter(d => d.bot === bot);
      const isAggregate = bot === "__aggregate__";
      const color = isAggregate ? "var(--fg)" : botColors(bot);
      const width = isAggregate ? 3 : 1.5;
      const dasharray = isAggregate ? "6 3" : "none";

      g.append("path")
        .datum(botData)
        .attr("fill", "none")
        .attr("stroke", color)
        .attr("stroke-width", width)
        .attr("stroke-dasharray", dasharray)
        .attr("d", d3.line()
          .defined(d => !isNaN(d.rework_rate))
          .x(d => x(new Date(d.date)))
          .y(d => y(d.rework_rate))
          .curve(d3.curveMonotoneX));

      g.selectAll(`.rr-dot-${bot.replace(/[^a-zA-Z0-9]/g, "_")}`)
        .data(botData.filter(d => !isNaN(d.rework_rate)))
        .join("circle")
        .attr("cx", d => x(new Date(d.date)))
        .attr("cy", d => y(d.rework_rate))
        .attr("r", 3)
        .attr("fill", color)
        .attr("opacity", 0)
        .on("mouseover", function (event, d) {
          d3.select(this).attr("opacity", 1).attr("r", 5);
          showTooltip(event,
            `<strong>${d.date}</strong><br>` +
            `Bot: ${d.bot}<br>` +
            `Touched: ${d.items_touched}<br>` +
            `Reworked: ${d.items_reworked}<br>` +
            `Rate: ${(d.rework_rate * 100).toFixed(1)}%`
          );
        })
        .on("mouseout", function () {
          d3.select(this).attr("opacity", 0).attr("r", 3);
          hideTooltip();
        })
        .on("click", function (event, d) {
          showReworkDetails(d.date, d.bot);
        });
    });

    // Legend
    const legend = g.append("g").attr("transform", "translate(0, -8)");
    const displayBots = bots.filter(b => b !== "__aggregate__");
    displayBots.forEach((bot, i) => {
      const offset = i * 120;
      legend.append("line").attr("x1", offset).attr("x2", offset + 16).attr("y1", 0).attr("y2", 0)
        .attr("stroke", botColors(bot)).attr("stroke-width", 2);
      legend.append("text").attr("x", offset + 20).attr("y", 4)
        .attr("font-size", "0.6875rem").attr("fill", "var(--muted)").text(bot);
    });
    const aggOffset = displayBots.length * 120;
    legend.append("line").attr("x1", aggOffset).attr("x2", aggOffset + 16).attr("y1", 0).attr("y2", 0)
      .attr("stroke", "var(--fg)").attr("stroke-width", 3).attr("stroke-dasharray", "6 3");
    legend.append("text").attr("x", aggOffset + 20).attr("y", 4)
      .attr("font-size", "0.6875rem").attr("fill", "var(--muted)").text("Aggregate");
  }
```

- [ ] **Step 5: Add bot activity chart rendering function**

```javascript
  function renderBotActivityChart(data) {
    const container = d3.select("#chart-bot-activity");
    if (data.length === 0) { container.select("svg").remove(); container.prepend("p").text("No data"); return; }
    container.select("p").remove();
    const dims = chartDimensions(container);
    const g = createSvg(container, dims);

    const bots = [...new Set(data.map(d => d.bot))];
    const x = d3.scaleTime()
      .domain(d3.extent(data, d => new Date(d.date)))
      .range([0, dims.innerW]);
    const maxTouched = d3.max(data, d => d.items_touched) || 1;
    const y = yScale([0, maxTouched * 1.15]).range([dims.innerH, 0]);

    drawGrid(g, y, dims.innerW);
    drawXAxis(g, x, dims.innerH);
    drawYAxis(g, y);

    bots.forEach(bot => {
      const botData = data.filter(d => d.bot === bot);
      const isAggregate = bot === "__aggregate__";
      const color = isAggregate ? "var(--fg)" : botColors(bot);
      const width = isAggregate ? 3 : 1.5;
      const dasharray = isAggregate ? "6 3" : "none";

      g.append("path")
        .datum(botData)
        .attr("fill", "none")
        .attr("stroke", color)
        .attr("stroke-width", width)
        .attr("stroke-dasharray", dasharray)
        .attr("d", d3.line()
          .x(d => x(new Date(d.date)))
          .y(d => y(d.items_touched))
          .curve(d3.curveMonotoneX));

      g.selectAll(`.ba-dot-${bot.replace(/[^a-zA-Z0-9]/g, "_")}`)
        .data(botData)
        .join("circle")
        .attr("cx", d => x(new Date(d.date)))
        .attr("cy", d => y(d.items_touched))
        .attr("r", 3)
        .attr("fill", color)
        .attr("opacity", 0)
        .on("mouseover", function (event, d) {
          d3.select(this).attr("opacity", 1).attr("r", 5);
          showTooltip(event,
            `<strong>${d.date}</strong><br>` +
            `Bot: ${d.bot}<br>` +
            `Items touched: ${d.items_touched}<br>` +
            `Reworked: ${d.items_reworked}`
          );
        })
        .on("mouseout", function () {
          d3.select(this).attr("opacity", 0).attr("r", 3);
          hideTooltip();
        });
    });

    // Legend (same as rework rate chart)
    const legend = g.append("g").attr("transform", "translate(0, -8)");
    const displayBots = bots.filter(b => b !== "__aggregate__");
    displayBots.forEach((bot, i) => {
      const offset = i * 120;
      legend.append("line").attr("x1", offset).attr("x2", offset + 16).attr("y1", 0).attr("y2", 0)
        .attr("stroke", botColors(bot)).attr("stroke-width", 2);
      legend.append("text").attr("x", offset + 20).attr("y", 4)
        .attr("font-size", "0.6875rem").attr("fill", "var(--muted)").text(bot);
    });
    const aggOffset = displayBots.length * 120;
    legend.append("line").attr("x1", aggOffset).attr("x2", aggOffset + 16).attr("y1", 0).attr("y2", 0)
      .attr("stroke", "var(--fg)").attr("stroke-width", 3).attr("stroke-dasharray", "6 3");
    legend.append("text").attr("x", aggOffset + 20).attr("y", 4)
      .attr("font-size", "0.6875rem").attr("fill", "var(--muted)").text("Aggregate");
  }
```

- [ ] **Step 6: Add rework details panel function**

```javascript
  function showReworkDetails(date, bot) {
    const panel = d3.select("#rework-details-panel");
    const matching = reworkDetails.filter(d =>
      d.datetime.startsWith(date) && (bot === "__aggregate__" || d.bot === bot)
    );

    if (matching.length === 0) {
      panel.html("<p>No rework details for this date.</p>");
      return;
    }

    let html = `<table><thead><tr><th>Time</th><th>Bot</th><th>Repo</th><th>Item</th></tr></thead><tbody>`;
    matching.forEach(d => {
      const time = d.datetime.substring(11, 19);
      html += `<tr>
        <td>${time}</td>
        <td>${d.bot}</td>
        <td>${d.repo}</td>
        <td><a href="${d.url}" target="_blank" rel="noopener">#${d.item}</a></td>
      </tr>`;
    });
    html += `</tbody></table>`;
    panel.html(html);
  }
```

- [ ] **Step 7: Add rework summary card to renderSummaryCards**

In the existing `renderSummaryCards` function, add the rework rate card to the `metrics` array. After the line `{ label: "Releases", key: "releases", agg: d3.sum },`, the function needs to handle rework data separately since it comes from a different CSV.

Add a new block after the existing `metrics.forEach(...)` loop in `renderSummaryCards`:

```javascript
    // Rework rate summary card
    const rData = filterReworkData().filter(d => d.bot === "__aggregate__");
    const rThisWeek = rData.filter(d => d.date >= weekAgoStr);
    const rLastWeek = rData.filter(d => d.date >= twoWeeksAgoStr && d.date < weekAgoStr);

    if (rThisWeek.length > 0) {
      const currRate = d3.mean(rThisWeek, d => d.rework_rate) || 0;
      const prevRate = d3.mean(rLastWeek, d => d.rework_rate) || 0;
      const delta = prevRate > 0 ? ((currRate - prevRate) / prevRate * 100) : 0;
      const isPositive = delta <= 0; // lower rework is better

      const card = container.append("div").attr("class", "card");
      card.append("div").attr("class", "label").text("Rework Rate");
      card.append("div").attr("class", "value").text((currRate * 100).toFixed(1) + "%");
      if (prevRate > 0) {
        card.append("div")
          .attr("class", `delta ${isPositive ? "positive" : "negative"}`)
          .text(`${delta >= 0 ? "+" : ""}${delta.toFixed(0)}% vs prev week`);
      }
    }
```

- [ ] **Step 8: Wire new charts into the render function**

In the existing `render()` function, add calls to the new chart renderers after the existing chart calls:

```javascript
    const rData = filterReworkData();
    renderReworkRateChart(rData);
    renderBotActivityChart(rData);
```

- [ ] **Step 9: Verify dashboard renders without errors**

Open `docs/index.html` in a browser. With no rework CSVs present, the new chart sections should show "No data". No JavaScript console errors. Existing charts should be unaffected.

If rework CSVs exist from Task 3 testing, the new charts should render with lines.

- [ ] **Step 10: Commit**

```bash
git add docs/dashboard.js
git commit -m "feat: add rework rate and bot activity charts to dashboard"
```

---

### Task 7: Update GitHub Action

**Files:**
- Modify: `.github/workflows/collect.yml`

- [ ] **Step 1: Add rework collection step**

In `.github/workflows/collect.yml`, add a new step after the existing "Collect metrics" step and before the "Commit and push" step:

```yaml
      - name: Collect rework metrics
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          INPUT_DATE: ${{ github.event.inputs.date }}
        run: |
          if [[ -n "$INPUT_DATE" ]]; then
            ./scripts/collect-rework.sh "$INPUT_DATE"
          else
            ./scripts/collect-rework.sh
          fi
```

- [ ] **Step 2: Verify YAML is valid**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/collect.yml'))"`
Expected: no output (valid YAML)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/collect.yml
git commit -m "feat: add rework metric collection to daily workflow"
```

---

### Task 8: End-to-end verification

- [ ] **Step 1: Run collect-rework.sh for a known date**

```bash
./scripts/collect-rework.sh 2026-05-07
```

Verify output CSVs have expected content (as described in Task 3).

- [ ] **Step 2: Open dashboard and verify new charts**

Open `docs/index.html` in a browser. Verify:
- "Bot Rework Rate" chart renders with lines per bot and an aggregate dashed line
- "Bot Activity" chart renders with lines per bot and an aggregate dashed line
- "Rework Rate" summary card appears with a percentage value
- Clicking a data point on the rework chart shows a details panel with linked items
- Hovering shows tooltips with correct data
- Time range buttons, weekend toggle, and log scale all work on new charts
- Existing charts are unaffected
- No JavaScript console errors

- [ ] **Step 3: Verify zero-data edge case**

Run: `./scripts/collect-rework.sh 2026-04-25` (a day with minimal activity)

Check that if no bots touched anything, no rows are added to rework.csv for that date. The chart should show a gap for that date, not a zero.

- [ ] **Step 4: Clean up test data**

```bash
rm -f docs/rework.csv docs/rework-details.csv
```

- [ ] **Step 5: Final commit if any fixes were needed**

```bash
git add -A
git diff --staged --quiet || git commit -m "fix: adjustments from end-to-end testing"
```
