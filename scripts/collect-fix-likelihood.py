#!/usr/bin/env python3
"""PR fix likelihood: lower, middle and higher bands, and how often each later needed a fix.

Band, from what was known when the PR merged:
- size: the PR's changed lines (additions + deletions), placed in thirds of the
  same repository's PRs merged in earlier weeks;
- riskier kind of change: a feat, ci or untyped ("other") PR, from
  docs/pr-type-details.csv. A PR without a conventional type counts as riskier.
lower = a small or middle size and not riskier; higher = a big size and
riskier; middle = everything else. With fewer than MIN_HISTORY earlier PRs the
band is unable_to_assess, never lower.

Outcome: a later PR fixed a medium- or high-severity defect from this PR within
30 days (docs/fix-judgments.csv, filled by collect-escaped-fixes.py and the
fix-judge agent).

Outputs, for weeks whose 30-day window has closed (the same weeks as
docs/escaped-fixes.csv):
- docs/fix-likelihood.csv: per week, repo and band: merged PRs, fixed PRs,
  unjudged or unknown candidates, fix rate.
- docs/fix-likelihood-check.csv: per repo and for all repos, whether the bands
  rank the fixed PRs first: AUC of the bands ordered by size inside, and the
  share of fixed PRs among the riskiest 30% of PRs. Every band uses only earlier
  PRs, so this checks the rule on PRs it was not fitted on. PRs with an open
  candidate are left out. Under MIN_FIXES fixed PRs it reports too few to judge.

The bands are advisory: they show where review should look hardest, and never
approve or merge a PR.

Sizes are cached in docs/pr-size.csv and fetched over GraphQL only for PRs that
lack a row. Usage: collect-fix-likelihood.py [--rebuild]  (--rebuild: no GitHub calls)
"""
from __future__ import annotations

import argparse
import bisect
import importlib.util
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ef = _load("collect_escaped_fixes", "collect-escaped-fixes.py")

SIZES = ef.ROOT / "docs" / "pr-size.csv"
OUTPUT = ef.ROOT / "docs" / "fix-likelihood.csv"
CHECK = ef.ROOT / "docs" / "fix-likelihood-check.csv"
SIZE_HEADER = ["repo", "number", "changed_lines"]
OUTPUT_HEADER = ["week", "repo", "band", "merged_prs", "fixed_prs", "unjudged_candidates", "fix_rate"]
CHECK_HEADER = ["repo", "checked_prs", "fixed_prs", "left_out_open", "auc", "fixed_in_riskiest_30pct"]
RISKIER = {"feat", "ci", "other", ""}
BAND = {("small", False): "lower", ("middle", False): "lower", ("small", True): "middle",
        ("middle", True): "middle", ("big", False): "middle", ("big", True): "higher"}
RANK = {"lower": 0, "middle": 1, "higher": 2}
MIN_HISTORY = 50  # earlier PRs needed before a repo's size thirds mean anything
MIN_FIXES = 20  # fixed PRs needed before the check is worth reading (as in the spike)
BATCH = 50  # PRs per GraphQL query


def key(pr: dict) -> str:
    return f"{pr['repo']}#{pr['number']}"


def monday(day: str) -> date:
    merged_on = date.fromisoformat(day)
    return merged_on - timedelta(days=merged_on.weekday())


def fetch_sizes(details: list[dict]) -> None:
    """Add a docs/pr-size.csv row for every fullsend/agents PR that lacks one."""
    sizes = ef.read(SIZES)
    have = {f"{r['repo']}#{r['number']}" for r in sizes}
    missing = sorted({(pr["repo"], int(pr["number"])) for pr in details
                      if pr["repo"] in ef.REPOS and key(pr) not in have})
    if not missing:
        return
    pr_type = ef._load_pr_type()
    for start in range(0, len(missing), BATCH):
        batch = missing[start:start + BATCH]
        fields = " ".join(
            f'p{i}: repository(owner: "{ef.ORG}", name: "{repo}") {{ pullRequest(number: {number}) {{ additions deletions }} }}'
            for i, (repo, number) in enumerate(batch))
        data = pr_type.gh_json(["api", "graphql", "-f", f"query={{ {fields} }}"])["data"]
        for i, (repo, number) in enumerate(batch):
            pull = data[f"p{i}"]["pullRequest"]
            sizes.append({"repo": repo, "number": number, "changed_lines": pull["additions"] + pull["deletions"]})
        # Written after every batch, so a failure keeps what was fetched.
        ef.write(SIZES, SIZE_HEADER, sorted(sizes, key=lambda r: (r["repo"], int(r["number"]))))
    print(f"  {len(missing)} PR sizes fetched")


def bands(details: list[dict], sizes: dict[str, int]) -> dict[str, tuple[str, float]]:
    """repo#N -> (band, size percentile among earlier PRs), from PRs merged in earlier weeks only."""
    out: dict[str, tuple[str, float]] = {}
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for pr in details:
        if pr["repo"] in ef.REPOS and key(pr) in sizes:
            by_repo[pr["repo"]].append(pr)
    for prs in by_repo.values():
        by_week: dict[date, list[dict]] = defaultdict(list)
        for pr in prs:
            by_week[monday(pr["date"])].append(pr)
        earlier: list[int] = []  # sorted sizes of the repo's PRs from earlier weeks
        for week in sorted(by_week):
            cuts = statistics.quantiles(earlier, n=3, method="inclusive") if len(earlier) >= MIN_HISTORY else None
            for pr in by_week[week]:
                size = sizes[key(pr)]
                if cuts is None:
                    out[key(pr)] = ("unable_to_assess", 0.0)
                    continue
                third = "small" if size <= cuts[0] else "middle" if size <= cuts[1] else "big"
                out[key(pr)] = (BAND[(third, pr["pr_type"] in RISKIER)], bisect.bisect_right(earlier, size) / len(earlier))
            for pr in by_week[week]:
                bisect.insort(earlier, sizes[key(pr)])
    return out


def outcomes(candidates: list[dict], judgments: list[dict]) -> tuple[set[str], dict[str, int]]:
    """PRs a later PR fixed (medium or high severity) within 30 days, and open candidates per PR."""
    verdicts = {(r["fix"], r["original"]): r for r in judgments}
    fixed: set[str] = set()
    open_pairs: dict[str, int] = defaultdict(int)
    for row in candidates:
        if float(row["days_between"]) > ef.WINDOW_DAYS:
            continue
        judgment = verdicts.get((row["fix"], row["original"]), {"verdict": "unjudged", "severity": ""})
        if judgment["verdict"] == "defect_from_original" and judgment["severity"] in ("medium", "high"):
            fixed.add(row["original"])
        elif judgment["verdict"] in ("unjudged", "unknown"):
            open_pairs[row["original"]] += 1
    return fixed, open_pairs


def auc(scores: list[float], hits: list[bool]) -> float:
    """Chance that a fixed PR scores above one that was not (ties count half)."""
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    positives = sum(hits)
    negatives = len(hits) - positives
    return (sum(r for r, h in zip(ranks, hits) if h) - positives * (positives + 1) / 2) / (positives * negatives)


def check(repo: str, prs: list[tuple[float, bool]], left_out: int) -> dict:
    fixed = sum(h for _, h in prs)
    row = {"repo": repo, "checked_prs": len(prs), "fixed_prs": fixed, "left_out_open": left_out,
           "auc": "too_few_fixes", "fixed_in_riskiest_30pct": "too_few_fixes"}
    if fixed >= MIN_FIXES and fixed < len(prs):
        ranked = sorted(prs, key=lambda p: -p[0])[:math.ceil(0.3 * len(prs))]
        row["auc"] = f"{auc([s for s, _ in prs], [h for _, h in prs]):.3f}"
        row["fixed_in_riskiest_30pct"] = f"{sum(h for _, h in ranked) / fixed:.3f}"
    return row


def rebuild() -> None:
    details = ef.read(ef.DETAILS)
    aggregate = ef.read(ef.AGGREGATE)
    if not aggregate:
        raise SystemExit("pr-type.csv is empty; run collect-pr-type.sh first")
    as_of = ef.closed_through(aggregate)
    gaps = ef.gap_weeks(aggregate)
    sizes = {f"{r['repo']}#{r['number']}": int(r["changed_lines"]) for r in ef.read(SIZES)}
    banded = bands(details, sizes)
    fixed, open_pairs = outcomes(ef.read(ef.CANDIDATES), ef.read(ef.JUDGMENTS))

    totals: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    checked: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    left_out: dict[str, int] = defaultdict(int)
    for pr in details:
        if pr["repo"] not in ef.REPOS or key(pr) not in banded:
            continue
        week = monday(pr["date"])
        if week + timedelta(days=6 + ef.WINDOW_DAYS) > as_of or (week.isoformat(), pr["repo"]) in gaps:
            continue
        band, percentile = banded[key(pr)]
        bucket = totals[(week.isoformat(), pr["repo"], band)]
        bucket[0] += 1
        bucket[1] += key(pr) in fixed
        bucket[2] += open_pairs.get(key(pr), 0)
        if band == "unable_to_assess":
            continue
        for repo in (pr["repo"], "all"):
            if open_pairs.get(key(pr)) and key(pr) not in fixed:
                left_out[repo] += 1
            else:
                checked[repo].append((RANK[band] + percentile * 0.99, key(pr) in fixed))

    ef.write(OUTPUT, OUTPUT_HEADER, [
        {"week": week, "repo": repo, "band": band, "merged_prs": merged, "fixed_prs": hit,
         "unjudged_candidates": open_count, "fix_rate": f"{hit / merged:.4f}"}
        for (week, repo, band), (merged, hit, open_count) in sorted(totals.items())])
    ef.write(CHECK, CHECK_HEADER, [check(repo, checked[repo], left_out[repo])
                                   for repo in sorted(checked, key=lambda r: (r == "all", r))])
    unsized = sum(1 for pr in details if pr["repo"] in ef.REPOS and key(pr) not in sizes)
    if unsized:
        print(f"  {unsized} PRs have no size yet and are left out", file=sys.stderr)
    print(f"  {len(totals)} weekly band rows in {OUTPUT.name}, {len(checked)} rows in {CHECK.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rebuild", action="store_true", help="Only recompute from the CSVs (no GitHub calls).")
    args = parser.parse_args()
    if not args.rebuild:
        fetch_sizes(ef.read(ef.DETAILS))
    rebuild()


if __name__ == "__main__":
    main()
