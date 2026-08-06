# Community Contribution Metrics — Plan & Status

## Status: backfill complete and verified (2026-08-06)

Both mistakes below are fixed (`search_issues()` helper in `scripts/lib.sh`,
used by all four search-result loops in `scripts/collect-community.sh`,
plus a `[[ "$number" =~ ^[0-9]+$ ]]` guard on every parsed row). Verified via:
- 5-day smoke test (2026-07-27..07-31) before the full run — clean, zero
  contamination, results reproduced identically in the full run below.
- Full backfill 2026-04-06..2026-08-05 (122 days) completed with **zero**
  contamination markers and zero rate-limit retries logged.
- `docs/community.csv`: 976 rows, exactly 8 repo-rows per date for all 122
  dates.
- Column sums match detail-row counts by event type exactly (190 external
  issues opened, 51 closed via merged PR, 47 closed other, 922 core PRs
  opened, 83 external review events).
- All 1,293 detail rows have a valid numeric issue/PR number in the
  `number` field (no contamination survived).

Still uncommitted — per standing instruction, only commit when asked.
Dashboard rendering against this now-clean data has not yet been
re-verified visually.

## Goal

Add community-contribution metrics to the fullsend-ai metrics dashboard,
tracking how much activity comes from outside a configured core-team list,
then backfill historical data back to 2026-04-06.

## Design (confirmed)

- `docs/community-config.json` — `coreTeam` list (8 usernames) + `ignoreBots`
  override list (for User-type accounts that are actually automation, e.g.
  `redhat-chai-bot`)
- `docs/community.csv` — per-repo/day counts: `issues_opened_external`,
  `issues_closed_via_pr_external`, `issues_closed_other_external`,
  `prs_opened_core`, `pr_reviews_external`
- `docs/community-details.csv` — per-event drill-down rows (actor, title, url)
  — **note: no per-event timestamp column**, only the aggregate date. This
  matters below (see mistake #1).
- Issue "closed via PR" vs "other" determined via GraphQL `ClosedEvent.closer`
  (merged PR vs. anything else)
- Review metric counts individual events (reviews + inline comments +
  conversation comments), excluding the PR author's own comments, dated by
  event timestamp, core-team and bot-filtered
- `scripts/collect-community.sh` (daily) + `scripts/backfill-community.sh`
  (date-range loop, mirrors `backfill-failures.sh`)
- Wired into `.github/workflows/collect.yml`
- Dashboard: two new charts (Community Issue Attribution, Community PR
  Attribution) + nav links + summary cards in `dashboard.js`/`index.html`

## Code status

All code is written:
- `docs/community-config.json`, dashboard wiring, workflow wiring — done,
  not yet re-verified against clean data (dashboard was checked against a
  single sparse day earlier; needs re-check against real backfilled data).
- `scripts/lib.sh` — added `is_core`, `is_ignored_bot`, `pr_review_events`
  (moved out of `collect-community.sh` so they're shared).
- `scripts/collect-community.sh` — daily collector. Review-count logic now
  checks `COMMUNITY_REVIEW_COUNTS_FILE` env var: if set (backfill mode), it
  looks up a precomputed count instead of re-scanning; otherwise behaves as
  before (fine for daily/production use, only 1 day at a time).
- `scripts/backfill-collect-reviews.sh` — **new**. One-time, one-pass-per-repo
  scan of every PR's review/comment history (via REST `/pulls?state=all`,
  paginated — deliberately *not* the Search API, see mistake #2 below),
  bucketed by exact date into a lookup file, with matching detail rows
  appended directly.
- `scripts/backfill-community.sh` — now runs the review scan once up front,
  then loops per-day over the (now cheap) issue/PR search calls.

**Nothing committed** — per standing instruction, only commit when asked.

## Current repo state — DO NOT TRUST THE DATA

`docs/community.csv` (969 rows) and `docs/community-details.csv` (6,739 rows)
currently contain a completed backfill run, but **the issue/PR portion of
that data is corrupted** (see mistake #2). Only the `pr_reviews_external`
column and its 80 detail rows are known-good. Everything else needs to be
wiped and regenerated once the fix below lands. Since both files are
untracked and uncommitted, this is safe to discard.

## Mistakes found today

### 1. Per-day PR review scan was O(days × PRs) — FIXED, verified

`collect-community.sh`'s original `pr_reviews_external` logic searched
`is:pr updated:>=TARGET_DATE` once per day. For a ~120-day backfill loop,
this re-scanned nearly the same huge PR set (up to ~1,900 PRs across 8
repos) on every single day — projected multiple hours and likely GraphQL
rate-limit exhaustion.

**Fix:** `backfill-collect-reviews.sh` does the review scan exactly once
per repo, across the whole date range, then `collect-community.sh` looks up
precomputed per-(date,repo) counts during backfill instead of rescanning.
Also switched PR enumeration from the Search API to REST `/pulls?state=all`
because the Search API hard-caps results at 1,000 total, and `fullsend`
alone has 1,561+ PRs — a bounded search there would have silently dropped
~35% of PRs.

Verified by cross-checking: the new scan's counts for `fullsend`/`agents` on
2026-08-04 matched an earlier ad-hoc manual test of the old logic (8 and 6
respectively). This part of the pipeline is solid.

*(Side note while validating this: the same date got double-counted in
detail rows the first time, because my earlier ad-hoc single-day test run
and the new full scan both wrote rows for that day, and — since the CSV has
no per-event timestamp — genuinely-distinct same-day events by the same
actor are indistinguishable from true duplicates by content alone.
Resolved by wiping and regenerating from a clean slate rather than trying
to surgically de-duplicate indistinguishable rows.)*

### 2. GitHub Search API secondary rate limit (30 req/min) — NOT YET FIXED, caused real data corruption

`collect-community.sh` calls `/search/issues` 3 times per repo × 8 repos =
24 calls per day iteration, back-to-back, with only a 3-second sleep
*between* days (not between individual calls). The Search API has its own
strict secondary limit — **30 requests/minute** — completely separate from
the 5,000/hour "core" limit I checked before running anything. I only
verified the core/GraphQL budget and missed this.

When rate-limited, `gh api` writes the rate-limit error JSON to normal
stdout (not just a failing exit code), and the script's
`while IFS=$'\t' read -r number ... ` loop parsed each of the ~5 lines of
that JSON body (`{`, the message line, the `documentation_url` line, the
`status` line, `}`) as if they were real tab-separated issue rows. Each one
got silently counted as a fake "external issue opened/closed" and written
to `community-details.csv`, because nothing validated that `$number` looked
like an actual issue number before trusting it. The existing
`2>/dev/null || true` pattern only suppresses a failing *exit code* — it
does nothing when the command exits 0 but the payload is garbage.

**Impact:** of 6,739 detail rows from today's run, 6,180 (92%) were this
garbage; only 559 were real. This corrupted `issues_opened_external`,
`issues_closed_via_pr_external`, `issues_closed_other_external`, and
`prs_opened_core` across nearly every repo/day. `pr_reviews_external` is
unaffected (different code path, GraphQL + REST `/pulls`, no Search API
calls).

## Plan for tomorrow

1. **Fix the root cause, not just the trigger.** Add defensive validation
   in `collect-community.sh`'s three search-result loops: before trusting a
   parsed row, check that `$number` actually looks like an issue number
   (e.g. `[[ "$number" =~ ^[0-9]+$ ]] || continue`, or fail loudly instead of
   silently continuing). This would have prevented today's corruption
   regardless of *why* the API call failed, and protects against future
   failure modes we haven't thought of yet, not just this specific rate
   limit.
2. **Fix the trigger too.** Throttle the `/search/issues` calls to stay
   under 30 req/min (e.g. a short sleep between the 3 calls per repo, or
   between repos), and treat a detected rate-limit response as a real error
   (retry with backoff using `X-RateLimit-Reset`, or fail the script) rather
   than swallowing it via `|| true`.
3. **Smoke-test before the full run.** Run the day-loop for a small window
   (3-5 days) first and grep the resulting `community-details.csv` for
   contamination markers (`documentation_url`, `"message"`, non-numeric
   `$5` field) as a pass/fail gate — this should take well under a minute,
   vs. the 15-40 minutes it takes to notice a problem in a full run.
4. **Wipe and regenerate.** Reset `docs/community.csv` and
   `docs/community-details.csv` to header-only, then re-run
   `scripts/backfill-community.sh 2026-04-06 <yesterday>` in one clean shot.
5. **Watch it differently this time.** While the full backfill runs, poll
   `community-details.csv` every few minutes for the same contamination
   markers as step 3, instead of only checking line counts / log tail. Stop
   immediately if anything shows up, rather than waiting for full
   completion.
6. Once clean: re-run the same spot checks as before (column sums, exactly
   8 rows/date, no contamination markers, cross-check a couple of known
   PRs/issues by hand) before touching git or reporting done.

## On the OODA loop problem (why this took so long to catch)

The core issue: the backfill has no cheap, fast-failing correctness signal.
Every "let's run it and see" cycle cost 15-40 minutes of wall-clock and API
budget before I could learn anything, and I was only watching for *process
liveness* (line counts, log tail) during that time, not *correctness*.
Concretely, for next time:

- **Validate data shape immediately, not just at the end.** A single
  `[[ "$number" =~ ^[0-9]+$ ]]` guard turns "silently corrupt 92% of the
  dataset" into "skip this line" or "fail loudly" — either is a much faster
  signal than discovering it after the fact by eyeballing aggregates.
- **Test on a narrow slice first.** A 3-5 day smoke test surfaces the same
  bug in under a minute instead of 15-40 minutes, at a fraction of the API
  cost. I did this for the review-scan fix but not for the search-rate-limit
  issue — should always smoke-test the *whole* pipeline end-to-end on a
  small range before committing to a full run.
- **Poll for correctness markers during long runs, not just progress.**
  I was periodically checking `wc -l` and `tail` on the log while waiting —
  cheap to also `grep` the growing output file for known bad-data
  signatures at the same time, giving an early warning without slowing
  anything down.
- **Prefer loud failure over silent continuation.** `|| true` /
  `2>/dev/null || echo "other"` patterns are convenient but actively hide
  the exact class of bug that hit us twice today. Scripts that fail fast on
  unexpected input are easier to debug in real time than ones that
  degrade gracefully into wrong answers.
