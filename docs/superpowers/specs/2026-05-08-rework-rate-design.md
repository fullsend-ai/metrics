# Rework Rate Metric

**Date:** 2026-05-08
**Status:** Proposed

## Problem

We have AI bot agents doing work across the fullsend-ai org (opening/closing issues, reviewing PRs, modifying project board fields). If an agent does a perfect job, it gets things right the first time and never revisits a work item. In practice, agents revisit work items — and the rate at which this happens is a useful signal of system efficiency. We currently have no visibility into this.

## Goals

- Measure rework rate per bot identity per day across all fullsend-ai repos
- Provide an aggregate (all-bots) rework rate per day
- Store raw rework event details with links back to GitHub for inspection
- Visualize rework rate and bot activity trends on the existing D3 dashboard

## Non-Goals

- Tracking human (non-bot) rework
- Distinguishing "good" rework from "bad" rework (all re-touches count equally)
- Real-time or sub-daily granularity in the summary metric

## Definitions

**Work item:** A GitHub issue or pull request, identified by `repo#number`.

**Touch:** Any timeline event on a work item performed by a bot actor. This includes comments, reviews, label changes, assignments, state changes, and project board field modifications. Bot actors are identified by the GitHub API `type: "Bot"` field on the user object.

**Rework:** A bot touches a work item on a given day that it previously touched on any earlier day. Multiple actions within 8 seconds of each other are treated as a single activity (deduplicated). Once a bot has touched an item on day N, every subsequent touch on day N+1 or later counts as a rework instance.

**Rework rate (daily, per bot):** `items_reworked / items_touched` for that bot on that day. Not reported (no row emitted) when `items_touched` is 0.

**Rework rate (daily, aggregate):** Same formula applied across all bots combined: total distinct reworked items / total distinct touched items for the day.

## Data Model

### `docs/rework-details.csv`

Raw rework events. One row per reworked item per bot per day (deduplicated to the first rework event of the day for that item+bot pair).

```
datetime,bot,repo,item,url
2026-05-07T14:32:00Z,github-actions[bot],fullsend,42,https://github.com/fullsend-ai/fullsend/issues/42#event-12345678
2026-05-07T16:01:00Z,dependabot[bot],fullsend,108,https://github.com/fullsend-ai/fullsend/pull/108#event-12345679
```

- `datetime`: full ISO 8601 timestamp of the rework event
- `bot`: the bot's GitHub login (e.g. `github-actions[bot]`)
- `repo`: repository name (not full `org/repo`)
- `item`: issue/PR number
- `url`: link to the work item, with anchor to the specific timeline event when available (best-effort; project field change events from GraphQL may lack a direct anchor)

### `docs/rework.csv`

Daily summary, derived from `rework-details.csv` plus the full touch data. One row per bot per day, plus one `__aggregate__` row per day.

```
date,bot,items_touched,items_reworked,rework_rate
2026-05-07,github-actions[bot],12,4,0.333
2026-05-07,dependabot[bot],3,1,0.333
2026-05-07,__aggregate__,15,5,0.333
```

- Days where a bot touched zero items: no row emitted (avoids zero-divisor, no misleading data points on the chart)
- `__aggregate__` row: counts are sums across all bots, rate is computed from those sums (not an average of per-bot rates)

## Collection Logic

### Script: `scripts/collect-rework.sh`

Takes a target date argument (defaults to yesterday). Follows the same patterns as `collect.sh`: uses `gh api`, `jq`, sources `lib.sh`.

**Steps:**

1. **Discover repos.** Call `list_repos` (from `lib.sh`) to get all non-archived, non-fork repos in the org.

2. **Find candidate work items.** For each repo, use the search API to find issues/PRs updated on the target date:
   ```
   GET /search/issues?q=repo:{org}/{repo}+updated:{date}
   ```

3. **Fetch timeline events.** For each candidate item, fetch its full timeline:
   ```
   GET /repos/{org}/{repo}/issues/{number}/timeline
   ```
   Filter to events where:
   - The actor has `type: "Bot"`
   - The event timestamp falls on the target date

   Apply 8-second deduplication: if multiple events by the same bot on the same item occur within 8 seconds, treat them as one activity.

4. **Fetch project board activity.** Discover the org's Projects v2 via GraphQL:
   ```graphql
   query {
     organization(login: "fullsend-ai") {
       projectsV2(first: 10) {
         nodes { id number title }
       }
     }
   }
   ```
   Then query for item field changes on the target date. Each ProjectV2 item has a `content` field that references the source issue/PR — use this to map project activity back to the work item. Attribute project field changes to the bot that made them.

5. **Build touch map.** For each bot, collect the set of distinct items it touched on the target date (union of timeline events and project activity).

6. **Determine rework.** For each item a bot touched today, scan that item's full timeline history for any touch by that same bot on a prior day (before the target date, with >8s gap from the target-date events). If found, it's a rework instance.

   This is self-contained — no external state file needed. The timeline fetch from step 3 already contains the full history.

7. **Write rework-details.csv.** For each rework instance, append a row with the datetime of the first rework event of the day for that item+bot, the bot name, repo, item number, and URL with event anchor.

8. **Write rework.csv.** For each bot that touched at least one item:
   - `items_touched`: count of distinct items touched
   - `items_reworked`: count of distinct items that were rework
   - `rework_rate`: `items_reworked / items_touched`

   Then write the `__aggregate__` row with sums across all bots.

### Script: `scripts/backfill-rework.sh`

Iterates from a start date to an end date, calling the same collection logic per day. Skips dates already present in `rework.csv`. Same pattern as the existing `backfill.sh`.

Rate limit consideration: more API-intensive than existing collection since it fetches timelines for every updated issue/PR. For a busy day with ~50 updated items, that's ~50 timeline fetches + 1 GraphQL query per project — well within hourly rate limits for a single day. Backfill will need throttling between days.

## Dashboard Changes

All changes are additive. No modifications to existing charts or data.

### New Charts

**1. Bot Rework Rate** (first) — D3 line chart:
- One line per bot, plus a thicker/distinct line for `__aggregate__`
- Y-axis: rework rate (0.0 to 1.0)
- X-axis: date
- Days with no data for a bot: gap in the line (no point plotted), not zero
- Tooltips on hover: date, bot name, items touched, items reworked, rate
- Clicking a data point expands to show links from `rework-details.csv` for that bot+date
- Respects existing controls (time range, weekend toggle, log scale)

**2. Bot Activity** (second) — D3 line chart:
- One line per bot, plus aggregate
- Y-axis: items touched (count)
- X-axis: date
- Same gap behavior, tooltips, and controls as the rework chart

### New Summary Card

**"Aggregate Rework Rate"** — shows this week's aggregate rate vs previous week, with delta indicator. Same style as existing summary cards.

## GitHub Action Changes

Extend `collect.yml` to run rework collection after existing metrics:

```yaml
- name: Collect rework metrics
  run: ./scripts/collect-rework.sh
  env:
    GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

The existing commit step already does `git add docs/` so the new CSV files are picked up automatically.

## File Changes Summary

New files:
- `scripts/collect-rework.sh` — daily rework metric collection
- `scripts/backfill-rework.sh` — historical backfill
- `docs/rework.csv` — daily rework summary (append-only)
- `docs/rework-details.csv` — raw rework events (append-only)

Modified files:
- `docs/index.html` — add chart sections for rework rate and bot activity, add summary card
- `docs/dashboard.js` — add rendering functions for new charts, load new CSV
- `.github/workflows/collect.yml` — add rework collection step
