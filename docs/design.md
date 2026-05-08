# fullsend-ai/metrics: Org Metrics Dashboard

**Date:** 2026-05-08
**Status:** Proposed

## Problem

We produce and merge a large volume of changes across fullsend-ai repos but have no visibility into how that volume and velocity change over time. We need a lightweight, self-hosted dashboard that tracks key SDLC metrics without SaaS dependencies.

## Goals

- Track deployment frequency, PR lead time, PR volume, and issue volume across all fullsend-ai repos
- Show trends over time with historical data back to April 1, 2025
- Publish a public, single-page D3.js dashboard via GitHub Pages
- Run autonomously on a daily cron with zero maintenance

## Non-Goals

- DORA change failure rate or mean time to recovery
- Per-contributor breakdowns
- Alerts, notifications, or Slack integration
- SaaS dependencies of any kind

## Metrics

| Metric | Definition | Source |
|--------|-----------|--------|
| Merge frequency | Merges to default branch per day | GitHub API: merged PRs with `base:main` |
| Release frequency | Tagged releases per day | GitHub API: releases endpoint |
| PR lead time | Median hours from PR open to merge | GitHub API: PR `created_at` vs `merged_at` |
| PR volume | PRs opened, merged, closed per day | GitHub API: PR search by date |
| Issue volume | Issues opened, closed per day | GitHub API: issue search by date |

All metrics are computed per repo and aggregated org-wide.

## Architecture

### Data Layer

A single append-only CSV file at `data/metrics.csv`:

```
date,repo,prs_opened,prs_merged,prs_closed,issues_opened,issues_closed,releases,pr_lead_time_median_hours
2025-04-01,fullsend,3,2,1,5,3,0,4.5
2025-04-01,experiments,1,1,0,0,0,0,1.2
```

One row per repo per day. The file grows by N rows per day where N is the number of repos in the org. At ~30 repos and 365 days, that's ~11K rows/year — trivially small.

### Collection Script (`scripts/collect.sh`)

A shell script that:

1. Discovers all repos in the org via `gh api /orgs/fullsend-ai/repos`
2. For each repo, queries the GitHub API for the previous day's metrics:
   - `GET /search/issues?q=repo:{repo}+is:pr+created:{date}` for PRs opened
   - `GET /search/issues?q=repo:{repo}+is:pr+is:merged+merged:{date}` for PRs merged
   - `GET /search/issues?q=repo:{repo}+is:pr+is:closed+closed:{date}` for PRs closed
   - `GET /search/issues?q=repo:{repo}+is:issue+created:{date}` for issues opened
   - `GET /search/issues?q=repo:{repo}+is:issue+is:closed+closed:{date}` for issues closed
   - `GET /repos/{repo}/releases` filtered by date for releases
   - PR lead time computed from merged PRs' `created_at` and `merged_at` timestamps
3. Appends rows to `data/metrics.csv`
4. Copies `data/metrics.csv` into `docs/` for D3 to load

Uses `gh api` with pagination and `jq` for JSON processing. No external dependencies beyond `gh` and `jq` (both available in GitHub Actions runners).

### Backfill Script (`scripts/backfill.sh`)

A one-time script that iterates from 2025-04-01 to today, calling the same collection logic for each day. Produces the initial `data/metrics.csv`. Respects GitHub API rate limits with small delays between days.

Rate limit considerations:
- GitHub search API allows 30 requests/minute for authenticated users
- Each repo-day requires ~6 search queries
- For 30 repos, one day = ~180 queries = ~6 minutes
- Backfilling ~400 days = ~40 hours at full serial speed
- The backfill script will checkpoint progress so it can resume if interrupted

### GitHub Action (`collect.yml`)

```yaml
name: Collect metrics
on:
  schedule:
    - cron: '0 6 * * *'  # 06:00 UTC daily
  workflow_dispatch: {}   # manual trigger

jobs:
  collect:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - name: Collect yesterday's metrics
        run: ./scripts/collect.sh
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

`GITHUB_TOKEN` scoped to the org provides read access to all public repos. If private repos need tracking, a GitHub App or PAT with broader scope would be needed.

### GitHub Pages Dashboard (`docs/index.html`)

A single HTML page served by GitHub Pages (from the `docs/` directory on `main`).

**Technology:** D3.js loaded from CDN. No build step, no bundler, no framework.

**Layout:**

1. **Summary cards** (top) — current week vs. previous week for each metric, with delta indicators showing percentage change
2. **Merge + release frequency** — D3 line chart, dual y-axes (merges left, releases right), org-wide aggregate with per-repo tooltip on hover
3. **PR lead time trend** — D3 line chart showing median hours over time
4. **PR volume** — D3 stacked area chart: opened vs. merged vs. closed
5. **Issue volume** — D3 area chart: opened vs. closed
6. **Per-repo breakdown** — HTML table below the charts, sortable by any column, showing current-week stats per repo

**Data loading:** `d3.csv("metrics.csv")` loads the CSV directly. The collection step copies the data file into `docs/` so it's served alongside the HTML.

**Styling:** Minimal CSS. Respects `prefers-color-scheme` for dark/light mode. Responsive layout using CSS grid.

**Interactivity:**
- Time range selector (last 7d / 30d / 90d / all)
- Per-repo filter (dropdown or clickable legend)
- Hover tooltips on chart data points

### DORA Tier Reference Lines

The dashboard draws horizontal reference lines on relevant charts showing where fullsend-ai falls relative to DORA tiers:

| Metric | Elite | High | Medium | Low |
|--------|-------|------|--------|-----|
| Deployment frequency | Multiple per day | Weekly-monthly | Monthly-biannually | < once per 6 months |
| Lead time for changes | < 1 hour | 1 day-1 week | 1 week-1 month | 1-6 months |

## Repo Structure

```
fullsend-ai/metrics/
├── .github/workflows/collect.yml    # daily cron Action
├── scripts/
│   ├── collect.sh                   # daily metric collection
│   └── backfill.sh                  # one-time historical seed
├── data/
│   └── metrics.csv                  # append-only data
├── docs/
│   ├── index.html                   # D3 dashboard (single page)
│   ├── style.css                    # minimal styling
│   └── metrics.csv                  # copy of data file for Pages
└── README.md
```

## Key Decisions

- **Shell + `gh` + `jq` over Python:** Keeps dependencies minimal. GitHub Actions runners have `gh` and `jq` preinstalled. No virtualenv, no pip, no runtime to manage.
- **CSV over JSON:** D3 loads CSV natively with `d3.csv()`. Human-readable in git diffs. Easy to append to. JSON would require read-modify-write.
- **Single HTML file over static site generator:** No build step. One file to maintain. D3 handles all rendering client-side.
- **`docs/` directory over `gh-pages` branch:** Simpler — no branch management, the dashboard source lives alongside the data.
- **Daily granularity over hourly:** Sufficient for trend analysis. Keeps API usage low. Avoids partial-day noise.
- **Org-wide dynamic repo discovery:** No hardcoded repo list to maintain. New repos automatically appear in the next day's collection.
- **Both merge and release frequency:** Reported as separate metrics since merge-to-main and tagged releases represent different deployment signals.
