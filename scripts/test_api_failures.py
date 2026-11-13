#!/usr/bin/env python3
"""Failed GitHub API calls must stop the shell collectors, never become zeros.

Each test runs a real collector script in a temporary directory with a stub
`gh` on PATH. The stub answers from a list of (substring, exit code, stdout)
rules matched against its arguments; the first match wins and anything
unmatched succeeds with empty output (a legitimately empty result).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
DATE = "2026-11-01"
RATE_LIMITED = "rate-limited"
ZERO_COUNT = [".total_count", 0, "0"]

STUB = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    args = " ".join(sys.argv[1:])
    rules = [["/orgs/fullsend-ai/repos", 0, "demo"]] + json.loads(os.environ.get("GH_STUB_RULES", "[]"))
    for needle, code, out in rules:
        if needle in args:
            if out == "%s":
                # Real gh prints the error body on stdout and the message on stderr.
                print('{"message":"API rate limit exceeded","status":"403"}')
                print("gh: API rate limit exceeded (HTTP 403)", file=sys.stderr)
            elif out:
                print(out)
            sys.exit(code)
    """
    % RATE_LIMITED
)


def fail(needle: str) -> list:
    return [needle, 1, RATE_LIMITED]


class ScriptCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)
        bin_dir = self.work / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "gh"
        stub.write_text(STUB)
        stub.chmod(0o755)
        self.path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self, name: str, *args: str, rules: list | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ, PATH=self.path, GH_STUB_RULES=json.dumps(rules or []))
        env.pop("COMMUNITY_REVIEW_COUNTS_FILE", None)
        return subprocess.run(
            ["bash", str(SCRIPTS / name), *args],
            cwd=self.work, env=env, capture_output=True, text=True, timeout=120,
        )

    def data_lines(self, csv_name: str) -> list[str]:
        path = self.work / "docs" / csv_name
        if not path.exists():
            return []
        return path.read_text().splitlines()[1:]

    def assert_failed_without_rows(self, result, *csv_names: str):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("rate limit", result.stderr.lower())
        for csv_name in csv_names:
            self.assertEqual(self.data_lines(csv_name), [], csv_name)


class TestCollect(ScriptCase):
    def test_failed_count_is_not_written_as_zero(self):
        result = self.run_script("collect.sh", DATE, rules=[fail("/search/issues")])
        self.assert_failed_without_rows(result, "metrics.csv", "metric-details.csv")
        self.assertIn("search_count: API call failed", result.stderr)

    def test_late_failure_leaves_no_partial_rows(self):
        rules = [
            fail("is:open"),
            [".total_count", 0, "1"],
            ["is:pr created:", 0, "7\tA title\thttps://example.test/7"],
        ]
        result = self.run_script("collect.sh", DATE, rules=rules)
        self.assert_failed_without_rows(result, "metrics.csv", "metric-details.csv")

    def test_failed_detail_listing_fails(self):
        rules = [[".total_count", 0, "1"], fail("is:pr is:merged merged:")]
        result = self.run_script("collect.sh", DATE, rules=rules)
        self.assert_failed_without_rows(result, "metrics.csv", "metric-details.csv")

    def test_failed_lead_time_fetch_mid_loop_fails(self):
        rules = [
            [".total_count", 0, "1"],
            ["pull_request.url", 0, "https://example.test/pulls/1\nhttps://example.test/pulls/2"],
            fail("/pulls/1"),
            ["/pulls/2", 0, "1.5"],
        ]
        result = self.run_script("collect.sh", DATE, rules=rules)
        self.assert_failed_without_rows(result, "metrics.csv", "metric-details.csv")

    def test_empty_result_is_a_real_zero(self):
        result = self.run_script("collect.sh", DATE, rules=[ZERO_COUNT])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.data_lines("metrics.csv"), [f"{DATE},demo,0,0,0,0,0,0,0,0"])

    def test_release_counts_are_summed_across_pages(self):
        result = self.run_script("collect.sh", DATE, rules=[ZERO_COUNT, ["/releases", 0, "2\n0\n1"]])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.data_lines("metrics.csv"), [f"{DATE},demo,0,0,0,0,0,3,0,0"])


class TestBackfill(ScriptCase):
    def test_failed_fetch_is_not_bucketed_as_zero(self):
        result = self.run_script("backfill.sh", DATE, DATE, rules=[fail("is:issue created:")])
        self.assert_failed_without_rows(result, "metrics.csv")

    def test_empty_result_is_a_real_zero(self):
        result = self.run_script("backfill.sh", DATE, DATE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.data_lines("metrics.csv"), [f"{DATE},demo,0,0,0,0,0,0,0,0"])


class TestBackfillMetricDetails(ScriptCase):
    def test_failure_mid_day_leaves_no_partial_day(self):
        rules = [
            ["is:pr created:", 0, "7\tA title\thttps://example.test/7"],
            fail("is:pr is:merged"),
        ]
        result = self.run_script("backfill-metric-details.sh", DATE, DATE, rules=rules)
        self.assert_failed_without_rows(result, "metric-details.csv")


class TestFailures(ScriptCase):
    def test_collect_failure_is_not_no_runs(self):
        result = self.run_script("collect-failures.sh", DATE, rules=[fail("/actions/runs")])
        self.assert_failed_without_rows(result, "failures.csv", "failure-details.csv")
        self.assertNotIn("No workflow runs found", result.stdout)

    def test_collect_empty_result_succeeds(self):
        result = self.run_script("collect-failures.sh", DATE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No workflow runs found", result.stdout)

    def test_backfill_details_failure_fails(self):
        result = self.run_script("backfill-failure-details.sh", DATE, DATE, rules=[fail("/actions/runs")])
        self.assert_failed_without_rows(result, "failure-details.csv")


class TestRework(ScriptCase):
    ITEM = ["updated:>=", 0, "5\thttps://example.test/5"]

    def test_collect_timeline_failure_fails(self):
        result = self.run_script("collect-rework.sh", DATE, rules=[self.ITEM, fail("/timeline")])
        self.assert_failed_without_rows(result, "rework.csv", "rework-details.csv")

    def test_collect_project_board_failure_fails(self):
        result = self.run_script("collect-rework.sh", DATE, rules=[fail("graphql")])
        self.assert_failed_without_rows(result, "rework.csv", "rework-details.csv")

    def test_collect_empty_result_succeeds(self):
        result = self.run_script("collect-rework.sh", DATE)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_backfill_search_failure_fails(self):
        result = self.run_script("backfill-rework.sh", DATE, DATE, rules=[fail("updated:>=")])
        self.assert_failed_without_rows(result, "rework.csv", "rework-details.csv")


class TestCommunity(ScriptCase):
    def test_failed_close_classification_is_not_other(self):
        rules = [
            ["is:issue is:closed", 0, "5\talice\tUser\tA title\thttps://example.test/5"],
            fail("graphql"),
        ]
        result = self.run_script("collect-community.sh", DATE, rules=rules)
        self.assert_failed_without_rows(result, "community.csv", "community-details.csv")

    def test_failed_review_fetch_fails(self):
        rules = [
            ["is:pr updated:>=", 0, "9\tbob\tA title\thttps://example.test/9"],
            fail("graphql"),
        ]
        result = self.run_script("collect-community.sh", DATE, rules=rules)
        self.assert_failed_without_rows(result, "community.csv", "community-details.csv")

    def test_backfill_review_scan_pr_list_failure_fails(self):
        counts = self.work / "counts"
        result = self.run_script(
            "backfill-collect-reviews.sh", DATE, DATE, str(counts), rules=[fail("/pulls")]
        )
        self.assert_failed_without_rows(result, "community-details.csv")

    def test_backfill_review_scan_review_failure_fails(self):
        counts = self.work / "counts"
        rules = [["/pulls", 0, "9\tbob\tA title\thttps://example.test/9"], fail("graphql")]
        result = self.run_script("backfill-collect-reviews.sh", DATE, DATE, str(counts), rules=rules)
        self.assert_failed_without_rows(result, "community-details.csv")


if __name__ == "__main__":
    unittest.main()
