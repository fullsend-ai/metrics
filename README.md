# fullsend-ai/metrics

SDLC metrics dashboard for the fullsend-ai organization. Tracks deployment frequency, PR lead time, PR volume, issue volume, community attribution, and **PR delivery mix** (conventional-commit type + fix-filer source) across repos. Published daily as a D3.js dashboard on GitHub Pages.

**Dashboard:** https://fullsend-ai.github.io/metrics/

## PR type / fix source metrics

Dedicated tab: **[Delivered PR Types](https://fullsend-ai.github.io/metrics/delivered-pr-types.html)** (`docs/delivered-pr-types.html`).

Daily collector for merged PRs on `fullsend` + `agents`:

| File | Purpose |
|------|---------|
| `docs/pr-type.csv` | Per day × repo counts by type (`feat`…`other`) and fix filer (`fix_core` / `fix_external` / `fix_bot` / `fix_unlinked`) |
| `docs/pr-type-details.csv` | Per-PR drill-down |
| `scripts/collect-pr-type.sh` | Daily (wired into `.github/workflows/collect.yml`) |
| `scripts/backfill-pr-type.sh` | Date-range backfill |

Core-team roster comes from `docs/community-config.json` (same list as community metrics).

```bash
# Daily
./scripts/collect-pr-type.sh            # yesterday UTC
./scripts/collect-pr-type.sh 2026-08-11

# Backfill (rewrites CSVs)
./scripts/backfill-pr-type.sh 2026-05-14 2026-08-11
```

See [docs/design.md](docs/design.md) for the full design spec.

## Quality Signals

The [Quality Signals](https://fullsend-ai.github.io/metrics/quality.html) tab
reports two GitHub PR-based proxies for `fullsend` and `agents`:

- **Defect-labeled rate:** `fix` PRs linked to an issue with a defect label divided by merged PRs.
- **Revert event rate:** PRs titled `Revert ...` or typed subjects such as `fix: revert ...`, plus Git commits containing `This reverts commit`; duplicate evidence and temporary changes reverted within the same merged PR are excluded before division by merged PRs.

These are leading indicators, not DORA Change Failure Rate. DORA requires
production deployment and rollback/hotfix evidence. The daily workflow derives
`docs/quality.csv` from the PR-type datasets and GitHub issue/commit metadata.
