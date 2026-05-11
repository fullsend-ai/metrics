# Metric Details Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a details page that shows the full audit trail (every event) behind each dashboard metric for a given date or date range, plus collect and backfill event-level data for all metrics.

**Architecture:** Extend the three collection scripts (`collect.sh`, `collect-rework.sh`, `collect-failures.sh`) to emit event-level detail CSVs alongside their existing summary CSVs. Add a static `details.html` page that loads these detail CSVs and renders sortable tables. Dashboard chart clicks navigate to the details page.

**Tech Stack:** Bash, `gh` CLI, `jq`, GitHub REST API, D3.js v7, vanilla JS/CSS

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `scripts/lib.sh` | Modify | Add new CSV constants/helpers, update rework details header |
| `scripts/collect.sh` | Modify | Emit metric detail rows (PR/issue events) |
| `scripts/collect-rework.sh` | Modify | Emit all touches with `is_rework` flag |
| `scripts/backfill-rework.sh` | Modify | Same touch recording change |
| `scripts/collect-failures.sh` | Modify | Emit failure detail rows (every workflow run) |
| `scripts/backfill-failure-details.sh` | Create | Backfill historical failure detail data |
| `scripts/backfill-metric-details.sh` | Create | Backfill historical PR/issue detail data |
| `docs/details.html` | Create | Details page HTML |
| `docs/details.js` | Create | Details page rendering logic |
| `docs/style.css` | Modify | Add detail table and tag styles |
| `docs/dashboard.js` | Modify | Chart clicks navigate to details page |
| `docs/index.html` | Modify | Remove rework details panel |

---

### Task 1: Add new CSV helpers to lib.sh

**Files:**
- Modify: `scripts/lib.sh`

- [ ] **Step 1: Add failure details constants and helpers**

Add the following after the existing `append_failure_row` function (after line 63) in `scripts/lib.sh`:

```bash
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
```

- [ ] **Step 2: Add metric details constants and helpers**

Add the following immediately after the failure details block:

```bash
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
```

- [ ] **Step 3: Update rework details header to include is_rework**

In `scripts/lib.sh`, change line 31:

```bash
REWORK_DETAILS_HEADER="datetime,bot,repo,item,url,is_rework"
```

And update `append_rework_detail` (line 47-49) to accept and write a 6th argument:

```bash
append_rework_detail() {
  echo "$1,$2,$3,$4,$5,$6" >> "$REWORK_DETAILS_FILE"
}
```

- [ ] **Step 4: Verify lib.sh syntax**

Run: `bash -n scripts/lib.sh`
Expected: no output (syntax OK)

- [ ] **Step 5: Commit**

```bash
git add scripts/lib.sh
git commit -m "feat: add failure/metric detail CSV helpers, extend rework details with is_rework"
```

---

### Task 2: Extend collect-rework.sh to emit all touches

**Files:**
- Modify: `scripts/collect-rework.sh`

- [ ] **Step 1: Change the rework-details.csv writing to emit ALL touches**

In `scripts/collect-rework.sh`, replace the rework-details writing block (lines 261-265):

Old code:
```bash
# Write rework-details.csv rows.
if [[ -s "$REWORK_ITEMS" ]]; then
  while IFS=$'\t' read -r bot repo number ts item_url; do
    append_rework_detail "$ts" "$bot" "$repo" "$number" "$item_url"
  done < "$REWORK_ITEMS"
fi
```

New code:
```bash
# Write rework-details.csv rows for ALL touches (not just rework).
while IFS=$'\t' read -r bot repo number ts item_url; do
  if [[ -s "$REWORK_ITEMS" ]] && grep -qP "^${bot}\t${repo}\t${number}\t" "$REWORK_ITEMS" 2>/dev/null; then
    append_rework_detail "$ts" "$bot" "$repo" "$number" "$item_url" "true"
  else
    append_rework_detail "$ts" "$bot" "$repo" "$number" "$item_url" "false"
  fi
done < "$TOUCHES_DEDUPED"
```

- [ ] **Step 2: Verify syntax**

Run: `bash -n scripts/collect-rework.sh`
Expected: no output (syntax OK)

- [ ] **Step 3: Commit**

```bash
git add scripts/collect-rework.sh
git commit -m "feat: emit all bot touches to rework-details.csv with is_rework flag"
```

---

### Task 3: Extend backfill-rework.sh to emit all touches

**Files:**
- Modify: `scripts/backfill-rework.sh`

- [ ] **Step 1: Change the rework-details.csv writing to emit ALL touches**

In `scripts/backfill-rework.sh`, replace the rework-details writing block (around line 265-269):

Old code:
```bash
  # Write rework-details.csv rows.
  if [[ -s "$REWORK" ]]; then
    while IFS=$'\t' read -r bot repo number ts item_url; do
      append_rework_detail "$ts" "$bot" "$repo" "$number" "$item_url"
    done < "$REWORK"
  fi
```

New code:
```bash
  # Write rework-details.csv rows for ALL touches (not just rework).
  while IFS=$'\t' read -r bot repo number ts item_url; do
    if [[ -s "$REWORK" ]] && grep -qP "^${bot}\t${repo}\t${number}\t" "$REWORK" 2>/dev/null; then
      append_rework_detail "$ts" "$bot" "$repo" "$number" "$item_url" "true"
    else
      append_rework_detail "$ts" "$bot" "$repo" "$number" "$item_url" "false"
    fi
  done < "$DEDUPED"
```

- [ ] **Step 2: Verify syntax**

Run: `bash -n scripts/backfill-rework.sh`
Expected: no output (syntax OK)

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill-rework.sh
git commit -m "feat: backfill all bot touches to rework-details.csv with is_rework flag"
```

---

### Task 4: Extend collect-failures.sh to emit detail rows

**Files:**
- Modify: `scripts/collect-failures.sh`

- [ ] **Step 1: Add failure detail CSV initialization**

In `scripts/collect-failures.sh`, after line 10 (`ensure_failure_csv`), add:

```bash
ensure_failure_details_csv
```

- [ ] **Step 2: Extend the API query to capture run_id and html_url**

Replace the runs query (lines 33-35):

Old code:
```bash
runs=$(gh api "/repos/${AGENT_REPO}/actions/runs?per_page=100&created=${TARGET_DATE}" \
  --paginate \
  --jq '.workflow_runs[] | select(.status == "completed") | [.name, .conclusion] | @tsv' 2>/dev/null || true)
```

New code:
```bash
runs=$(gh api "/repos/${AGENT_REPO}/actions/runs?per_page=100&created=${TARGET_DATE}" \
  --paginate \
  --jq '.workflow_runs[] | select(.status == "completed") | [.name, .conclusion, (.id | tostring), .html_url] | @tsv' 2>/dev/null || true)
```

- [ ] **Step 3: Update the counting loop to emit detail rows**

Replace the counting loop (lines 52-58):

Old code:
```bash
while IFS=$'\t' read -r name conclusion; do
  [[ -z "${is_agent[$name]+x}" ]] && continue
  total_runs["$name"]=$(( ${total_runs["$name"]:-0} + 1 ))
  if [[ "$conclusion" == "failure" ]]; then
    failed_runs["$name"]=$(( ${failed_runs["$name"]:-0} + 1 ))
  fi
done <<< "$runs"
```

New code:
```bash
while IFS=$'\t' read -r name conclusion run_id run_url; do
  [[ -z "${is_agent[$name]+x}" ]] && continue
  total_runs["$name"]=$(( ${total_runs["$name"]:-0} + 1 ))
  if [[ "$conclusion" == "failure" ]]; then
    failed_runs["$name"]=$(( ${failed_runs["$name"]:-0} + 1 ))
    append_failure_detail "$TARGET_DATE" "$name" ".fullsend" "$run_id" "failure" "$run_url"
  else
    append_failure_detail "$TARGET_DATE" "$name" ".fullsend" "$run_id" "success" "$run_url"
  fi
done <<< "$runs"
```

- [ ] **Step 4: Verify syntax**

Run: `bash -n scripts/collect-failures.sh`
Expected: no output (syntax OK)

- [ ] **Step 5: Commit**

```bash
git add scripts/collect-failures.sh
git commit -m "feat: emit failure detail rows for every workflow run"
```

---

### Task 5: Extend collect.sh to emit metric detail rows

**Files:**
- Modify: `scripts/collect.sh`

- [ ] **Step 1: Add metric detail CSV initialization**

In `scripts/collect.sh`, after line 16 (`ensure_csv`), add:

```bash
ensure_metric_details_csv
```

- [ ] **Step 2: Add detail row emission for PRs opened**

After the `prs_opened` query (after line 37), add:

```bash
  if (( prs_opened > 0 )); then
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:pr created:${TARGET_DATE}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$TARGET_DATE" "$repo" "pr" "opened" "$number" "$title" "$url"
    done
  fi
```

- [ ] **Step 3: Add detail row emission for PRs merged**

After the `prs_merged` query (after line 42), add:

```bash
  if (( prs_merged > 0 )); then
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:pr is:merged merged:${TARGET_DATE}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$TARGET_DATE" "$repo" "pr" "merged" "$number" "$title" "$url"
    done
  fi
```

- [ ] **Step 4: Add detail row emission for PRs closed (non-merge)**

After the `prs_closed` calculation (after line 49), add:

```bash
  if (( prs_closed > 0 )); then
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:pr is:closed is:unmerged closed:${TARGET_DATE}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$TARGET_DATE" "$repo" "pr" "closed" "$number" "$title" "$url"
    done
  fi
```

- [ ] **Step 5: Add detail row emission for issues opened**

After the `issues_opened` query (after line 54), add:

```bash
  if (( issues_opened > 0 )); then
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:issue created:${TARGET_DATE}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$TARGET_DATE" "$repo" "issue" "opened" "$number" "$title" "$url"
    done
  fi
```

- [ ] **Step 6: Add detail row emission for issues closed**

After the `issues_closed` query (after line 59), add:

```bash
  if (( issues_closed > 0 )); then
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:issue is:closed closed:${TARGET_DATE}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$TARGET_DATE" "$repo" "issue" "closed" "$number" "$title" "$url"
    done
  fi
```

- [ ] **Step 7: Verify syntax**

Run: `bash -n scripts/collect.sh`
Expected: no output (syntax OK)

- [ ] **Step 8: Commit**

```bash
git add scripts/collect.sh
git commit -m "feat: emit metric detail rows for PR and issue events"
```

---

### Task 6: Create backfill-failure-details.sh

**Files:**
- Create: `scripts/backfill-failure-details.sh`

- [ ] **Step 1: Create the backfill script**

Create `scripts/backfill-failure-details.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

START_DATE="${1:-2026-04-06}"
END_DATE="${2:-$(date -d yesterday +%Y-%m-%d)}"

echo "Backfilling failure details from ${START_DATE} to ${END_DATE}..."

ensure_failure_details_csv

AGENT_REPO="fullsend-ai/.fullsend"

AGENT_WORKFLOWS=(
  "Code"
  "Fix"
  "Review"
  "Triage"
  "Prioritize"
  "Retro"
  "Scribe"
  "Classify GitHub Issues"
)

declare -A is_agent
for wf in "${AGENT_WORKFLOWS[@]}"; do
  is_agent["$wf"]=1
done

current="$START_DATE"
while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
  # Skip if this date already has data.
  if grep -q "^${current}," "$FAILURE_DETAILS_FILE" 2>/dev/null; then
    echo "  ${current}: already exists, skipping."
    current=$(date -d "${current} + 1 day" +%Y-%m-%d)
    continue
  fi

  echo "  ${current}: fetching workflow runs..."
  runs=$(gh api "/repos/${AGENT_REPO}/actions/runs?per_page=100&created=${current}" \
    --paginate \
    --jq '.workflow_runs[] | select(.status == "completed") | [.name, .conclusion, (.id | tostring), .html_url] | @tsv' 2>/dev/null || true)

  if [[ -n "$runs" ]]; then
    while IFS=$'\t' read -r name conclusion run_id run_url; do
      [[ -z "${is_agent[$name]+x}" ]] && continue
      if [[ "$conclusion" == "failure" ]]; then
        append_failure_detail "$current" "$name" ".fullsend" "$run_id" "failure" "$run_url"
      else
        append_failure_detail "$current" "$name" ".fullsend" "$run_id" "success" "$run_url"
      fi
    done <<< "$runs"
  fi

  sleep 2
  current=$(date -d "${current} + 1 day" +%Y-%m-%d)
done

# Sort by date (keep header first).
header=$(head -1 "$FAILURE_DETAILS_FILE")
tail -n +2 "$FAILURE_DETAILS_FILE" | sort -t, -k1,1 > /tmp/fd_sorted.csv
echo "$header" > "$FAILURE_DETAILS_FILE"
cat /tmp/fd_sorted.csv >> "$FAILURE_DETAILS_FILE"
rm -f /tmp/fd_sorted.csv

echo "Backfill complete. $(( $(wc -l < "$FAILURE_DETAILS_FILE") - 1 )) detail rows."
```

- [ ] **Step 2: Make it executable and verify syntax**

Run: `chmod +x scripts/backfill-failure-details.sh && bash -n scripts/backfill-failure-details.sh`
Expected: no output (syntax OK)

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill-failure-details.sh
git commit -m "feat: add failure details backfill script"
```

---

### Task 7: Create backfill-metric-details.sh

**Files:**
- Create: `scripts/backfill-metric-details.sh`

- [ ] **Step 1: Create the backfill script**

Create `scripts/backfill-metric-details.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

START_DATE="${1:-2026-04-06}"
END_DATE="${2:-$(date -d yesterday +%Y-%m-%d)}"

echo "Backfilling metric details from ${START_DATE} to ${END_DATE}..."

ensure_metric_details_csv

repos=$(list_repos)

current="$START_DATE"
while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
  # Skip if this date already has data.
  if grep -q "^${current}," "$METRIC_DETAILS_FILE" 2>/dev/null; then
    echo "  ${current}: already exists, skipping."
    current=$(date -d "${current} + 1 day" +%Y-%m-%d)
    continue
  fi

  echo "  ${current}: fetching PR/issue events..."

  for repo in $repos; do
    full_repo="${ORG}/${repo}"

    # PRs opened
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:pr created:${current}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$current" "$repo" "pr" "opened" "$number" "$title" "$url"
    done

    # PRs merged
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:pr is:merged merged:${current}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$current" "$repo" "pr" "merged" "$number" "$title" "$url"
    done

    # PRs closed (non-merge)
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:pr is:closed is:unmerged closed:${current}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$current" "$repo" "pr" "closed" "$number" "$title" "$url"
    done

    # Issues opened
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:issue created:${current}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$current" "$repo" "issue" "opened" "$number" "$title" "$url"
    done

    # Issues closed
    gh api "/search/issues" --method GET --paginate \
      -f q="repo:${full_repo} is:issue is:closed closed:${current}" \
      --jq '.items[] | [.number, .title, .html_url] | @tsv' 2>/dev/null | \
    while IFS=$'\t' read -r number title url; do
      append_metric_detail "$current" "$repo" "issue" "closed" "$number" "$title" "$url"
    done
  done

  sleep 3
  current=$(date -d "${current} + 1 day" +%Y-%m-%d)
done

# Sort by date (keep header first).
header=$(head -1 "$METRIC_DETAILS_FILE")
tail -n +2 "$METRIC_DETAILS_FILE" | sort -t, -k1,1 > /tmp/md_sorted.csv
echo "$header" > "$METRIC_DETAILS_FILE"
cat /tmp/md_sorted.csv >> "$METRIC_DETAILS_FILE"
rm -f /tmp/md_sorted.csv

echo "Backfill complete. $(( $(wc -l < "$METRIC_DETAILS_FILE") - 1 )) detail rows."
```

- [ ] **Step 2: Make it executable and verify syntax**

Run: `chmod +x scripts/backfill-metric-details.sh && bash -n scripts/backfill-metric-details.sh`
Expected: no output (syntax OK)

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill-metric-details.sh
git commit -m "feat: add metric details backfill script"
```

---

### Task 8: Add detail page styles to style.css

**Files:**
- Modify: `docs/style.css`

- [ ] **Step 1: Read current style.css to find insertion point**

Read `docs/style.css` and note the last line. Add the new styles at the end of the file.

- [ ] **Step 2: Add detail page styles**

Append to `docs/style.css`:

```css
/* --- Details Page --- */
.details-header {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  flex-wrap: wrap;
  margin-bottom: 1.5rem;
}

.details-header h1 {
  margin: 0;
  font-size: 1.25rem;
}

.details-header a {
  color: var(--accent);
  text-decoration: none;
  font-size: 0.875rem;
}

.details-header a:hover {
  text-decoration: underline;
}

.date-picker {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8125rem;
}

.date-picker input[type="date"] {
  background: var(--surface);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.25rem 0.5rem;
  font-size: 0.8125rem;
}

.date-picker button {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 4px;
  padding: 0.25rem 0.75rem;
  cursor: pointer;
  font-size: 0.8125rem;
}

.detail-section {
  margin-bottom: 2rem;
}

.detail-section h2 {
  cursor: pointer;
  user-select: none;
  font-size: 1rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.detail-section h2::before {
  content: "▾";
  font-size: 0.75rem;
  transition: transform 0.15s;
}

.detail-section.collapsed h2::before {
  transform: rotate(-90deg);
}

.detail-section.collapsed .detail-body {
  display: none;
}

.detail-summary {
  color: var(--fg-light);
  font-size: 0.8125rem;
  margin: 0.25rem 0 0.75rem;
}

.detail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.75rem;
}

.detail-table thead {
  position: sticky;
  top: 0;
  z-index: 1;
}

.detail-table th {
  background: var(--surface);
  text-transform: uppercase;
  font-size: 0.6875rem;
  color: var(--fg-light);
  padding: 0.375rem 0.625rem;
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
}

.detail-table th:hover {
  color: var(--fg);
}

.detail-table th .sort-arrow {
  margin-left: 0.25rem;
  font-size: 0.5rem;
}

.detail-table td {
  padding: 0.375rem 0.625rem;
  border-top: 1px solid var(--border);
}

.detail-table tbody tr:hover td {
  background: var(--surface);
}

.detail-table tbody tr:nth-child(even) td {
  background: color-mix(in srgb, var(--surface) 50%, transparent);
}

.detail-table tbody tr:nth-child(even):hover td {
  background: var(--surface);
}

.detail-table a {
  color: var(--accent);
  text-decoration: none;
}

.detail-table a:hover {
  text-decoration: underline;
}

.tag {
  display: inline-block;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
  font-size: 0.625rem;
  font-weight: 600;
  text-transform: uppercase;
}

.tag-yes, .tag-failure {
  background: color-mix(in srgb, var(--negative) 15%, transparent);
  color: var(--negative);
}

.tag-no, .tag-success {
  background: color-mix(in srgb, var(--positive) 15%, transparent);
  color: var(--positive);
}

.tag-opened {
  background: color-mix(in srgb, var(--chart-1) 15%, transparent);
  color: var(--chart-1);
}

.tag-merged {
  background: color-mix(in srgb, var(--positive) 15%, transparent);
  color: var(--positive);
}

.tag-closed {
  background: color-mix(in srgb, var(--negative) 15%, transparent);
  color: var(--negative);
}

.row-highlight td {
  background: color-mix(in srgb, var(--negative) 6%, transparent) !important;
}
```

- [ ] **Step 3: Commit**

```bash
git add docs/style.css
git commit -m "feat: add detail page styles"
```

---

### Task 9: Create details.html

**Files:**
- Create: `docs/details.html`

- [ ] **Step 1: Create the details page HTML**

Create `docs/details.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Metric Details</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <div class="container" style="max-width: 960px; margin: 2rem auto; padding: 0 1rem;">
    <div class="details-header">
      <a href="index.html">← Dashboard</a>
      <h1 id="page-title">Details</h1>
      <div class="date-picker">
        <label>From <input type="date" id="date-from"></label>
        <label>To <input type="date" id="date-to"></label>
        <button id="date-update">Update</button>
      </div>
    </div>

    <div id="section-rework" class="detail-section">
      <h2>Rework</h2>
      <div class="detail-body">
        <p class="detail-summary" id="rework-summary"></p>
        <div style="overflow-x: auto;">
          <table class="detail-table" id="rework-table">
            <thead><tr>
              <th data-col="datetime">Time</th>
              <th data-col="bot">Bot</th>
              <th data-col="repo">Repo</th>
              <th data-col="item">Item</th>
              <th data-col="is_rework">Rework</th>
            </tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>

    <div id="section-failures" class="detail-section">
      <h2>Failures</h2>
      <div class="detail-body">
        <p class="detail-summary" id="failure-summary"></p>
        <div style="overflow-x: auto;">
          <table class="detail-table" id="failure-table">
            <thead><tr>
              <th data-col="date">Date</th>
              <th data-col="workflow">Workflow</th>
              <th data-col="repo">Repo</th>
              <th data-col="run_id">Run</th>
              <th data-col="status">Status</th>
            </tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>

    <div id="section-metrics" class="detail-section">
      <h2>PR &amp; Issue Activity</h2>
      <div class="detail-body">
        <p class="detail-summary" id="metric-summary"></p>
        <div style="overflow-x: auto;">
          <table class="detail-table" id="metric-table">
            <thead><tr>
              <th data-col="date">Date</th>
              <th data-col="repo">Repo</th>
              <th data-col="type">Type</th>
              <th data-col="event">Event</th>
              <th data-col="number">Number</th>
              <th data-col="title">Title</th>
            </tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/d3@7"></script>
  <script src="details.js"></script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add docs/details.html
git commit -m "feat: add details page HTML"
```

---

### Task 10: Create details.js

**Files:**
- Create: `docs/details.js`

- [ ] **Step 1: Create the details page JavaScript**

Create `docs/details.js`. Note: all dynamic content uses safe DOM methods (`textContent`, `createElement`, `setAttribute`) rather than innerHTML to prevent XSS from user-generated content (PR titles, bot names).

```javascript
(async function () {
  // --- HTML escape helper (for any remaining dynamic HTML) ---
  function esc(s) {
    const el = document.createElement("span");
    el.textContent = s;
    return el.innerHTML;
  }

  // --- Parse URL params ---
  const params = new URLSearchParams(window.location.search);
  let dateFrom, dateTo;
  if (params.has("date")) {
    dateFrom = dateTo = params.get("date");
  } else if (params.has("from") && params.has("to")) {
    dateFrom = params.get("from");
    dateTo = params.get("to");
  } else {
    const yesterday = d3.timeFormat("%Y-%m-%d")(d3.timeDay.offset(new Date(), -1));
    dateFrom = dateTo = yesterday;
  }

  document.getElementById("date-from").value = dateFrom;
  document.getElementById("date-to").value = dateTo;

  // --- Page title ---
  function formatTitle(from, to) {
    const fmt = d3.timeFormat("%b %-d, %Y");
    const d1 = new Date(from + "T00:00:00");
    const d2 = new Date(to + "T00:00:00");
    if (from === to) return "Details for " + fmt(d1);
    return "Details for " + d3.timeFormat("%b %-d")(d1) + " – " + fmt(d2);
  }
  document.getElementById("page-title").textContent = formatTitle(dateFrom, dateTo);

  // --- Date picker ---
  document.getElementById("date-update").addEventListener("click", () => {
    const from = document.getElementById("date-from").value;
    const to = document.getElementById("date-to").value;
    if (from && to) {
      const p = new URLSearchParams();
      if (from === to) {
        p.set("date", from);
      } else {
        p.set("from", from);
        p.set("to", to);
      }
      window.location.search = p.toString();
    }
  });

  // --- Collapsible sections ---
  document.querySelectorAll(".detail-section h2").forEach(h2 => {
    h2.addEventListener("click", () => {
      h2.closest(".detail-section").classList.toggle("collapsed");
    });
  });

  // --- Load CSVs ---
  let reworkDetails = [], failureDetails = [], metricDetails = [];
  try {
    reworkDetails = await d3.csv("rework-details.csv", d => ({
      datetime: d.datetime,
      bot: d.bot,
      repo: d.repo,
      item: +d.item,
      url: d.url,
      is_rework: d.is_rework === "true",
    }));
  } catch (e) { /* file may not exist */ }
  try {
    failureDetails = await d3.csv("failure-details.csv", d => ({
      date: d.date,
      workflow: d.workflow,
      repo: d.repo,
      run_id: d.run_id,
      status: d.status,
      url: d.url,
    }));
  } catch (e) { /* file may not exist */ }
  try {
    metricDetails = await d3.csv("metric-details.csv", d => ({
      date: d.date,
      repo: d.repo,
      type: d.type,
      event: d.event,
      number: +d.number,
      title: d.title,
      url: d.url,
    }));
  } catch (e) { /* file may not exist */ }

  // --- Filter to date range ---
  const inRange = (d) => d >= dateFrom && d <= dateTo;
  const rework = reworkDetails.filter(d => inRange(d.datetime.substring(0, 10)));
  const failures = failureDetails.filter(d => inRange(d.date));
  const metrics = metricDetails.filter(d => inRange(d.date));

  // --- DOM table builder helper ---
  function buildRow(cells) {
    const tr = document.createElement("tr");
    cells.forEach(cell => {
      const td = document.createElement("td");
      if (cell.href) {
        const a = document.createElement("a");
        a.href = cell.href;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = cell.text;
        td.appendChild(a);
      } else if (cell.tag) {
        const span = document.createElement("span");
        span.className = "tag tag-" + cell.tagClass;
        span.textContent = cell.tag;
        td.appendChild(span);
      } else {
        td.textContent = cell.text || "";
      }
      tr.appendChild(td);
    });
    return tr;
  }

  // --- Sortable table helper ---
  function makeTable(tableId, data, rowBuilder, defaultSort, defaultDir, highlightFn) {
    const table = document.getElementById(tableId);
    let sortCol = defaultSort;
    let sortDir = defaultDir || "asc";

    function render() {
      const sorted = [...data].sort((a, b) => {
        let va = a[sortCol], vb = b[sortCol];
        if (typeof va === "boolean") { va = va ? 1 : 0; vb = vb ? 1 : 0; }
        if (typeof va === "string") { va = va.toLowerCase(); vb = vb.toLowerCase(); }
        if (va < vb) return sortDir === "asc" ? -1 : 1;
        if (va > vb) return sortDir === "asc" ? 1 : -1;
        return 0;
      });

      const tbody = table.querySelector("tbody");
      tbody.replaceChildren();
      sorted.forEach(d => {
        const tr = rowBuilder(d);
        if (highlightFn && highlightFn(d)) tr.classList.add("row-highlight");
        tbody.appendChild(tr);
      });

      // Update sort arrows
      table.querySelectorAll("th").forEach(th => {
        const arrow = th.querySelector(".sort-arrow");
        if (arrow) arrow.remove();
        if (th.dataset.col === sortCol) {
          const span = document.createElement("span");
          span.className = "sort-arrow";
          span.textContent = sortDir === "asc" ? "▲" : "▼";
          th.appendChild(span);
        }
      });
    }

    table.querySelectorAll("th").forEach(th => {
      th.addEventListener("click", () => {
        const col = th.dataset.col;
        if (sortCol === col) {
          sortDir = sortDir === "asc" ? "desc" : "asc";
        } else {
          sortCol = col;
          sortDir = "asc";
        }
        render();
      });
    });

    render();
  }

  // --- Rework section ---
  const reworkCount = rework.filter(d => d.is_rework).length;
  const reworkBots = new Set(rework.map(d => d.bot)).size;
  const reworkRate = rework.length > 0 ? (reworkCount / rework.length * 100).toFixed(1) : "0.0";
  document.getElementById("rework-summary").textContent =
    rework.length + " items touched by " + reworkBots + " bots, " + reworkCount + " rework items (" + reworkRate + "%)";

  makeTable("rework-table", rework, d => buildRow([
    { text: d.datetime.substring(0, 19).replace("T", " ") },
    { text: d.bot },
    { text: d.repo },
    { href: d.url, text: "#" + d.item },
    { tag: d.is_rework ? "yes" : "no", tagClass: d.is_rework ? "yes" : "no" },
  ]), "datetime", "asc", d => d.is_rework);

  // --- Failures section ---
  const failCount = failures.filter(d => d.status === "failure").length;
  const failRate = failures.length > 0 ? (failCount / failures.length * 100).toFixed(1) : "0.0";
  document.getElementById("failure-summary").textContent =
    failures.length + " workflow runs, " + failCount + " failures (" + failRate + "%)";

  makeTable("failure-table", failures, d => buildRow([
    { text: d.date },
    { text: d.workflow },
    { text: d.repo },
    { href: d.url, text: d.run_id },
    { tag: d.status, tagClass: d.status },
  ]), "date", "asc", d => d.status === "failure");

  // --- PR & Issue section ---
  const prOpened = metrics.filter(d => d.type === "pr" && d.event === "opened").length;
  const prMerged = metrics.filter(d => d.type === "pr" && d.event === "merged").length;
  const prClosed = metrics.filter(d => d.type === "pr" && d.event === "closed").length;
  const issOpened = metrics.filter(d => d.type === "issue" && d.event === "opened").length;
  const issClosed = metrics.filter(d => d.type === "issue" && d.event === "closed").length;
  document.getElementById("metric-summary").textContent =
    prOpened + " PRs opened, " + prMerged + " merged, " + prClosed + " closed · " +
    issOpened + " issues opened, " + issClosed + " closed";

  makeTable("metric-table", metrics, d => buildRow([
    { text: d.date },
    { text: d.repo },
    { text: d.type.toUpperCase() },
    { tag: d.event, tagClass: d.event },
    { href: d.url, text: "#" + d.number },
    { text: d.title },
  ]), "date", "asc");
})();
```

- [ ] **Step 2: Commit**

```bash
git add docs/details.js
git commit -m "feat: add details page rendering logic"
```

---

### Task 11: Update dashboard.js to navigate to details page

**Files:**
- Modify: `docs/dashboard.js`
- Modify: `docs/index.html`

- [ ] **Step 1: Replace rework rate chart click handler**

In `docs/dashboard.js`, replace the rework rate click handler (lines 989-991):

Old code:
```javascript
        .on("click", function (event, d) {
          showReworkDetails(d.date, d.bot);
        });
```

New code:
```javascript
        .on("click", function (event, d) {
          window.location.href = "details.html?date=" + d.date;
        });
```

- [ ] **Step 2: Add click handlers to failure rate chart dots**

In `docs/dashboard.js`, in `renderFailureRateChart`, find the mouseout handler on the dots (around line 867-868):

```javascript
        .on("mouseout", function () {
          d3.select(this).attr("opacity", 0).attr("r", 3);
          hideTooltip();
        })
```

Add after it (before the semicolon that closes the chain):

```javascript
        .on("click", function (event, d) {
          window.location.href = "details.html?date=" + d.date;
        })
```

- [ ] **Step 3: Add click handlers to bot activity chart dots**

In `docs/dashboard.js`, in `renderBotActivityChart`, find the mouseout handler on the dots (around line 1061-1062). Change the trailing semicolon to a comma and add:

```javascript
        .on("click", function (event, d) {
          window.location.href = "details.html?date=" + d.date;
        });
```

- [ ] **Step 4: Add click handlers to PR volume chart dots**

In `docs/dashboard.js`, in `renderPRVolumeChart`, find the mouseout handler on the dots. Add after it:

```javascript
        .on("click", function (event, d) {
          window.location.href = "details.html?date=" + d.date;
        })
```

- [ ] **Step 5: Add click handlers to issue volume chart dots**

In `docs/dashboard.js`, in `renderIssueVolumeChart`, find the mouseout handler on the dots. Add after it:

```javascript
        .on("click", function (event, d) {
          window.location.href = "details.html?date=" + d.date;
        })
```

- [ ] **Step 6: Remove showReworkDetails function**

In `docs/dashboard.js`, delete the `showReworkDetails` function (lines 1075-1098).

- [ ] **Step 7: Remove rework-details-panel from index.html**

In `docs/index.html`, remove the line (line 80):

```html
    <div id="rework-details-panel" class="details-panel"></div>
```

- [ ] **Step 8: Commit**

```bash
git add docs/dashboard.js docs/index.html
git commit -m "feat: chart clicks navigate to details page, remove inline rework panel"
```

---

### Task 12: Run backfills and verify

- [ ] **Step 1: Re-run rework backfill to get all-touches data**

Run: `./scripts/backfill-rework.sh 2026-04-06 2026-05-10`

Expected: rework-details.csv now contains ALL touches with `is_rework` column. Row count should be significantly higher than before (was recording only rework events, now records all touches).

Verify: `head -3 docs/rework-details.csv` — should show `datetime,bot,repo,item,url,is_rework` header and rows with `true` or `false` in the last column.

- [ ] **Step 2: Run failure details backfill**

Run: `./scripts/backfill-failure-details.sh 2026-04-06 2026-05-10`

Expected: `docs/failure-details.csv` is created with per-run detail rows. Each row has date, workflow, repo, run_id, status (success/failure), and url.

Verify: `head -5 docs/failure-details.csv` — should show header and data rows. `wc -l docs/failure-details.csv` — should have hundreds of rows (one per workflow run).

- [ ] **Step 3: Run metric details backfill**

Run: `./scripts/backfill-metric-details.sh 2026-04-06 2026-05-10`

Expected: `docs/metric-details.csv` is created with per-event detail rows. Each row has date, repo, type, event, number, title, and url.

Verify: `head -5 docs/metric-details.csv` — should show header and data rows with PR/issue details.

- [ ] **Step 4: Verify details page renders**

Open `docs/details.html?date=2026-05-07` in a browser. Verify:
- Title shows "Details for May 7, 2026"
- Rework section shows a table with all bot touches, rework rows highlighted
- Failures section shows all workflow runs, failures highlighted
- PR & Issue section shows all events with tags
- Clicking column headers sorts the table
- Date picker works (change date, click Update, page reloads with new data)
- "← Dashboard" link navigates to index.html
- No JavaScript console errors

- [ ] **Step 5: Verify dashboard navigation**

Open `docs/index.html` in a browser. Verify:
- Clicking a rework rate chart point navigates to details.html with the correct date
- Clicking a failure rate chart point navigates to details.html
- Clicking PR/issue volume chart points navigates to details.html
- The old rework details panel no longer appears

- [ ] **Step 6: Commit backfilled data**

```bash
git add docs/rework-details.csv docs/failure-details.csv docs/metric-details.csv
git commit -m "data: backfill all detail CSVs for audit trail"
```
