# Metric Details Page

**Date:** 2026-05-11
**Status:** Approved

## Problem

The dashboard shows aggregate metrics (rework rate, failure rate, PR/issue counts) but provides no way to inspect the individual events behind those numbers. When a metric looks surprising, there is no audit trail to verify it. Building trust in the metrics requires a full ledger: every touch, every workflow run, every PR event — not just the summaries.

## Goals

- Provide a single details page showing every event that contributed to every metric for a given date or date range
- Make the details page navigable from dashboard chart clicks and via shareable URLs
- Collect and store event-level detail for all metrics (not just rework, which already has partial detail)
- Backfill historical detail data so the audit trail is complete from day one

## Non-Goals

- Real-time or streaming event display
- Filtering by bot, workflow, or repo on the details page (sorting covers this adequately)
- Replacing the dashboard — the details page is a drill-down companion, not a replacement

## Data Model

### Existing: `docs/rework-details.csv` — Extended

Currently records only rework events. Extended to record ALL bot touches with a new `is_rework` column.

```
datetime,bot,repo,item,url,is_rework
2026-05-07T14:32:00Z,fullsend-ai-coder[bot],fullsend,714,https://github.com/fullsend-ai/fullsend/pull/714,false
2026-05-07T16:01:00Z,fullsend-ai-review[bot],fullsend,714,https://github.com/fullsend-ai/fullsend/pull/714,true
```

- `is_rework`: `true` if this touch is a rework event (bot previously touched this item), `false` if it is a first touch
- All touches are recorded, not just rework, so the full ledger is visible
- Existing consumers of rework-details.csv (the dashboard's `showReworkDetails` panel) filter on `is_rework == true` and ignore the new column — backward compatible

### New: `docs/failure-details.csv`

One row per workflow run, recording both successes and failures.

```
date,workflow,repo,run_id,status,url
2026-05-07,Code,fullsend,12345,failure,https://github.com/fullsend-ai/fullsend/actions/runs/12345
2026-05-07,Code,fullsend,12346,success,https://github.com/fullsend-ai/fullsend/actions/runs/12346
```

- `date`: the date the run completed
- `workflow`: workflow name (matches the `workflow` column in `failures.csv`)
- `repo`: repository name
- `run_id`: GitHub Actions run ID
- `status`: `success` or `failure`
- `url`: direct link to the GitHub Actions run

### New: `docs/metric-details.csv`

One row per PR or issue event.

```
date,repo,type,event,number,title,url
2026-05-07,fullsend,pr,opened,714,Add retry logic,https://github.com/fullsend-ai/fullsend/pull/714
2026-05-07,fullsend,pr,merged,710,Fix auth flow,https://github.com/fullsend-ai/fullsend/pull/710
2026-05-07,fullsend,issue,closed,634,Stale triage items,https://github.com/fullsend-ai/fullsend/issues/634
```

- `type`: `pr` or `issue`
- `event`: `opened`, `merged`, or `closed`
- A PR that is both opened and merged on the same day produces two rows
- `title`: issue/PR title for display on the details page. Must be quoted in the CSV if it contains commas (use standard CSV quoting: wrap in double quotes, escape internal double quotes by doubling them). The `append_metric_detail` helper handles this.
- `url`: direct link to the issue/PR on GitHub

## Collection Script Changes

### `scripts/lib.sh`

Add constants and helpers for the new CSVs:

```bash
FAILURE_DETAILS_FILE="docs/failure-details.csv"
FAILURE_DETAILS_HEADER="date,workflow,repo,run_id,status,url"

METRIC_DETAILS_FILE="docs/metric-details.csv"
METRIC_DETAILS_HEADER="date,repo,type,event,number,title,url"
```

Add `ensure_*` and `append_*` functions following the existing pattern.

Update `REWORK_DETAILS_HEADER` to include `is_rework`:

```bash
REWORK_DETAILS_HEADER="datetime,bot,repo,item,url,is_rework"
```

### `scripts/collect-rework.sh`

- Write ALL bot touches to `rework-details.csv`, not just rework events
- Add `is_rework` column: `true` for rework touches, `false` for first touches
- The deduped touches file already contains all touches; currently only rework items are written to the details CSV. Change to write every deduped touch with the appropriate flag.

### `scripts/backfill-rework.sh`

- Same change: write all touches with `is_rework` flag
- The single-pass scan already has the complete touch data; extend the per-date detail writing to include non-rework touches

### `scripts/collect-failures.sh`

- After computing summary rows, also emit one detail row per workflow run
- The script already queries the GitHub Actions API for runs; extend to capture `run_id` and `html_url` from each run and write to `failure-details.csv`
- Record both successful and failed runs

### `scripts/collect.sh`

- After computing summary counts, also emit one detail row per PR/issue event
- The script already queries the search/issues API for PRs and issues; extend to capture `number`, `title`, and `html_url` from each result and write to `metric-details.csv`
- Record event type (`opened`, `merged`, `closed`) based on the query that found the item

### New: `scripts/backfill-failure-details.sh`

- Iterate from START_DATE to END_DATE
- For each date, query the GitHub Actions API for completed workflow runs on that date
- Write detail rows to `failure-details.csv`
- Skip dates already present (same idempotency pattern as other backfill scripts)

### New: `scripts/backfill-metric-details.sh`

- Iterate from START_DATE to END_DATE
- For each date, query the search API for PRs opened, merged, closed and issues opened, closed
- Write detail rows to `metric-details.csv`
- Skip dates already present

## Details Page

### File: `docs/details.html`

A static HTML page using the same CSS variables and D3.js as the dashboard.

### URL Scheme

- `details.html?date=2026-05-07` — single date view
- `details.html?from=2026-05-05&to=2026-05-07` — date range view
- If no params, defaults to yesterday

### Navigation

- **From dashboard**: clicking any chart data point navigates to `details.html?date=YYYY-MM-DD`. Applies to all charts: rework rate, bot activity, failure rate, PR volume, issue volume.
- **From details page**: link back to the dashboard in the header.
- The existing rework details panel (`#rework-details-panel` and `showReworkDetails`) is replaced by this navigation.

### Page Layout

**Header:**
- Title: "Details for May 7, 2026" (or "May 5–7, 2026" for ranges)
- Two date inputs (From / To) with an Update button. Single-date mode pre-fills both with the same date. Changing either updates the URL params and re-renders.
- "← Back to Dashboard" link

**Section 1: Rework**
- Summary line: "47 items touched by 6 bots, 18 rework items (38.3%)"
- Table columns: Time, Bot, Repo, Item (link), Rework (yes/no tag)
- Rework rows highlighted with a subtle background color
- Sortable by any column; default sort: time ascending
- Collapsible section, expanded by default

**Section 2: Failures**
- Summary line: "42 workflow runs, 8 failures (19.0%)"
- Table columns: Date, Workflow, Repo, Run ID (link), Status (success/failure tag)
- Failure rows highlighted
- Sortable by any column; default sort: date ascending
- Collapsible section, expanded by default

**Section 3: PR & Issue Activity**
- Summary line: "7 PRs opened, 5 merged, 1 closed · 3 issues opened, 2 closed"
- Table columns: Date, Repo, Type, Event, Number (link), Title
- Sortable by any column; default sort: date ascending
- Collapsible section, expanded by default

### Styling

Reuses `docs/style.css` variables and existing patterns (dark theme, card styling). Extends with:

- Detail table styles (striped rows, hover highlight, sticky header)
- Tag styles for status indicators (rework yes/no, success/failure, event type)
- Highlighted rows for rework and failure events (subtle background tint)
- Collapsible section toggle (chevron + click-to-collapse)

No new CSS framework. Vanilla CSS extending the existing stylesheet.

### JavaScript

Single file: `docs/details.js`. Loads all detail CSVs with D3, parses URL params, renders the three sections. Table sorting is vanilla JS (click header → re-sort data → re-render tbody).

## Dashboard Changes

### `docs/dashboard.js`

- All chart data point click handlers changed from showing inline details to navigating: `window.location.href = 'details.html?date=' + d.date`
- Remove `showReworkDetails` function and `#rework-details-panel` rendering
- The rework details panel div can remain in HTML (harmless) or be removed

### `docs/index.html`

- Remove `#rework-details-panel` div (optional, can leave as no-op)
- No other HTML changes needed

## GitHub Action Changes

No changes to `.github/workflows/collect.yml` needed — the existing steps call `collect.sh`, `collect-rework.sh`, and `collect-failures.sh`, which will now emit detail rows alongside summaries. The existing `git add docs/` step picks up the new CSV files automatically.

## File Changes Summary

**New files:**
- `docs/details.html` — details page HTML
- `docs/details.js` — details page rendering logic
- `docs/failure-details.csv` — per-run failure data (append-only)
- `docs/metric-details.csv` — per-event PR/issue data (append-only)
- `scripts/backfill-failure-details.sh` — historical failure detail backfill
- `scripts/backfill-metric-details.sh` — historical metric detail backfill

**Modified files:**
- `scripts/lib.sh` — add new CSV constants/helpers, update rework details header
- `scripts/collect.sh` — emit metric detail rows
- `scripts/collect-rework.sh` — emit all touches (not just rework) with `is_rework` flag
- `scripts/backfill-rework.sh` — same touch recording change
- `scripts/collect-failures.sh` — emit failure detail rows
- `docs/dashboard.js` — chart clicks navigate to details page, remove inline rework panel
- `docs/style.css` — add detail table and tag styles
- `docs/rework-details.csv` — gains `is_rework` column, gains non-rework touch rows
