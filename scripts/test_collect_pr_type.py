#!/usr/bin/env python3
"""Unit tests for collect-pr-type helpers (no GitHub calls)."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "collect_pr_type", Path(__file__).parent / "collect-pr-type.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)

classify_title = mod.classify_title
extract_issue_ref = mod.extract_issue_ref
is_rate_limited = mod.is_rate_limited


class TestClassifyTitle(unittest.TestCase):
    def test_conventional_types(self):
        self.assertEqual(classify_title("feat(cli): add flag"), "feat")
        self.assertEqual(classify_title("fix!: breaking"), "fix")

    def test_other(self):
        self.assertEqual(classify_title("Update readme"), "other")


class TestExtractIssueRef(unittest.TestCase):
    def test_title_ref_uses_pr_repo(self):
        self.assertEqual(extract_issue_ref("agents", "fix(#42): thing", ""), ("agents", 42))

    def test_qualified_body_ref(self):
        body = "Closes fullsend-ai/agents#99"
        self.assertEqual(extract_issue_ref("fullsend", "fix: x", body), ("agents", 99))

    def test_local_body_ref_uses_pr_repo(self):
        body = "Closes #12"
        self.assertEqual(extract_issue_ref("fullsend", "fix: x", body), ("fullsend", 12))

    def test_no_ref(self):
        self.assertIsNone(extract_issue_ref("fullsend", "fix: x", "no linkage"))


class TestRateLimitDetection(unittest.TestCase):
    def test_rate_limit_true(self):
        self.assertTrue(is_rate_limited("API rate limit exceeded"))
        self.assertTrue(is_rate_limited("secondary rate limit"))

    def test_permission_403_false(self):
        self.assertFalse(is_rate_limited("HTTP 403: Resource not accessible by integration"))


AGG_HEADER = "date,repo,feat,fix,docs,ci,chore,test,perf,other,fix_core,fix_external,fix_bot,fix_unlinked\r\n"
DETAILS_HEADER = "date,repo,number,title,pr_type,fix_source,issue_number,issue_author,url\r\n"


def agg_line(day, repo, feat=0):
    return f"{day},{repo},{feat},0,0,0,0,0,0,0,0,0,0,0\r\n"


def detail_line(day, repo, number, title="feat: old"):
    return (f"{day},{repo},{number},{title},feat,,,,"
            f"https://github.com/fullsend-ai/{repo}/pull/{number}\r\n")


class TestForceReplaceRange(unittest.TestCase):
    """--force replaces only the requested range, after fetching."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.agg = tmp / "pr-type.csv"
        self.details = tmp / "pr-type-details.csv"
        self.before = [agg_line("2026-05-01", r) for r in ("fullsend", "agents")]
        self.inside = [agg_line(d, r, feat=7) for d in ("2026-05-02", "2026-05-03")
                       for r in ("fullsend", "agents")]
        self.after = [agg_line("2026-05-04", r) for r in ("fullsend", "agents")]
        self.agg.write_text(AGG_HEADER + "".join(self.before + self.inside + self.after),
                            newline="")
        self.d_before = [detail_line("2026-05-01", "fullsend", 1, '"feat: a, b"')]
        self.d_inside = [detail_line("2026-05-02", "fullsend", 2),
                         detail_line("2026-05-03", "agents", 3)]
        self.d_after = [detail_line("2026-05-04", "agents", 4)]
        self.details.write_text(
            DETAILS_HEADER + "".join(self.d_before + self.d_inside + self.d_after),
            newline="")

        self.saved = (mod.PR_TYPE_FILE, mod.PR_TYPE_DETAILS_FILE,
                      mod.fetch_merged_range, mod.fetch_issue_author)
        mod.PR_TYPE_FILE = self.agg
        mod.PR_TYPE_DETAILS_FILE = self.details
        mod.fetch_issue_author = lambda *a, **k: (None, None)
        self.fetched = {
            "fullsend": [{"number": 20, "title": "feat: new", "closedAt": "2026-05-02T10:00:00Z",
                          "url": "https://github.com/fullsend-ai/fullsend/pull/20"}],
            "agents": [{"number": 30, "title": "docs: new", "closedAt": "2026-05-03T10:00:00Z",
                        "url": "https://github.com/fullsend-ai/agents/pull/30"}],
        }

        def fake_fetch(repo, start, end):
            return [pr for pr in self.fetched.get(repo, [])
                    if start.isoformat() <= pr["closedAt"][:10] <= end.isoformat()]

        mod.fetch_merged_range = fake_fetch

    def tearDown(self):
        (mod.PR_TYPE_FILE, mod.PR_TYPE_DETAILS_FILE,
         mod.fetch_merged_range, mod.fetch_issue_author) = self.saved
        self.tmp.cleanup()

    def run_force(self, start, end, repos=("fullsend", "agents")):
        mod.collect_range(date.fromisoformat(start), date.fromisoformat(end), list(repos),
                          set(), set(), skip_existing=False, replace=True)

    def read(self, path):
        return path.read_text(newline="")

    def test_outside_rows_byte_identical_and_inside_replaced(self):
        self.run_force("2026-05-02", "2026-05-03")
        new_agg = [
            "2026-05-02,fullsend,1,0,0,0,0,0,0,0,0,0,0,0\r\n",
            agg_line("2026-05-02", "agents"),
            agg_line("2026-05-03", "fullsend"),
            "2026-05-03,agents,0,0,1,0,0,0,0,0,0,0,0,0\r\n",
        ]
        self.assertEqual(self.read(self.agg),
                         AGG_HEADER + "".join(self.before + new_agg + self.after))
        new_details = [
            "2026-05-02,fullsend,20,feat: new,feat,,,,https://github.com/fullsend-ai/fullsend/pull/20\r\n",
            "2026-05-03,agents,30,docs: new,docs,,,,https://github.com/fullsend-ai/agents/pull/30\r\n",
        ]
        self.assertEqual(self.read(self.details),
                         DETAILS_HEADER + "".join(self.d_before + new_details + self.d_after))

    def test_rerun_does_not_duplicate(self):
        self.run_force("2026-05-02", "2026-05-03")
        first = (self.read(self.agg), self.read(self.details))
        self.run_force("2026-05-02", "2026-05-03")
        self.assertEqual((self.read(self.agg), self.read(self.details)), first)

    def test_single_day_range_is_inclusive(self):
        self.run_force("2026-05-03", "2026-05-03")
        self.assertEqual(
            self.read(self.agg),
            AGG_HEADER + "".join(self.before + self.inside[:2] + [
                agg_line("2026-05-03", "fullsend"),
                "2026-05-03,agents,0,0,1,0,0,0,0,0,0,0,0,0\r\n",
            ] + self.after))
        self.assertEqual(
            self.read(self.details),
            DETAILS_HEADER + "".join(self.d_before + self.d_inside[:1] + [
                "2026-05-03,agents,30,docs: new,docs,,,,https://github.com/fullsend-ai/agents/pull/30\r\n",
            ] + self.d_after))

    def test_other_repos_in_range_are_kept(self):
        self.run_force("2026-05-02", "2026-05-03", repos=("fullsend",))
        agg = self.read(self.agg)
        for line in self.before + self.after:
            self.assertIn(line, agg)
        self.assertIn(agg_line("2026-05-02", "agents", feat=7), agg)
        self.assertIn(agg_line("2026-05-03", "agents", feat=7), agg)
        self.assertIn(self.d_inside[1], self.read(self.details))

    def test_failed_fetch_leaves_files_untouched(self):
        agg, details = self.read(self.agg), self.read(self.details)

        def boom(*a, **k):
            raise RuntimeError("rate limit retries exhausted")

        mod.fetch_merged_range = boom
        with self.assertRaises(RuntimeError):
            self.run_force("2026-05-02", "2026-05-03")
        self.assertEqual(self.read(self.agg), agg)
        self.assertEqual(self.read(self.details), details)

    def test_range_with_no_existing_rows_inserts_in_date_order(self):
        self.agg.write_text(AGG_HEADER + "".join(self.before + self.after), newline="")
        self.run_force("2026-05-02", "2026-05-02")
        self.assertEqual(
            self.read(self.agg),
            AGG_HEADER + "".join(self.before + [
                "2026-05-02,fullsend,1,0,0,0,0,0,0,0,0,0,0,0\r\n",
                agg_line("2026-05-02", "agents"),
            ] + self.after))

    def test_empty_replacement_leaves_unterminated_last_record_untouched(self):
        # Last record is out of range and has no trailing newline; the range
        # collects no rows, so the file must stay byte-identical.
        details = DETAILS_HEADER + "".join(self.d_before + self.d_inside) + \
            detail_line("2026-05-04", "agents", 4).rstrip("\r\n")
        self.details.write_text(details, newline="")
        self.run_force("2026-05-10", "2026-05-11")
        self.assertEqual(self.read(self.details), details)


ORG_REPOS = [
    {"name": "pub", "visibility": "public", "archived": False, "fork": False},
    {"name": "priv", "visibility": "private", "archived": False, "fork": False},
    {"name": "internal", "visibility": "internal", "archived": False, "fork": False},
    {"name": "old", "visibility": "public", "archived": True, "fork": False},
    {"name": "forked", "visibility": "public", "archived": False, "fork": True},
    {"name": "unknown", "archived": False, "fork": False},
]


class TestRepoVisibility(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.real_gh_json = mod.gh_json

    def tearDown(self):
        mod.gh_json = self.real_gh_json

    def fake_gh(self, args, retries=5):
        path = args[1]
        self.calls.append(path)
        if path.endswith("/repos"):
            return ORG_REPOS
        if path.startswith("repos/") and "/issues/" in path:
            return {"login": "someone", "type": "User"}
        name = path.split("/")[-1]
        for repo in ORG_REPOS:
            if repo["name"] == name:
                return {"visibility": repo.get("visibility")}
        raise RuntimeError("HTTP 404: Not Found")

    def test_list_repos_keeps_only_public(self):
        mod.gh_json = self.fake_gh
        self.assertEqual(mod.list_repos(), ["pub"])

    def test_explicit_repo_lists_keep_only_public(self):
        mod.gh_json = self.fake_gh
        real = mod.is_public_repo
        visibility = {}
        mod.is_public_repo = lambda name: real(name, visibility)
        try:
            names = ["pub", "priv", "internal", "unknown", "missing"]
            self.assertEqual(mod.public_only(names), ["pub"])
            self.assertEqual(mod.public_only(["priv"]), [])
        finally:
            mod.is_public_repo = real

    def test_issue_author_skips_non_public_repos(self):
        mod.gh_json = self.fake_gh
        visibility = {}
        real = mod.is_public_repo
        mod.is_public_repo = lambda name: real(name, visibility)
        try:
            for issue_repo in ("priv", "internal", "unknown", "missing"):
                self.assertEqual(mod.fetch_issue_author(issue_repo, 5, "priv", {}), (None, None))
            self.assertEqual(mod.fetch_issue_author("pub", 5, "priv", {}), ("someone", "User"))
        finally:
            mod.is_public_repo = real
        issue_calls = [c for c in self.calls if "/issues/" in c]
        self.assertEqual(issue_calls, ["repos/fullsend-ai/pub/issues/5"])

    def test_failed_visibility_lookup_propagates(self):
        def failing(args, retries=5):
            raise RuntimeError("HTTP 502: Bad Gateway")
        mod.gh_json = failing
        with self.assertRaises(RuntimeError):
            mod.is_public_repo("pub", {})
        with self.assertRaises(RuntimeError):
            mod.public_only(["pub"])

    def test_not_found_lookup_is_non_public(self):
        def missing(args, retries=5):
            raise RuntimeError("gh: Not Found (HTTP 404)")
        mod.gh_json = missing
        cache = {}
        self.assertFalse(mod.is_public_repo("gone", cache))
        self.assertEqual(cache, {"gone": False})

    def test_issue_author_tolerates_failed_visibility_lookup(self):
        def failing(args, retries=5):
            raise RuntimeError("HTTP 502: Bad Gateway")
        mod.gh_json = failing
        real = mod.is_public_repo
        mod.is_public_repo = lambda name: real(name, {})
        try:
            self.assertEqual(mod.fetch_issue_author("pub", 5, "pub", {}), (None, None))
        finally:
            mod.is_public_repo = real

    def test_force_does_not_wipe_csvs_when_visibility_lookup_fails(self):
        def failing(args, retries=5):
            raise RuntimeError("HTTP 502: Bad Gateway")
        mod.gh_json = failing
        with tempfile.TemporaryDirectory() as d:
            agg = Path(d) / "pr-type.csv"
            det = Path(d) / "pr-type-details.csv"
            agg.write_text("existing\n")
            det.write_text("existing\n")
            saved = (mod.PR_TYPE_FILE, mod.PR_TYPE_DETAILS_FILE, mod.ensure_csvs,
                     mod.load_config, mod.os.environ.get("PR_TYPE_REPOS"))
            mod.PR_TYPE_FILE, mod.PR_TYPE_DETAILS_FILE = agg, det
            mod.ensure_csvs = lambda: None
            mod.load_config = lambda: (set(), set())
            mod.os.environ["PR_TYPE_REPOS"] = "pub"
            try:
                with mock.patch.object(sys, "argv", ["collect-pr-type.py", "--force", "--date", "2026-11-10"]):
                    with self.assertRaises(RuntimeError):
                        mod.main()
            finally:
                mod.PR_TYPE_FILE, mod.PR_TYPE_DETAILS_FILE, mod.ensure_csvs, mod.load_config = saved[:4]
                if saved[4] is None:
                    mod.os.environ.pop("PR_TYPE_REPOS", None)
                else:
                    mod.os.environ["PR_TYPE_REPOS"] = saved[4]
            self.assertEqual(agg.read_text(), "existing\n")
            self.assertEqual(det.read_text(), "existing\n")


if __name__ == "__main__":
    unittest.main()
