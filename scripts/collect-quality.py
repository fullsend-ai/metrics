#!/usr/bin/env python3
"""Derive defect-label and Git-revert signals for fullsend and agents."""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORG = "fullsend-ai"
AGGREGATE = ROOT / "docs" / "pr-type.csv"
DETAILS = ROOT / "docs" / "pr-type-details.csv"
OUTPUT = ROOT / "docs" / "quality.csv"
REPOS = ("fullsend", "agents")
TYPES = ("feat", "fix", "docs", "ci", "chore", "test", "perf", "other")
HEADER = [
    "date", "repo", "merged_prs", "fix_prs", "defect_prs", "revert_prs",
    "revert_commits", "defect_rate", "revert_rate",
]
REVERT_TITLE = re.compile(
    r"^\s*(?:(?:[a-z][\w-]*)(?:\([^)]*\))?!?:\s*)?revert(?:\b|!|:)", re.I
)
GIT_REVERT = re.compile(r"\bthis reverts commit\b", re.I)
REVERT_TARGET = re.compile(r"^\s*Revert\s+\"([^\"]+)\"", re.I)
REVERT_SHA = re.compile(r"\bThis reverts commit\s+([0-9a-f]{7,40})\b", re.I)
DEFECT_LABELS = {"bug", "defect", "regression", "incident", "production-bug", "type: bug"}


def read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


RATE_LIMIT_MARKERS = ("rate limit", "secondary rate limit", "api rate limit")


def gh_json(args: list[str], retries: int = 5):
    """Run gh with bounded retries for GitHub rate-limit responses."""
    last_error = ""
    for attempt in range(1, retries + 1):
        result = subprocess.run(["gh", *args], capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout) if result.stdout.strip() else []
        last_error = result.stderr.strip()
        if not any(marker in last_error.lower() for marker in RATE_LIMIT_MARKERS):
            raise RuntimeError(last_error or "gh command failed")
        if attempt < retries:
            delay = attempt * 25
            print(f"  rate limited (attempt {attempt}/{retries}), sleep {delay}s", file=sys.stderr)
            time.sleep(delay)
    raise RuntimeError(f"rate limit retries exhausted: {last_error}")


def issue_is_defect(repo: str, number: str, cache: dict[tuple[str, str], bool]) -> bool:
    key = (repo, number)
    if key in cache:
        return cache[key]
    if not number:
        cache[key] = False
        return False
    try:
        issue = gh_json(["api", f"repos/{ORG}/{repo}/issues/{number}"])
    except RuntimeError as error:
        # A PR title can reference a PR number or an issue may have been
        # removed. Neither is label-confirmed defect evidence.
        if "404" in str(error) or "Not Found" in str(error):
            cache[key] = False
            return False
        raise
    labels = {str(label.get("name", "")).strip().lower() for label in issue.get("labels", [])}
    cache[key] = bool(labels & DEFECT_LABELS)
    return cache[key]


def labeled_issue_numbers(repo: str) -> set[str]:
    """Bulk-load defect-labeled issue numbers for an occasional backfill."""
    numbers: set[str] = set()
    for label in DEFECT_LABELS:
        issues = gh_json([
            "search", "issues", "--repo", f"{ORG}/{repo}",
            "--label", label, "--json", "number", "--limit", "1000",
        ])
        numbers.update(str(issue["number"]) for issue in issues)
    return numbers


def paginated_items(args: list[str]) -> list[dict]:
    """Flatten gh api --paginate --slurp output without leaking API details."""
    pages = gh_json(["api", "--paginate", "--slurp", *args])
    return [item for page in pages for item in page]


def same_pr_cleanup(repo: str, commit: dict, cache: dict[tuple[str, str, str, str], bool]) -> bool:
    """Exclude a revert that undoes a temporary commit in the same merged PR.

    GitHub can associate a revert commit with a PR even when the reverted commit
    was rebased, so compare both the target SHA and the conventional Revert
    title against commits in each merged PR containing the revert.
    """
    message = commit.get("commit", {}).get("message", "")
    target_sha_match = REVERT_SHA.search(message)
    target_title_match = REVERT_TARGET.match(message.splitlines()[0] if message else "")
    target_sha = target_sha_match.group(1).lower() if target_sha_match else ""
    target_title = target_title_match.group(1).strip() if target_title_match else ""
    if not target_sha and not target_title:
        return False

    repo_key = f"{ORG}/{repo}"
    commit_sha = commit.get("sha", "")
    pull_requests = paginated_items([f"repos/{repo_key}/commits/{commit_sha}/pulls"])
    for pull in pull_requests:
        if pull.get("state") != "closed" or not pull.get("merged_at"):
            continue
        number = str(pull["number"])
        # The result depends on the specific revert target, not only the PR.
        # A PR can contain multiple distinct revert commits.
        key = (repo, number, target_sha, target_title)
        if key not in cache:
            pr_commits = paginated_items([f"repos/{repo_key}/pulls/{number}/commits"])
            cache[key] = any(
                item.get("sha", "").lower() == target_sha
                or (
                    target_title
                    and item.get("commit", {}).get("message", "").splitlines()[0].strip() == target_title
                )
                for item in pr_commits
            )
        if cache[key]:
            return True
    return False


def git_revert_counts(start: str, end: str) -> Counter:
    counts: Counter = Counter()
    cleanup_cache: dict[tuple[str, str, str, str], bool] = {}
    for repo in REPOS:
        commits = paginated_items([
            f"repos/{ORG}/{repo}/commits?since={start}T00:00:00Z&until={end}T23:59:59Z&per_page=100",
        ])
        for commit in commits:
            message = commit.get("commit", {}).get("message", "")
            committed = commit.get("commit", {}).get("committer", {}).get("date", "")
            if GIT_REVERT.search(message) and committed and not same_pr_cleanup(repo, commit, cleanup_cache):
                counts[(committed[:10], repo)] += 1
    return counts


def rebuild(target_dates: set[str]) -> None:
    aggregate = read(AGGREGATE)
    details = read(DETAILS)
    if not aggregate:
        raise SystemExit("pr-type.csv is empty; run collect-pr-type.sh first")

    fix_rows: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    titled_reverts: Counter = Counter()
    for row in details:
        key = (row["date"], row["repo"])
        if row.get("pr_type") == "fix":
            fix_rows[key].append(row)
        if REVERT_TITLE.match(row.get("title", "")):
            titled_reverts[key] += 1

    dates = target_dates or {row["date"] for row in aggregate}
    git_reverts = git_revert_counts(min(dates), max(dates))
    labels_cache: dict[tuple[str, str], bool] = {}
    bulk_labels = {repo: labeled_issue_numbers(repo) for repo in REPOS} if len(dates) > 1 else {}
    existing = {
        (row["date"], row["repo"]): {key: row.get(key, "") for key in HEADER}
        for row in read(OUTPUT)
        if (not target_dates or row["date"] not in target_dates) and set(HEADER).issubset(row)
    }

    for row in aggregate:
        if target_dates and row["date"] not in target_dates:
            continue
        if row.get("repo") not in REPOS:
            continue
        key = (row["date"], row["repo"])
        merged = sum(int(row[name]) for name in TYPES)
        fixes = fix_rows[key]
        if bulk_labels:
            defect_prs = sum(item.get("issue_number", "") in bulk_labels[row["repo"]] for item in fixes)
        else:
            defect_prs = sum(issue_is_defect(row["repo"], item.get("issue_number", ""), labels_cache) for item in fixes)
        titled = titled_reverts[key]
        commits = git_reverts[key]
        events = max(titled, commits)
        existing[key] = {
            "date": row["date"], "repo": row["repo"], "merged_prs": str(merged),
            "fix_prs": str(len(fixes)), "defect_prs": str(defect_prs),
            "revert_prs": str(titled), "revert_commits": str(commits),
            "defect_rate": f"{defect_prs / merged:.4f}" if merged else "0.0000",
            "revert_rate": f"{events / merged:.4f}" if merged else "0.0000",
        }

    with OUTPUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(sorted(existing.values(), key=lambda row: (row["date"], row["repo"])))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Replace quality data for one YYYY-MM-DD date.")
    parser.add_argument("--from", dest="start", help="Start date for an inclusive YYYY-MM-DD range.")
    parser.add_argument("--to", dest="end", help="End date for an inclusive YYYY-MM-DD range.")
    args = parser.parse_args()
    if args.date:
        date.fromisoformat(args.date)
        rebuild({args.date})
    elif args.start:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end or args.start)
        if start > end:
            parser.error("--from must not be later than --to")
        rebuild({(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)})
    else:
        parser.error("one of --date or --from is required")


if __name__ == "__main__":
    main()
