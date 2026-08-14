#!/usr/bin/env python3
"""Collect conventional-commit PR type + fix-filer metrics for fullsend-ai.

Writes docs/pr-type.csv (daily aggregates) and docs/pr-type-details.csv
(per-PR rows). Classifies merged PR titles as feat/fix/docs/ci/chore/test/
perf/other, and for fix PRs traces the linked issue author to core /
external / bot / unlinked using docs/community-config.json.

Uses month-sized search windows during backfill to stay within the GitHub
Search API secondary rate limit (~30 req/min). Issue author lookups use the
REST core quota (5000/hr).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ORG = "fullsend-ai"
TYPES = ("feat", "fix", "docs", "ci", "chore", "test", "perf", "other")
REPO_PATTERN = re.compile(r"^(\w+)(?:\([^)]+\))?!?:")
ISSUE_TITLE = re.compile(r"^fix\(#(\d+)\)", re.I)
ISSUE_BODY_QUALIFIED = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+fullsend-ai/([\w.-]+)#(\d+)",
    re.I,
)
ISSUE_BODY_LOCAL = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)",
    re.I,
)

ROOT = Path(__file__).resolve().parent.parent
PR_TYPE_FILE = ROOT / "docs" / "pr-type.csv"
PR_TYPE_DETAILS_FILE = ROOT / "docs" / "pr-type-details.csv"
CONFIG_FILE = ROOT / "docs" / "community-config.json"

PR_TYPE_HEADER = [
    "date", "repo", "feat", "fix", "docs", "ci", "chore", "test", "perf", "other",
    "fix_core", "fix_external", "fix_bot", "fix_unlinked",
]
PR_TYPE_DETAILS_HEADER = [
    "date", "repo", "number", "title", "pr_type", "fix_source",
    "issue_number", "issue_author", "url",
]

RATE_LIMIT_MARKERS = ("rate limit", "secondary rate limit")


def load_config():
    cfg = json.loads(CONFIG_FILE.read_text())
    return set(cfg.get("coreTeam") or []), set(cfg.get("ignoreBots") or [])


def classify_title(title: str) -> str:
    m = REPO_PATTERN.match(title.strip())
    if m:
        t = m.group(1).lower()
        if t in TYPES:
            return t
    return "other"


def extract_issue_ref(pr_repo: str, title: str, body: str | None) -> tuple[str, int] | None:
    """Return (issue_repo, issue_number) for a fix PR linkage."""
    m = ISSUE_TITLE.match(title.strip())
    if m:
        return (pr_repo, int(m.group(1)))
    text = body or ""
    for m in ISSUE_BODY_QUALIFIED.finditer(text):
        return (m.group(1), int(m.group(2)))
    for m in ISSUE_BODY_LOCAL.finditer(text):
        return (pr_repo, int(m.group(1)))
    return None


def is_bot(login: str | None, user_type: str | None, ignored_bots: set[str]) -> bool:
    if not login:
        return True
    if user_type == "Bot":
        return True
    if login.lower().endswith("[bot]"):
        return True
    if login in ignored_bots:
        return True
    return False


def classify_filer(login, user_type, core, ignored_bots) -> str:
    if is_bot(login, user_type, ignored_bots):
        return "bot"
    if login in core:
        return "core"
    return "external"


def is_rate_limited(err: str) -> bool:
    low = err.lower()
    return any(marker in low for marker in RATE_LIMIT_MARKERS)


def gh_json(args: list[str], retries: int = 5):
    last_err = ""
    for attempt in range(1, retries + 1):
        r = subprocess.run(["gh", *args], capture_output=True, text=True)
        if r.returncode == 0:
            return json.loads(r.stdout) if r.stdout.strip() else None
        last_err = r.stderr.strip()
        if is_rate_limited(last_err):
            wait = attempt * 25
            print(f"  rate limited (attempt {attempt}/{retries}), sleep {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        raise RuntimeError(last_err or f"gh failed: {' '.join(args)}")
    raise RuntimeError(
        f"rate limit retries exhausted: {last_err}; args: {' '.join(args)}"
    )


def list_repos() -> list[str]:
    data = gh_json(["api", f"/orgs/{ORG}/repos", "--paginate"])
    return sorted(r["name"] for r in data if not r.get("archived") and not r.get("fork"))


def _search_merged(repo: str, start: date, end: date) -> list[dict]:
    cmd = [
        "search", "prs",
        "--repo", f"{ORG}/{repo}",
        "--merged",
        "--merged-at", f"{start.isoformat()}..{end.isoformat()}",
        "--json", "title,closedAt,number,body,url",
        "--limit", "1000",
    ]
    return gh_json(cmd) or []


def fetch_merged_range(repo: str, start: date, end: date) -> list[dict]:
    """Fetch all merged PRs in [start, end] inclusive, splitting on the 1000 cap."""
    by_number: dict[int, dict] = {}

    def collect(chunk_start: date, chunk_end: date) -> None:
        data = _search_merged(repo, chunk_start, chunk_end)
        if len(data) >= 1000:
            if chunk_start >= chunk_end:
                raise RuntimeError(
                    f"Search cap exceeded for {repo} on {chunk_start} "
                    f"(>=1000 merged PRs in one day)"
                )
            span = (chunk_end - chunk_start).days
            mid = chunk_start + timedelta(days=span // 2)
            collect(chunk_start, mid)
            collect(mid + timedelta(days=1), chunk_end)
            return
        for pr in data:
            by_number[pr["number"]] = pr

    collect(start, end)
    time.sleep(2.5)
    return list(by_number.values())


def fetch_issue_author(issue_repo: str, number: int, pr_repo: str, cache: dict) -> tuple[str | None, str | None]:
    key = (issue_repo, number)
    if key in cache:
        return cache[key]
    # Only probe the referenced repo, then the PR's repo when they differ.
    for name in dict.fromkeys([issue_repo, pr_repo]):
        try:
            data = gh_json([
                "api", f"repos/{ORG}/{name}/issues/{number}",
                "--jq", "{login: .user.login, type: .user.type}",
            ], retries=3)
            if data and data.get("login"):
                cache[key] = (data["login"], data.get("type"))
                return cache[key]
        except RuntimeError:
            continue
    cache[key] = (None, None)
    return cache[key]


def ensure_csvs():
    PR_TYPE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not PR_TYPE_FILE.exists():
        with PR_TYPE_FILE.open("w", newline="") as f:
            csv.writer(f).writerow(PR_TYPE_HEADER)
    if not PR_TYPE_DETAILS_FILE.exists():
        with PR_TYPE_DETAILS_FILE.open("w", newline="") as f:
            csv.writer(f).writerow(PR_TYPE_DETAILS_HEADER)


def existing_day_repo() -> set[tuple[str, str]]:
    if not PR_TYPE_FILE.exists():
        return set()
    with PR_TYPE_FILE.open() as f:
        return {(row["date"], row["repo"]) for row in csv.DictReader(f)}


def existing_detail_keys() -> set[tuple[str, str, str]]:
    if not PR_TYPE_DETAILS_FILE.exists():
        return set()
    with PR_TYPE_DETAILS_FILE.open() as f:
        return {
            (row["date"], row["repo"], row["number"])
            for row in csv.DictReader(f)
        }


def month_chunks(start: date, end: date):
    """Yield (chunk_start, chunk_end) covering [start, end] in ~30-day pieces."""
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=29), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def process_prs(prs, repo, core, ignored_bots, author_cache):
    """Bucket PRs by merge day and classify."""
    by_day = defaultdict(list)
    for pr in prs:
        closed = pr.get("closedAt") or ""
        if len(closed) < 10:
            continue
        by_day[closed[:10]].append(pr)

    day_rows = {}
    detail_rows = []

    for day_s, day_prs in sorted(by_day.items()):
        counts = {t: 0 for t in TYPES}
        fix_counts = {"core": 0, "external": 0, "bot": 0, "unlinked": 0}
        for pr in day_prs:
            title = pr.get("title") or ""
            pr_type = classify_title(title)
            counts[pr_type] += 1
            fix_source = ""
            issue_num = ""
            issue_author = ""

            if pr_type == "fix":
                ref = extract_issue_ref(repo, title, pr.get("body"))
                if not ref:
                    fix_source = "unlinked"
                    fix_counts["unlinked"] += 1
                else:
                    issue_repo, num = ref
                    login, typ = fetch_issue_author(issue_repo, num, repo, author_cache)
                    if not login:
                        fix_source = "unlinked"
                        fix_counts["unlinked"] += 1
                    else:
                        fix_source = classify_filer(login, typ, core, ignored_bots)
                        fix_counts[fix_source] += 1
                        issue_author = login
                    issue_num = str(num)

            detail_rows.append({
                "date": day_s,
                "repo": repo,
                "number": pr["number"],
                "title": title,
                "pr_type": pr_type,
                "fix_source": fix_source,
                "issue_number": issue_num,
                "issue_author": issue_author,
                "url": pr.get("url") or f"https://github.com/{ORG}/{repo}/pull/{pr['number']}",
            })

        day_rows[day_s] = (
            [day_s, repo,
             counts["feat"], counts["fix"], counts["docs"], counts["ci"],
             counts["chore"], counts["test"], counts["perf"], counts["other"],
             fix_counts["core"], fix_counts["external"], fix_counts["bot"], fix_counts["unlinked"]],
            len(day_prs),
        )
    return day_rows, detail_rows


def write_zero_row(day_s: str, repo: str):
    with PR_TYPE_FILE.open("a", newline="") as f:
        csv.writer(f).writerow([day_s, repo, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])


def collect_range(start: date, end: date, repos: list[str], core, ignored_bots,
                  skip_existing: bool) -> int:
    existing_rows = existing_day_repo() if skip_existing else set()
    existing_details = existing_detail_keys() if skip_existing else set()
    author_cache = {}
    total_prs = 0

    all_day_rows = {}
    all_details = []

    for repo in repos:
        print(f"Fetching {ORG}/{repo} {start} → {end}...")
        for chunk_start, chunk_end in month_chunks(start, end):
            print(f"  chunk {chunk_start}..{chunk_end}")
            prs = fetch_merged_range(repo, chunk_start, chunk_end)
            print(f"    {len(prs)} merged PRs")
            day_rows, details = process_prs(prs, repo, core, ignored_bots, author_cache)
            for day_s, (row, n) in day_rows.items():
                all_day_rows[(day_s, repo)] = row
                total_prs += n
            all_details.extend(details)

    cur = start
    while cur <= end:
        day_s = cur.isoformat()
        for repo in repos:
            key = (day_s, repo)
            if skip_existing and key in existing_rows:
                continue
            if key in all_day_rows:
                with PR_TYPE_FILE.open("a", newline="") as f:
                    csv.writer(f).writerow(all_day_rows[key])
            else:
                write_zero_row(day_s, repo)
        cur += timedelta(days=1)

    if all_details:
        with PR_TYPE_DETAILS_FILE.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=PR_TYPE_DETAILS_HEADER)
            for d in all_details:
                detail_key = (d["date"], d["repo"], str(d["number"]))
                if skip_existing and detail_key in existing_details:
                    continue
                w.writerow(d)

    return total_prs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="Single day YYYY-MM-DD (default: yesterday)")
    p.add_argument("--from", dest="date_from", help="Backfill start YYYY-MM-DD")
    p.add_argument("--to", dest="date_to", help="Backfill end YYYY-MM-DD (inclusive)")
    p.add_argument("--repos", nargs="*", help="Limit to these repo names")
    p.add_argument("--force", action="store_true", help="Wipe and re-collect range")
    args = p.parse_args()

    core, ignored_bots = load_config()
    ensure_csvs()

    if os.environ.get("PR_TYPE_REPOS"):
        repos = [r.strip() for r in os.environ["PR_TYPE_REPOS"].split(",") if r.strip()]
    elif args.repos:
        repos = args.repos
    else:
        repos = list_repos()

    yesterday = date.today() - timedelta(days=1)
    if args.date_from:
        start = date.fromisoformat(args.date_from)
        end = date.fromisoformat(args.date_to) if args.date_to else yesterday
    elif args.date:
        start = end = date.fromisoformat(args.date)
    else:
        start = end = yesterday

    if args.force:
        with PR_TYPE_FILE.open("w", newline="") as f:
            csv.writer(f).writerow(PR_TYPE_HEADER)
        with PR_TYPE_DETAILS_FILE.open("w", newline="") as f:
            csv.writer(f).writerow(PR_TYPE_DETAILS_HEADER)

    print(f"Collecting PR type metrics {start} → {end} for {repos}...")
    total = collect_range(start, end, repos, core, ignored_bots, skip_existing=not args.force)

    rows = sum(1 for _ in open(PR_TYPE_FILE)) - 1
    details = sum(1 for _ in open(PR_TYPE_DETAILS_FILE)) - 1
    print(f"Done. {total} PRs this run; {rows} aggregate rows, {details} detail rows")


if __name__ == "__main__":
    main()
