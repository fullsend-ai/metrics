#!/usr/bin/env python3
"""PR fix likelihood: lower, middle and higher bands, and how often each later needed a fix.

Band, from what was known when the PR merged:
- size: the PR's changed lines (additions + deletions), placed in thirds of the
  same repository's PRs merged in earlier weeks;
- riskier kind of change: a feat, ci or build PR, or one with no conventional
  prefix, from the titles in docs/pr-type-details.csv. A typed refactor, chore,
  docs, test, fix or perf PR is not riskier.
lower = a small or middle size and not riskier; higher = a big size and
riskier; middle = everything else. With fewer than MIN_HISTORY earlier PRs the
band is unable_to_assess, never lower.

Outcome: a later PR fixed a medium- or high-severity defect from this PR within
30 days (docs/fix-judgments.csv, filled by collect-escaped-fixes.py and the
fix-judge agent).

Outputs, for weeks whose 30-day window has closed (the same weeks as
docs/escaped-fixes.csv):
- docs/fix-likelihood.csv: per week, repo and band: merged PRs, fixed PRs,
  unjudged or unknown candidates, fix rate, and that week's size boundaries
  (small_up_to and middle_up_to changed lines; a bigger PR is big).
- docs/fix-likelihood-check.csv: per repo, whether the bands rank the fixed PRs
  first: AUC of the bands ordered by size inside, and the share of fixed PRs
  among the riskiest 30% of PRs. Each repo has two rows: all closed weeks, and
  only PRs merged after STUDY_THROUGH. The size thirds always come from earlier
  weeks, but the rule itself (size plus kind of change) came from an offline
  study of these repos' PRs merged through STUDY_THROUGH, so only the second
  row checks the rule on PRs that are new to it. PRs whose outcome is still
  open (no confirmed fix, and an unjudged or unknown candidate) are left out; a
  PR with a confirmed fix is checked even if another candidate is open. Under
  MIN_FIXES fixed PRs a row reports too few to judge.

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
import re
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
OUTPUT_HEADER = ["week", "repo", "band", "merged_prs", "fixed_prs", "unjudged_candidates", "fix_rate",
                 "small_up_to", "middle_up_to"]
CHECK_HEADER = ["repo", "period", "checked_prs", "fixed_prs", "left_out_open", "auc", "fixed_in_riskiest_30pct"]
RISKIER = {"feat", "ci", "build"}  # title prefixes; no conventional prefix also counts
PREFIX = re.compile(r"^(\w+)(?:\([^)]+\))?!?:")
BAND = {("small", False): "lower", ("middle", False): "lower", ("small", True): "middle",
        ("middle", True): "middle", ("big", False): "middle", ("big", True): "higher"}
RANK = {"lower": 0, "middle": 1, "higher": 2}
MIN_HISTORY = 50  # earlier PRs needed before a repo's size thirds mean anything
MIN_FIXES = 20  # fixed PRs needed before the check is worth reading (as in the spike)
BATCH = 50  # PRs per GraphQL query
# The offline study behind the rule used these repos' PRs merged through this day,
# so only PRs merged after it are new to the rule.
STUDY_THROUGH = date(2026, 9, 30)
ALL_WEEKS = "all_weeks"
AFTER_STUDY = f"since_{STUDY_THROUGH + timedelta(days=1)}"


def key(pr: dict) -> str:
    return f"{pr['repo']}#{pr['number']}"


def riskier(pr: dict) -> bool:
    """feat, ci or build by title prefix, or no conventional prefix at all."""
    # pr_type folds refactor and build into "other", so read the prefix itself.
    match = PREFIX.match(pr.get("title", "").strip())
    return match is None or match.group(1).lower() in RISKIER


def periods(day: str) -> tuple[str, ...]:
    """The check rows a PR merged on this day counts in."""
    return (ALL_WEEKS, AFTER_STUDY) if date.fromisoformat(day) > STUDY_THROUGH else (ALL_WEEKS,)


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
    pr_type = _load("collect_pr_type", "collect-pr-type.py")
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


def bands(details: list[dict], sizes: dict[str, int]) -> dict[str, tuple[str, float, tuple[int, int] | None]]:
    """repo#N -> (band, size percentile among earlier PRs, size boundaries), from PRs merged in earlier weeks only.

    The boundaries are (small_up_to, middle_up_to) in changed lines for the PR's
    repo and week, or None while the repo has too little history.
    """
    out: dict[str, tuple[str, float, tuple[int, int] | None]] = {}
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
                    out[key(pr)] = ("unable_to_assess", 0.0, None)
                    continue
                third = "small" if size <= cuts[0] else "middle" if size <= cuts[1] else "big"
                # Sizes are whole lines, so rounding the boundaries down changes no band.
                out[key(pr)] = (BAND[(third, riskier(pr))], bisect.bisect_right(earlier, size) / len(earlier),
                                (math.floor(cuts[0]), math.floor(cuts[1])))
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


def share_in_riskiest(prs: list[tuple[float, bool]], fraction: float = 0.3) -> float:
    """Share of the fixed PRs that sit in the riskiest `fraction` of PRs.

    PRs with the same score at the cut-off share the places that are left, so
    the result does not depend on the order of the input (as auc() splits ties).
    """
    groups: dict[float, list[int]] = defaultdict(lambda: [0, 0])  # score -> [PRs, fixed PRs]
    for score, hit in prs:
        groups[score][0] += 1
        groups[score][1] += hit
    places = math.ceil(fraction * len(prs))
    found = 0.0
    for score in sorted(groups, reverse=True):
        count, hits = groups[score]
        taken = min(count, places)
        found += hits * taken / count
        places -= taken
        if not places:
            break
    return found / sum(hit for _, hit in prs)


def check(repo: str, period: str, prs: list[tuple[float, bool]], left_out: int) -> dict:
    fixed = sum(h for _, h in prs)
    row = {"repo": repo, "period": period, "checked_prs": len(prs), "fixed_prs": fixed, "left_out_open": left_out,
           "auc": "too_few_fixes", "fixed_in_riskiest_30pct": "too_few_fixes"}
    if fixed >= MIN_FIXES and fixed < len(prs):
        row["auc"] = f"{auc([s for s, _ in prs], [h for _, h in prs]):.3f}"
        row["fixed_in_riskiest_30pct"] = f"{share_in_riskiest(prs):.3f}"
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
    boundaries: dict[tuple[str, str], tuple[int, int]] = {}  # (week, repo) -> size boundaries
    checked: dict[tuple[str, str], list[tuple[float, bool]]] = defaultdict(list)  # by (repo, period)
    left_out: dict[tuple[str, str], int] = defaultdict(int)
    for pr in details:
        if pr["repo"] not in ef.REPOS or key(pr) not in banded:
            continue
        week = monday(pr["date"])
        if week + timedelta(days=6 + ef.WINDOW_DAYS) > as_of or (week.isoformat(), pr["repo"]) in gaps:
            continue
        band, percentile, cuts = banded[key(pr)]
        bucket = totals[(week.isoformat(), pr["repo"], band)]
        bucket[0] += 1
        bucket[1] += key(pr) in fixed
        bucket[2] += open_pairs.get(key(pr), 0)
        if cuts is None:
            continue
        boundaries[(week.isoformat(), pr["repo"])] = cuts
        for period in periods(pr["date"]):
            if open_pairs.get(key(pr)) and key(pr) not in fixed:
                left_out[(pr["repo"], period)] += 1
            else:
                checked[(pr["repo"], period)].append((RANK[band] + percentile * 0.99, key(pr) in fixed))

    rows = []
    for (week, repo, band), (merged, hit, open_count) in sorted(totals.items()):
        small_up_to, middle_up_to = boundaries.get((week, repo), ("", "")) if band != "unable_to_assess" else ("", "")
        rows.append({"week": week, "repo": repo, "band": band, "merged_prs": merged, "fixed_prs": hit,
                     "unjudged_candidates": open_count, "fix_rate": f"{hit / merged:.4f}",
                     "small_up_to": small_up_to, "middle_up_to": middle_up_to})
    ef.write(OUTPUT, OUTPUT_HEADER, rows)
    # One row per repo and period, never all repos together: a repo with many PRs would hide the others.
    ef.write(CHECK, CHECK_HEADER, [check(repo, period, checked[(repo, period)], left_out[(repo, period)])
                                   for repo in ef.REPOS for period in (ALL_WEEKS, AFTER_STUDY)])
    unsized = sum(1 for pr in details if pr["repo"] in ef.REPOS and key(pr) not in sizes)
    if unsized:
        print(f"  {unsized} PRs have no size yet and are left out", file=sys.stderr)
    print(f"  {len(totals)} weekly band rows in {OUTPUT.name}, {2 * len(ef.REPOS)} rows in {CHECK.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rebuild", action="store_true", help="Only recompute from the CSVs (no GitHub calls).")
    args = parser.parse_args()
    if not args.rebuild:
        fetch_sizes(ef.read(ef.DETAILS))
    rebuild()


if __name__ == "__main__":
    main()
