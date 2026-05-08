# Org Metrics Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a daily-updating SDLC metrics dashboard for fullsend-ai, published as a D3.js GitHub Pages site backed by an append-only CSV.

**Architecture:** Shell scripts using `gh api` and `jq` collect metrics from GitHub into `data/metrics.csv`. A daily GitHub Action appends new rows and regenerates `docs/metrics.csv`. A single `docs/index.html` page loads the CSV with D3.js and renders interactive charts. A one-time backfill script seeds historical data from April 1, 2025.

**Tech Stack:** Bash, `gh` CLI, `jq`, D3.js (v7, CDN), GitHub Actions, GitHub Pages

---

## File Map

| File | Purpose |
|------|---------|
| `scripts/lib.sh` | Shared functions: repo discovery, CSV append, date helpers |
| `scripts/collect.sh` | Collect one day's metrics for all repos (default: yesterday) |
| `scripts/backfill.sh` | Bulk-fetch historical data from 2025-04-01 to today, bucket by day |
| `data/metrics.csv` | Append-only data store, one row per repo per day |
| `docs/index.html` | Single-page D3.js dashboard |
| `docs/style.css` | Minimal styling, dark/light mode |
| `.github/workflows/collect.yml` | Daily cron Action |

---

### Task 1: Shared Library (`scripts/lib.sh`)

**Files:**
- Create: `scripts/lib.sh`

- [ ] **Step 1: Create `scripts/lib.sh` with repo discovery and CSV helpers**

```bash
#!/usr/bin/env bash
set -euo pipefail

ORG="fullsend-ai"
DATA_FILE="data/metrics.csv"
DOCS_CSV="docs/metrics.csv"
CSV_HEADER="date,repo,prs_opened,prs_merged,prs_closed,issues_opened,issues_closed,releases,pr_lead_time_median_hours"

ensure_csv() {
  mkdir -p data docs
  if [[ ! -f "$DATA_FILE" ]]; then
    echo "$CSV_HEADER" > "$DATA_FILE"
  fi
}

copy_csv_to_docs() {
  cp "$DATA_FILE" "$DOCS_CSV"
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
```

- [ ] **Step 2: Verify the script parses cleanly**

Run: `cd /home/rbean/code/metrics && bash -n scripts/lib.sh`
Expected: no output, exit 0

- [ ] **Step 3: Quick smoke test of `list_repos`**

Run: `cd /home/rbean/code/metrics && source scripts/lib.sh && list_repos | head -5`
Expected: a list of repo names from the fullsend-ai org

- [ ] **Step 4: Commit**

```bash
git add scripts/lib.sh
git commit -m "feat: add shared library for repo discovery and CSV helpers"
```

---

### Task 2: Daily Collection Script (`scripts/collect.sh`)

**Files:**
- Create: `scripts/collect.sh`

- [ ] **Step 1: Create `scripts/collect.sh`**

This script collects metrics for a single date (default: yesterday) across all org repos.

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

# Accept a date argument, default to yesterday.
if [[ $# -ge 1 ]]; then
  TARGET_DATE="$1"
else
  TARGET_DATE="$(date -d yesterday +%Y-%m-%d)"
fi

echo "Collecting metrics for ${TARGET_DATE}..."

ensure_csv

repos=$(list_repos)

for repo in $repos; do
  full_repo="${ORG}/${repo}"
  echo "  ${full_repo}..."

  # PRs opened on this date
  prs_opened=$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:pr created:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || echo "0")

  # PRs merged on this date
  prs_merged=$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:pr is:merged merged:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || echo "0")

  # PRs closed (not merged) on this date
  prs_closed_total=$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:pr is:closed closed:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || echo "0")
  prs_closed=$(( prs_closed_total - prs_merged ))
  if (( prs_closed < 0 )); then prs_closed=0; fi

  # Issues opened on this date (exclude PRs)
  issues_opened=$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:issue created:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || echo "0")

  # Issues closed on this date (exclude PRs)
  issues_closed=$(gh api "/search/issues" \
    --method GET \
    -f q="repo:${full_repo} is:issue is:closed closed:${TARGET_DATE}" \
    --jq '.total_count' 2>/dev/null || echo "0")

  # Releases published on this date
  releases=$(gh api "/repos/${full_repo}/releases" \
    --paginate \
    --jq "[.[] | select(.published_at | startswith(\"${TARGET_DATE}\"))] | length" 2>/dev/null || echo "0")

  # PR lead time: median hours from open to merge for PRs merged on this date.
  # Fetch the actual PR data to get created_at and merged_at timestamps.
  lead_time_median="0"
  if (( prs_merged > 0 )); then
    lead_times=$(gh api "/search/issues" \
      --method GET \
      -f q="repo:${full_repo} is:pr is:merged merged:${TARGET_DATE}" \
      --jq '.items[].pull_request.url' 2>/dev/null | while read -r pr_url; do
        # pr_url is the full API URL for the PR
        gh api "$pr_url" --jq '
          (((.merged_at | fromdateiso8601) - (.created_at | fromdateiso8601)) / 3600)
          | . * 10 | round / 10
        ' 2>/dev/null
      done)

    if [[ -n "$lead_times" ]]; then
      lead_time_median=$(echo "$lead_times" | median)
    fi
  fi

  append_row "$TARGET_DATE" "$repo" "$prs_opened" "$prs_merged" "$prs_closed" \
    "$issues_opened" "$issues_closed" "$releases" "$lead_time_median"
done

copy_csv_to_docs
echo "Done. Metrics for ${TARGET_DATE} written to ${DATA_FILE}."
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/collect.sh`

- [ ] **Step 3: Verify script parses cleanly**

Run: `bash -n scripts/collect.sh`
Expected: no output, exit 0

- [ ] **Step 4: Test against live API for yesterday**

Run: `cd /home/rbean/code/metrics && ./scripts/collect.sh`
Expected: prints repo names, writes rows to `data/metrics.csv`, copies to `docs/metrics.csv`

- [ ] **Step 5: Verify CSV output looks correct**

Run: `head -5 data/metrics.csv`
Expected: header row followed by one row per repo with numeric values

- [ ] **Step 6: Commit**

```bash
git add scripts/collect.sh data/metrics.csv docs/metrics.csv
git commit -m "feat: add daily collection script"
```

---

### Task 3: Backfill Script (`scripts/backfill.sh`)

**Files:**
- Create: `scripts/backfill.sh`

The backfill script iterates day-by-day from a start date to today, calling `collect.sh` for each day. It checkpoints progress by checking which dates already exist in the CSV, so it can resume after interruption.

- [ ] **Step 1: Create `scripts/backfill.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

START_DATE="${1:-2025-04-01}"
END_DATE="${2:-$(date -d yesterday +%Y-%m-%d)}"

echo "Backfilling metrics from ${START_DATE} to ${END_DATE}..."

ensure_csv

current="$START_DATE"
while [[ "$current" < "$END_DATE" || "$current" == "$END_DATE" ]]; do
  # Skip dates already in the CSV (checkpoint/resume support).
  if grep -q "^${current}," "$DATA_FILE" 2>/dev/null; then
    echo "Skipping ${current} (already collected)"
    current=$(date -d "${current} + 1 day" +%Y-%m-%d)
    continue
  fi

  "${SCRIPT_DIR}/collect.sh" "$current"

  # Rate limit: GitHub search API allows 30 req/min for authenticated users.
  # Each repo needs ~6 search queries + N PR detail queries.
  # Sleep between days to stay under limits.
  echo "Sleeping 60s for rate limits..."
  sleep 60

  current=$(date -d "${current} + 1 day" +%Y-%m-%d)
done

echo "Backfill complete."
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/backfill.sh`

- [ ] **Step 3: Verify script parses cleanly**

Run: `bash -n scripts/backfill.sh`
Expected: no output, exit 0

- [ ] **Step 4: Test with a small date range (2 days)**

Run: `cd /home/rbean/code/metrics && ./scripts/backfill.sh 2025-04-01 2025-04-02`
Expected: collects metrics for April 1 and April 2, appends to CSV

- [ ] **Step 5: Test resume behavior — run the same range again**

Run: `./scripts/backfill.sh 2025-04-01 2025-04-02`
Expected: prints "Skipping" for both dates since they're already in the CSV

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill.sh
git commit -m "feat: add backfill script with checkpoint/resume support"
```

---

### Task 4: GitHub Action Workflow

**Files:**
- Create: `.github/workflows/collect.yml`

- [ ] **Step 1: Create `.github/workflows/collect.yml`**

```yaml
name: Collect metrics

on:
  schedule:
    - cron: '0 6 * * *'
  workflow_dispatch:
    inputs:
      date:
        description: 'Date to collect (YYYY-MM-DD, default: yesterday)'
        required: false

jobs:
  collect:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4

      - name: Collect metrics
        run: |
          if [[ -n "${{ github.event.inputs.date }}" ]]; then
            ./scripts/collect.sh "${{ github.event.inputs.date }}"
          else
            ./scripts/collect.sh
          fi
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Commit and push
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/ docs/
          git diff --staged --quiet || git commit -m "metrics: $(date -d yesterday +%Y-%m-%d)"
          git push
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/collect.yml
git commit -m "ci: add daily metrics collection workflow"
```

---

### Task 5: Dashboard HTML Structure (`docs/index.html`, `docs/style.css`)

**Files:**
- Create: `docs/index.html`
- Create: `docs/style.css`

- [ ] **Step 1: Create `docs/style.css`**

```css
:root {
  --bg: #ffffff;
  --fg: #1a1a2e;
  --card-bg: #f5f5f7;
  --border: #e0e0e0;
  --muted: #6b7280;
  --accent: #2563eb;
  --positive: #16a34a;
  --negative: #dc2626;
  --chart-1: #2563eb;
  --chart-2: #7c3aed;
  --chart-3: #059669;
  --chart-4: #d97706;
  --chart-5: #dc2626;
  --dora-elite: #16a34a;
  --dora-high: #2563eb;
  --dora-medium: #d97706;
  --dora-low: #dc2626;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f172a;
    --fg: #e2e8f0;
    --card-bg: #1e293b;
    --border: #334155;
    --muted: #94a3b8;
    --accent: #60a5fa;
    --positive: #4ade80;
    --negative: #f87171;
    --chart-1: #60a5fa;
    --chart-2: #a78bfa;
    --chart-3: #34d399;
    --chart-4: #fbbf24;
    --chart-5: #f87171;
  }
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--fg);
  line-height: 1.6;
  padding: 2rem;
  max-width: 1200px;
  margin: 0 auto;
}

h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
.subtitle { color: var(--muted); font-size: 0.875rem; margin-bottom: 1.5rem; }

.controls {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1.5rem;
  flex-wrap: wrap;
  align-items: center;
}

.controls button {
  padding: 0.375rem 0.75rem;
  border: 1px solid var(--border);
  background: var(--card-bg);
  color: var(--fg);
  border-radius: 0.375rem;
  cursor: pointer;
  font-size: 0.8125rem;
}

.controls button.active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

.controls select {
  padding: 0.375rem 0.75rem;
  border: 1px solid var(--border);
  background: var(--card-bg);
  color: var(--fg);
  border-radius: 0.375rem;
  font-size: 0.8125rem;
}

.summary-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  margin-bottom: 2rem;
}

.card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 1rem;
}

.card .label { font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.card .value { font-size: 1.5rem; font-weight: 700; margin: 0.25rem 0; }
.card .delta { font-size: 0.8125rem; }
.card .delta.positive { color: var(--positive); }
.card .delta.negative { color: var(--negative); }

.chart-section {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 1.25rem;
  margin-bottom: 1.5rem;
}

.chart-section h2 { font-size: 1rem; margin-bottom: 1rem; }

.chart-section svg { width: 100%; overflow: visible; }

.axis text { fill: var(--muted); font-size: 0.6875rem; }
.axis path, .axis line { stroke: var(--border); }
.grid line { stroke: var(--border); stroke-opacity: 0.3; }

.dora-line { stroke-dasharray: 6 4; stroke-width: 1; opacity: 0.7; }
.dora-label { font-size: 0.625rem; font-weight: 600; }

.tooltip {
  position: absolute;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  padding: 0.5rem 0.75rem;
  font-size: 0.75rem;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.15s;
  z-index: 10;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}

th, td {
  text-align: left;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--border);
}

th {
  cursor: pointer;
  user-select: none;
  color: var(--muted);
  font-weight: 600;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

th:hover { color: var(--fg); }

tr:hover td { background: var(--bg); }
```

- [ ] **Step 2: Create `docs/index.html` with page structure and D3 loaded**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>fullsend-ai metrics</title>
  <link rel="stylesheet" href="style.css">
  <script src="https://d3js.org/d3.v7.min.js"></script>
</head>
<body>
  <h1>fullsend-ai metrics</h1>
  <p class="subtitle">SDLC performance across all repos, updated daily</p>

  <div class="controls">
    <button class="range-btn active" data-days="30">30d</button>
    <button class="range-btn" data-days="90">90d</button>
    <button class="range-btn" data-days="180">180d</button>
    <button class="range-btn" data-days="0">All</button>
    <select id="repo-filter">
      <option value="__all__">All repos</option>
    </select>
  </div>

  <div class="summary-cards" id="summary-cards"></div>

  <div class="chart-section">
    <h2>Merge &amp; Release Frequency</h2>
    <div id="chart-frequency"></div>
  </div>

  <div class="chart-section">
    <h2>PR Lead Time (median hours)</h2>
    <div id="chart-leadtime"></div>
  </div>

  <div class="chart-section">
    <h2>PR Volume</h2>
    <div id="chart-pr-volume"></div>
  </div>

  <div class="chart-section">
    <h2>Issue Volume</h2>
    <div id="chart-issue-volume"></div>
  </div>

  <div class="chart-section">
    <h2>Per-Repo Breakdown (selected period)</h2>
    <table id="repo-table">
      <thead>
        <tr>
          <th data-col="repo">Repo</th>
          <th data-col="prs_merged">PRs Merged</th>
          <th data-col="prs_opened">PRs Opened</th>
          <th data-col="issues_opened">Issues Opened</th>
          <th data-col="issues_closed">Issues Closed</th>
          <th data-col="releases">Releases</th>
          <th data-col="pr_lead_time_median_hours">Lead Time (h)</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  </div>

  <div class="tooltip" id="tooltip"></div>

  <script src="dashboard.js"></script>
</body>
</html>
```

- [ ] **Step 3: Verify HTML is well-formed**

Open `docs/index.html` in a browser or run: `python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('docs/index.html').read()); print('OK')"`
Expected: OK (no parse errors)

- [ ] **Step 4: Commit**

```bash
git add docs/index.html docs/style.css
git commit -m "feat: add dashboard HTML structure and CSS"
```

---

### Task 6: Dashboard D3.js — Data Loading and Summary Cards

**Files:**
- Create: `docs/dashboard.js`

- [ ] **Step 1: Create `docs/dashboard.js` with data loading, filtering, and summary cards**

```javascript
(async function () {
  "use strict";

  // --- State ---
  let allData = [];
  let rangeDays = 30;
  let selectedRepo = "__all__";

  // --- Load CSV ---
  const raw = await d3.csv("metrics.csv", d => ({
    date: d.date,
    repo: d.repo,
    prs_opened: +d.prs_opened,
    prs_merged: +d.prs_merged,
    prs_closed: +d.prs_closed,
    issues_opened: +d.issues_opened,
    issues_closed: +d.issues_closed,
    releases: +d.releases,
    pr_lead_time_median_hours: +d.pr_lead_time_median_hours,
  }));
  allData = raw;

  // --- Populate repo filter ---
  const repos = [...new Set(raw.map(d => d.repo))].sort();
  const repoSelect = d3.select("#repo-filter");
  repos.forEach(r => repoSelect.append("option").attr("value", r).text(r));

  // --- Controls ---
  d3.selectAll(".range-btn").on("click", function () {
    d3.selectAll(".range-btn").classed("active", false);
    d3.select(this).classed("active", true);
    rangeDays = +this.dataset.days;
    render();
  });
  repoSelect.on("change", function () {
    selectedRepo = this.value;
    render();
  });

  // --- Helpers ---
  function filterData() {
    let data = allData;
    if (selectedRepo !== "__all__") {
      data = data.filter(d => d.repo === selectedRepo);
    }
    if (rangeDays > 0) {
      const cutoff = d3.timeDay.offset(new Date(), -rangeDays);
      const cutoffStr = d3.timeFormat("%Y-%m-%d")(cutoff);
      data = data.filter(d => d.date >= cutoffStr);
    }
    return data;
  }

  // Aggregate rows by date (sum across repos).
  function aggregateByDate(data) {
    const byDate = d3.rollup(data, rows => ({
      prs_opened: d3.sum(rows, d => d.prs_opened),
      prs_merged: d3.sum(rows, d => d.prs_merged),
      prs_closed: d3.sum(rows, d => d.prs_closed),
      issues_opened: d3.sum(rows, d => d.issues_opened),
      issues_closed: d3.sum(rows, d => d.issues_closed),
      releases: d3.sum(rows, d => d.releases),
      pr_lead_time_median_hours: d3.median(rows.filter(r => r.pr_lead_time_median_hours > 0), d => d.pr_lead_time_median_hours) || 0,
    }), d => d.date);

    return Array.from(byDate, ([date, vals]) => ({ date, ...vals }))
      .sort((a, b) => a.date.localeCompare(b.date));
  }

  // --- Summary Cards ---
  function renderSummaryCards(daily) {
    const container = d3.select("#summary-cards");
    container.html("");

    if (daily.length === 0) return;

    // Split into current week vs previous week.
    const today = new Date();
    const weekAgo = d3.timeDay.offset(today, -7);
    const twoWeeksAgo = d3.timeDay.offset(today, -14);
    const weekAgoStr = d3.timeFormat("%Y-%m-%d")(weekAgo);
    const twoWeeksAgoStr = d3.timeFormat("%Y-%m-%d")(twoWeeksAgo);

    const thisWeek = daily.filter(d => d.date >= weekAgoStr);
    const lastWeek = daily.filter(d => d.date >= twoWeeksAgoStr && d.date < weekAgoStr);

    const metrics = [
      { label: "PRs Merged", key: "prs_merged", agg: d3.sum },
      { label: "PRs Opened", key: "prs_opened", agg: d3.sum },
      { label: "Lead Time (h)", key: "pr_lead_time_median_hours", agg: arr => d3.median(arr) || 0, invert: true },
      { label: "Issues Opened", key: "issues_opened", agg: d3.sum },
      { label: "Issues Closed", key: "issues_closed", agg: d3.sum },
      { label: "Releases", key: "releases", agg: d3.sum },
    ];

    metrics.forEach(m => {
      const curr = m.agg(thisWeek.map(d => d[m.key]));
      const prev = m.agg(lastWeek.map(d => d[m.key]));
      const delta = prev > 0 ? ((curr - prev) / prev * 100) : 0;
      // For lead time, lower is better (invert the arrow).
      const isPositive = m.invert ? delta <= 0 : delta >= 0;

      const card = container.append("div").attr("class", "card");
      card.append("div").attr("class", "label").text(m.label);
      card.append("div").attr("class", "value").text(
        m.key === "pr_lead_time_median_hours" ? curr.toFixed(1) : curr
      );
      if (prev > 0) {
        card.append("div")
          .attr("class", `delta ${isPositive ? "positive" : "negative"}`)
          .text(`${delta >= 0 ? "+" : ""}${delta.toFixed(0)}% vs prev week`);
      }
    });
  }

  // --- Render all ---
  function render() {
    const data = filterData();
    const daily = aggregateByDate(data);
    renderSummaryCards(daily);
    renderFrequencyChart(daily);
    renderLeadTimeChart(daily);
    renderPRVolumeChart(daily);
    renderIssueVolumeChart(daily);
    renderRepoTable(data);
  }

  // Chart functions are defined in subsequent steps.
  // Placeholder stubs so render() doesn't error during incremental development.
  window.renderFrequencyChart = window.renderFrequencyChart || function () {};
  window.renderLeadTimeChart = window.renderLeadTimeChart || function () {};
  window.renderPRVolumeChart = window.renderPRVolumeChart || function () {};
  window.renderIssueVolumeChart = window.renderIssueVolumeChart || function () {};
  window.renderRepoTable = window.renderRepoTable || function () {};

  render();
})();
```

- [ ] **Step 2: Create a sample `docs/metrics.csv` for testing**

Create a small test CSV to verify the dashboard renders before real data exists:

```csv
date,repo,prs_opened,prs_merged,prs_closed,issues_opened,issues_closed,releases,pr_lead_time_median_hours
2025-04-28,fullsend,5,3,1,4,2,0,6.2
2025-04-28,experiments,2,1,0,1,0,0,3.1
2025-04-29,fullsend,4,4,0,3,3,1,4.8
2025-04-29,experiments,1,1,1,2,1,0,2.5
2025-04-30,fullsend,6,5,1,5,4,0,5.1
2025-04-30,experiments,3,2,0,0,0,0,1.9
2025-05-01,fullsend,3,2,0,2,1,0,7.3
2025-05-01,experiments,1,1,0,1,1,1,4.0
2025-05-02,fullsend,7,6,1,6,5,0,3.5
2025-05-02,experiments,2,2,0,1,0,0,2.2
2025-05-03,fullsend,4,3,0,3,2,0,5.8
2025-05-03,experiments,1,0,0,0,0,0,0
2025-05-04,fullsend,2,1,0,1,1,0,8.1
2025-05-04,experiments,0,0,0,0,0,0,0
2025-05-05,fullsend,5,4,1,4,3,1,4.2
2025-05-05,experiments,2,2,0,2,1,0,3.0
2025-05-06,fullsend,6,5,0,5,4,0,3.9
2025-05-06,experiments,3,3,1,1,1,0,2.7
2025-05-07,fullsend,4,3,0,3,2,0,5.5
2025-05-07,experiments,1,1,0,0,0,0,1.8
```

- [ ] **Step 3: Test in browser**

Run: `cd /home/rbean/code/metrics/docs && python3 -m http.server 8080`
Open: `http://localhost:8080`
Expected: page loads, summary cards show values, range buttons work, repo filter populates. Charts are empty (stubs).

- [ ] **Step 4: Commit**

```bash
git add docs/dashboard.js docs/metrics.csv
git commit -m "feat: add D3 data loading, filtering, and summary cards"
```

---

### Task 7: Dashboard D3.js — Charts

**Files:**
- Modify: `docs/dashboard.js`

This task replaces the stub chart functions with real D3 visualizations. Each chart follows the same pattern: create SVG, set up scales, draw axes, draw data, add DORA reference lines where applicable.

- [ ] **Step 1: Add shared chart helper at the top of `dashboard.js` (after the `"use strict"` line)**

Insert this block right after `"use strict";`:

```javascript
  // --- Chart helpers ---
  const margin = { top: 20, right: 30, bottom: 30, left: 50 };
  const tooltip = d3.select("#tooltip");

  function chartDimensions(container) {
    const width = container.node().getBoundingClientRect().width;
    const height = 260;
    return {
      width, height,
      innerW: width - margin.left - margin.right,
      innerH: height - margin.top - margin.bottom,
    };
  }

  function createSvg(container, dims) {
    container.select("svg").remove();
    return container.append("svg")
      .attr("viewBox", `0 0 ${dims.width} ${dims.height}`)
      .append("g")
      .attr("transform", `translate(${margin.left},${margin.top})`);
  }

  function xTimeScale(daily, innerW) {
    return d3.scaleTime()
      .domain(d3.extent(daily, d => new Date(d.date)))
      .range([0, innerW]);
  }

  function drawXAxis(g, scale, innerH) {
    g.append("g")
      .attr("class", "axis")
      .attr("transform", `translate(0,${innerH})`)
      .call(d3.axisBottom(scale).ticks(6).tickFormat(d3.timeFormat("%b %d")));
  }

  function drawYAxis(g, scale) {
    g.append("g")
      .attr("class", "axis")
      .call(d3.axisLeft(scale).ticks(5));
  }

  function drawGrid(g, scale, innerW) {
    g.append("g")
      .attr("class", "grid")
      .call(d3.axisLeft(scale).ticks(5).tickSize(-innerW).tickFormat(""));
  }

  function showTooltip(event, html) {
    tooltip.html(html)
      .style("left", (event.pageX + 12) + "px")
      .style("top", (event.pageY - 12) + "px")
      .style("opacity", 1);
  }

  function hideTooltip() {
    tooltip.style("opacity", 0);
  }

  function doraLine(g, y, value, label, color, innerW) {
    if (value > y.domain()[1]) return; // off-chart
    g.append("line")
      .attr("class", "dora-line")
      .attr("x1", 0).attr("x2", innerW)
      .attr("y1", y(value)).attr("y2", y(value))
      .attr("stroke", color);
    g.append("text")
      .attr("class", "dora-label")
      .attr("x", innerW - 4).attr("y", y(value) - 4)
      .attr("text-anchor", "end")
      .attr("fill", color)
      .text(label);
  }
```

- [ ] **Step 2: Replace the `renderFrequencyChart` stub**

Replace `window.renderFrequencyChart = window.renderFrequencyChart || function () {};` with:

```javascript
  function renderFrequencyChart(daily) {
    const container = d3.select("#chart-frequency");
    if (daily.length === 0) { container.html("<p>No data</p>"); return; }
    const dims = chartDimensions(container);
    const g = createSvg(container, dims);
    const x = xTimeScale(daily, dims.innerW);
    const maxMerges = d3.max(daily, d => d.prs_merged) || 1;
    const y = d3.scaleLinear().domain([0, maxMerges * 1.15]).range([dims.innerH, 0]);

    drawGrid(g, y, dims.innerW);
    drawXAxis(g, x, dims.innerH);
    drawYAxis(g, y);

    // Merges line
    g.append("path")
      .datum(daily)
      .attr("fill", "none")
      .attr("stroke", "var(--chart-1)")
      .attr("stroke-width", 2)
      .attr("d", d3.line()
        .x(d => x(new Date(d.date)))
        .y(d => y(d.prs_merged))
        .curve(d3.curveMonotoneX));

    // Releases dots
    const withReleases = daily.filter(d => d.releases > 0);
    g.selectAll(".release-dot")
      .data(withReleases)
      .join("circle")
      .attr("class", "release-dot")
      .attr("cx", d => x(new Date(d.date)))
      .attr("cy", d => y(d.prs_merged))
      .attr("r", 5)
      .attr("fill", "var(--chart-2)")
      .attr("stroke", "var(--bg)")
      .attr("stroke-width", 2);

    // Hover dots
    g.selectAll(".merge-dot")
      .data(daily)
      .join("circle")
      .attr("cx", d => x(new Date(d.date)))
      .attr("cy", d => y(d.prs_merged))
      .attr("r", 3)
      .attr("fill", "var(--chart-1)")
      .attr("opacity", 0)
      .on("mouseover", function (event, d) {
        d3.select(this).attr("opacity", 1).attr("r", 5);
        showTooltip(event, `<strong>${d.date}</strong><br>Merges: ${d.prs_merged}<br>Releases: ${d.releases}`);
      })
      .on("mouseout", function () {
        d3.select(this).attr("opacity", 0).attr("r", 3);
        hideTooltip();
      });

    // Legend
    const legend = g.append("g").attr("transform", `translate(0, -8)`);
    legend.append("line").attr("x1", 0).attr("x2", 16).attr("y1", 0).attr("y2", 0).attr("stroke", "var(--chart-1)").attr("stroke-width", 2);
    legend.append("text").attr("x", 20).attr("y", 4).attr("font-size", "0.6875rem").attr("fill", "var(--muted)").text("Merges/day");
    legend.append("circle").attr("cx", 100).attr("cy", 0).attr("r", 4).attr("fill", "var(--chart-2)");
    legend.append("text").attr("x", 108).attr("y", 4).attr("font-size", "0.6875rem").attr("fill", "var(--muted)").text("Release");
  }
```

- [ ] **Step 3: Replace the `renderLeadTimeChart` stub**

Replace `window.renderLeadTimeChart = window.renderLeadTimeChart || function () {};` with:

```javascript
  function renderLeadTimeChart(daily) {
    const container = d3.select("#chart-leadtime");
    const withData = daily.filter(d => d.pr_lead_time_median_hours > 0);
    if (withData.length === 0) { container.html("<p>No data</p>"); return; }
    const dims = chartDimensions(container);
    const g = createSvg(container, dims);
    const x = xTimeScale(withData, dims.innerW);
    const maxH = d3.max(withData, d => d.pr_lead_time_median_hours) || 1;
    const y = d3.scaleLinear().domain([0, Math.max(maxH * 1.15, 48)]).range([dims.innerH, 0]);

    drawGrid(g, y, dims.innerW);
    drawXAxis(g, x, dims.innerH);
    drawYAxis(g, y);

    // DORA reference lines
    doraLine(g, y, 1, "Elite (<1h)", "var(--dora-elite)", dims.innerW);
    doraLine(g, y, 24, "High (<1d)", "var(--dora-high)", dims.innerW);
    doraLine(g, y, 168, "Medium (<1w)", "var(--dora-medium)", dims.innerW);

    // Area
    g.append("path")
      .datum(withData)
      .attr("fill", "var(--chart-3)")
      .attr("fill-opacity", 0.15)
      .attr("d", d3.area()
        .x(d => x(new Date(d.date)))
        .y0(dims.innerH)
        .y1(d => y(d.pr_lead_time_median_hours))
        .curve(d3.curveMonotoneX));

    // Line
    g.append("path")
      .datum(withData)
      .attr("fill", "none")
      .attr("stroke", "var(--chart-3)")
      .attr("stroke-width", 2)
      .attr("d", d3.line()
        .x(d => x(new Date(d.date)))
        .y(d => y(d.pr_lead_time_median_hours))
        .curve(d3.curveMonotoneX));

    // Hover dots
    g.selectAll(".lt-dot")
      .data(withData)
      .join("circle")
      .attr("cx", d => x(new Date(d.date)))
      .attr("cy", d => y(d.pr_lead_time_median_hours))
      .attr("r", 3)
      .attr("fill", "var(--chart-3)")
      .attr("opacity", 0)
      .on("mouseover", function (event, d) {
        d3.select(this).attr("opacity", 1).attr("r", 5);
        showTooltip(event, `<strong>${d.date}</strong><br>Median lead time: ${d.pr_lead_time_median_hours.toFixed(1)}h`);
      })
      .on("mouseout", function () {
        d3.select(this).attr("opacity", 0).attr("r", 3);
        hideTooltip();
      });
  }
```

- [ ] **Step 4: Replace the `renderPRVolumeChart` stub**

Replace `window.renderPRVolumeChart = window.renderPRVolumeChart || function () {};` with:

```javascript
  function renderPRVolumeChart(daily) {
    const container = d3.select("#chart-pr-volume");
    if (daily.length === 0) { container.html("<p>No data</p>"); return; }
    const dims = chartDimensions(container);
    const g = createSvg(container, dims);
    const x = xTimeScale(daily, dims.innerW);
    const maxPR = d3.max(daily, d => d.prs_opened + d.prs_merged + d.prs_closed) || 1;
    const y = d3.scaleLinear().domain([0, maxPR * 1.15]).range([dims.innerH, 0]);

    drawGrid(g, y, dims.innerW);
    drawXAxis(g, x, dims.innerH);
    drawYAxis(g, y);

    const keys = ["prs_opened", "prs_merged", "prs_closed"];
    const colors = ["var(--chart-4)", "var(--chart-1)", "var(--chart-5)"];
    const labels = ["Opened", "Merged", "Closed"];

    keys.forEach((key, i) => {
      g.append("path")
        .datum(daily)
        .attr("fill", "none")
        .attr("stroke", colors[i])
        .attr("stroke-width", 2)
        .attr("d", d3.line()
          .x(d => x(new Date(d.date)))
          .y(d => y(d[key]))
          .curve(d3.curveMonotoneX));
    });

    // Hover dots for all three
    keys.forEach((key, i) => {
      g.selectAll(`.pr-dot-${i}`)
        .data(daily)
        .join("circle")
        .attr("cx", d => x(new Date(d.date)))
        .attr("cy", d => y(d[key]))
        .attr("r", 3)
        .attr("fill", colors[i])
        .attr("opacity", 0)
        .on("mouseover", function (event, d) {
          d3.select(this).attr("opacity", 1).attr("r", 5);
          showTooltip(event, `<strong>${d.date}</strong><br>Opened: ${d.prs_opened}<br>Merged: ${d.prs_merged}<br>Closed: ${d.prs_closed}`);
        })
        .on("mouseout", function () {
          d3.select(this).attr("opacity", 0).attr("r", 3);
          hideTooltip();
        });
    });

    // Legend
    const legend = g.append("g").attr("transform", `translate(0, -8)`);
    keys.forEach((_, i) => {
      const offset = i * 80;
      legend.append("line").attr("x1", offset).attr("x2", offset + 16).attr("y1", 0).attr("y2", 0).attr("stroke", colors[i]).attr("stroke-width", 2);
      legend.append("text").attr("x", offset + 20).attr("y", 4).attr("font-size", "0.6875rem").attr("fill", "var(--muted)").text(labels[i]);
    });
  }
```

- [ ] **Step 5: Replace the `renderIssueVolumeChart` stub**

Replace `window.renderIssueVolumeChart = window.renderIssueVolumeChart || function () {};` with:

```javascript
  function renderIssueVolumeChart(daily) {
    const container = d3.select("#chart-issue-volume");
    if (daily.length === 0) { container.html("<p>No data</p>"); return; }
    const dims = chartDimensions(container);
    const g = createSvg(container, dims);
    const x = xTimeScale(daily, dims.innerW);
    const maxIss = d3.max(daily, d => Math.max(d.issues_opened, d.issues_closed)) || 1;
    const y = d3.scaleLinear().domain([0, maxIss * 1.15]).range([dims.innerH, 0]);

    drawGrid(g, y, dims.innerW);
    drawXAxis(g, x, dims.innerH);
    drawYAxis(g, y);

    const keys = ["issues_opened", "issues_closed"];
    const colors = ["var(--chart-4)", "var(--chart-3)"];
    const labels = ["Opened", "Closed"];

    keys.forEach((key, i) => {
      // Area
      g.append("path")
        .datum(daily)
        .attr("fill", colors[i])
        .attr("fill-opacity", 0.1)
        .attr("d", d3.area()
          .x(d => x(new Date(d.date)))
          .y0(dims.innerH)
          .y1(d => y(d[key]))
          .curve(d3.curveMonotoneX));

      // Line
      g.append("path")
        .datum(daily)
        .attr("fill", "none")
        .attr("stroke", colors[i])
        .attr("stroke-width", 2)
        .attr("d", d3.line()
          .x(d => x(new Date(d.date)))
          .y(d => y(d[key]))
          .curve(d3.curveMonotoneX));
    });

    // Hover dots
    keys.forEach((key, i) => {
      g.selectAll(`.iss-dot-${i}`)
        .data(daily)
        .join("circle")
        .attr("cx", d => x(new Date(d.date)))
        .attr("cy", d => y(d[key]))
        .attr("r", 3)
        .attr("fill", colors[i])
        .attr("opacity", 0)
        .on("mouseover", function (event, d) {
          d3.select(this).attr("opacity", 1).attr("r", 5);
          showTooltip(event, `<strong>${d.date}</strong><br>Opened: ${d.issues_opened}<br>Closed: ${d.issues_closed}`);
        })
        .on("mouseout", function () {
          d3.select(this).attr("opacity", 0).attr("r", 3);
          hideTooltip();
        });
    });

    // Legend
    const legend = g.append("g").attr("transform", `translate(0, -8)`);
    keys.forEach((_, i) => {
      const offset = i * 80;
      legend.append("line").attr("x1", offset).attr("x2", offset + 16).attr("y1", 0).attr("y2", 0).attr("stroke", colors[i]).attr("stroke-width", 2);
      legend.append("text").attr("x", offset + 20).attr("y", 4).attr("font-size", "0.6875rem").attr("fill", "var(--muted)").text(labels[i]);
    });
  }
```

- [ ] **Step 6: Replace the `renderRepoTable` stub**

Replace `window.renderRepoTable = window.renderRepoTable || function () {};` with:

```javascript
  function renderRepoTable(data) {
    const tbody = d3.select("#repo-table tbody");
    tbody.html("");

    // Aggregate by repo over the filtered period.
    const byRepo = d3.rollup(data, rows => ({
      prs_merged: d3.sum(rows, d => d.prs_merged),
      prs_opened: d3.sum(rows, d => d.prs_opened),
      issues_opened: d3.sum(rows, d => d.issues_opened),
      issues_closed: d3.sum(rows, d => d.issues_closed),
      releases: d3.sum(rows, d => d.releases),
      pr_lead_time_median_hours: d3.median(rows.filter(r => r.pr_lead_time_median_hours > 0), d => d.pr_lead_time_median_hours) || 0,
    }), d => d.repo);

    const rows = Array.from(byRepo, ([repo, vals]) => ({ repo, ...vals }))
      .sort((a, b) => b.prs_merged - a.prs_merged);

    rows.forEach(r => {
      const tr = tbody.append("tr");
      tr.append("td").text(r.repo);
      tr.append("td").text(r.prs_merged);
      tr.append("td").text(r.prs_opened);
      tr.append("td").text(r.issues_opened);
      tr.append("td").text(r.issues_closed);
      tr.append("td").text(r.releases);
      tr.append("td").text(r.pr_lead_time_median_hours.toFixed(1));
    });

    // Sortable headers
    d3.selectAll("#repo-table th").on("click", function () {
      const col = this.dataset.col;
      const sorted = [...rows].sort((a, b) => {
        if (col === "repo") return a.repo.localeCompare(b.repo);
        return b[col] - a[col];
      });
      tbody.html("");
      sorted.forEach(r => {
        const tr = tbody.append("tr");
        tr.append("td").text(r.repo);
        tr.append("td").text(r.prs_merged);
        tr.append("td").text(r.prs_opened);
        tr.append("td").text(r.issues_opened);
        tr.append("td").text(r.issues_closed);
        tr.append("td").text(r.releases);
        tr.append("td").text(r.pr_lead_time_median_hours.toFixed(1));
      });
    });
  }
```

- [ ] **Step 7: Remove the stub lines**

Delete these four lines from `dashboard.js`:

```javascript
  window.renderFrequencyChart = window.renderFrequencyChart || function () {};
  window.renderLeadTimeChart = window.renderLeadTimeChart || function () {};
  window.renderPRVolumeChart = window.renderPRVolumeChart || function () {};
  window.renderIssueVolumeChart = window.renderIssueVolumeChart || function () {};
  window.renderRepoTable = window.renderRepoTable || function () {};
```

- [ ] **Step 8: Test in browser**

Run: `cd /home/rbean/code/metrics/docs && python3 -m http.server 8080`
Open: `http://localhost:8080`
Expected: all four charts render with sample data. Summary cards show values. Per-repo table is sortable. Time range buttons filter the data. Repo dropdown filters to one repo. DORA reference lines appear on the lead time chart. Tooltips appear on hover.

- [ ] **Step 9: Commit**

```bash
git add docs/dashboard.js
git commit -m "feat: add D3 charts — frequency, lead time, PR volume, issue volume"
```

---

### Task 8: Enable GitHub Pages and Run Backfill

**Files:** None created — this is operational setup.

- [ ] **Step 1: Enable GitHub Pages on the repo**

```bash
gh api repos/fullsend-ai/metrics/pages \
  --method POST \
  --field source='{"branch":"main","path":"/docs"}' \
  --field build_type="legacy"
```

Expected: 201 Created. GitHub Pages will serve from `docs/` on `main`.

- [ ] **Step 2: Run the backfill to seed historical data**

This will take a long time due to API rate limits. Run in the background or in a tmux session:

```bash
cd /home/rbean/code/metrics && ./scripts/backfill.sh 2025-04-01
```

The script checkpoints progress, so if interrupted, re-run the same command and it will resume where it left off.

- [ ] **Step 3: After backfill completes, verify the data**

```bash
wc -l data/metrics.csv    # should be header + (repos * days) rows
head -3 data/metrics.csv   # verify format
tail -3 data/metrics.csv   # verify recent dates
```

- [ ] **Step 4: Commit and push the backfilled data**

```bash
git add data/metrics.csv docs/metrics.csv
git commit -m "data: backfill metrics from 2025-04-01"
git push
```

- [ ] **Step 5: Verify GitHub Pages is live**

Open: `https://fullsend-ai.github.io/metrics/`
Expected: the D3 dashboard loads with historical data, charts show trends from April 2025 to present.
