#!/usr/bin/env python3
"""Failed GitHub API calls must stop the shell collectors, never become zeros.

Each test runs a real collector script in a temporary directory with a stub
`gh` on PATH. The stub answers from a list of (substring, exit code, stdout)
rules matched against its arguments; the first match wins and anything
unmatched succeeds with empty output (a legitimately empty result). A rule
may carry a fourth item, N: its first N matching calls fail like a rate limit
before it answers normally, which models a transient error.
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
# collect_agent_runs finds the dispatch workflow, then counts its runs.
DISPATCH_WORKFLOW = ["/actions/workflows", 0, "1"]
# The project-board query is opt-in; this enables it for a run.
PROJECTS_ON = {"REWORK_INCLUDE_PROJECTS": "1"}
# One bot event from the project-board GraphQL query (repo, number, bot, ts, url).
PROJECT_TOUCH = ["graphql", 0, f"demo\t5\tboard-bot[bot]\t{DATE}T10:00:00Z\thttps://example.test/5"]

STUB = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    args = " ".join(sys.argv[1:])
    rules = [["/orgs/fullsend-ai/repos", 0, "demo"]] + json.loads(os.environ.get("GH_STUB_RULES", "[]"))
    for i, (needle, code, out, *flaky) in enumerate(rules):
        if needle in args:
            if flaky:
                state = os.path.join(os.environ["GH_STUB_STATE"], str(i))
                seen = int(open(state).read()) if os.path.exists(state) else 0
                open(state, "w").write(str(seen + 1))
                if seen < flaky[0]:
                    out, code = "%s", 1
            if out == "%s":
                # Real gh prints the error body on stdout and the message on stderr.
                print('{"message":"API rate limit exceeded","status":"403"}')
                print("gh: API rate limit exceeded (HTTP 403)", file=sys.stderr)
            elif out:
                print(out)
            sys.exit(code)
    """
    % (RATE_LIMITED, RATE_LIMITED)
)


def fail(needle: str) -> list:
    return [needle, 1, RATE_LIMITED]


def fail_once(needle: str, out: str) -> list:
    """Fail the first matching call with a rate limit, then answer with `out`."""
    return [needle, 0, out, 1]


class ScriptCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)
        bin_dir = self.work / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "gh"
        stub.write_text(STUB)
        stub.chmod(0o755)
        # No real backoff in tests: retries would sleep for minutes.
        sleep = bin_dir / "sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n")
        sleep.chmod(0o755)
        self.path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        self.stub_state = self.work / "stub-state"
        self.stub_state.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self, name: str, *args: str, rules: list | None = None,
                   extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ, PATH=self.path, GH_STUB_RULES=json.dumps(rules or []), GH_RETRY_SLEEP="0",
                   GH_STUB_STATE=str(self.stub_state))
        env.pop("COMMUNITY_REVIEW_COUNTS_FILE", None)
        env.pop("REWORK_INCLUDE_PROJECTS", None)
        env.update(extra_env or {})
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

    def test_call_that_fails_once_is_retried_and_the_row_is_written(self):
        rules = [
            fail_once("is:pr is:open", "4"),
            fail_once(f"--paginate -f q=repo:fullsend-ai/demo is:pr created:{DATE}", "7\tA title\thttps://example.test/7"),
            fail_once("/releases", "1"),
            [".total_count", 0, "1"],
        ]
        result = self.run_script("collect.sh", DATE, rules=rules)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.data_lines("metrics.csv"), [f"{DATE},demo,1,1,0,1,1,1,0,4"])
        self.assertIn(f'{DATE},demo,pr,opened,7,"A title",https://example.test/7', self.data_lines("metric-details.csv"))
        # One failed attempt for each of the three flaky calls was retried.
        self.assertEqual(result.stderr.count("(attempt 1/"), 3, result.stderr)
        self.assertNotIn("(attempt 2/", result.stderr)

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
        result = self.run_script("collect-failures.sh", DATE, rules=[fail("/actions/workflows")])
        self.assert_failed_without_rows(result, "failures.csv", "failure-details.csv")
        self.assertNotIn("No agent jobs ran", result.stdout + result.stderr)

    def test_collect_empty_result_succeeds(self):
        result = self.run_script("collect-failures.sh", DATE, rules=[ZERO_COUNT, DISPATCH_WORKFLOW])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No agent jobs ran", result.stdout + result.stderr)

    def test_backfill_details_failure_fails(self):
        result = self.run_script("backfill-failure-details.sh", DATE, DATE, rules=[fail("/actions/workflows")])
        self.assert_failed_without_rows(result, "failure-details.csv")


class TestRework(ScriptCase):
    # search_updated_range counts the matches (per_page=1), then lists them.
    COUNT = ["per_page=1 --jq", 0, "1"]
    ITEM = ["per_page=100", 0, "5\thttps://example.test/5"]

    def test_collect_timeline_failure_fails(self):
        result = self.run_script("collect-rework.sh", DATE, rules=[self.COUNT, self.ITEM, fail("/timeline")])
        self.assert_failed_without_rows(result, "rework.csv", "rework-details.csv")

    def test_collect_project_board_failure_fails_when_enabled(self):
        result = self.run_script("collect-rework.sh", DATE, rules=[fail("graphql")],
                                 extra_env=PROJECTS_ON)
        self.assert_failed_without_rows(result, "rework.csv", "rework-details.csv")

    def test_collect_skips_project_board_by_default(self):
        # The board query would fail, but it must not even run.
        for extra_env in (None, {"REWORK_INCLUDE_PROJECTS": "0"}):
            with self.subTest(extra_env=extra_env):
                result = self.run_script("collect-rework.sh", DATE, rules=[fail("graphql"), PROJECT_TOUCH],
                                         extra_env=extra_env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Skipping project board activity", result.stdout)
                self.assertNotIn("Fetching project board activity", result.stdout)
                self.assertEqual(self.data_lines("rework.csv"), [])

    def test_collect_records_project_board_activity_when_enabled(self):
        result = self.run_script("collect-rework.sh", DATE, rules=[PROJECT_TOUCH], extra_env=PROJECTS_ON)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Fetching project board activity", result.stdout)
        self.assertNotIn("Skipping project board activity", result.stdout)
        self.assertIn(f"{DATE},board-bot[bot],1,0,0.000", self.data_lines("rework.csv"))

    def test_collect_empty_result_succeeds(self):
        result = self.run_script("collect-rework.sh", DATE)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_backfill_search_failure_fails(self):
        result = self.run_script("backfill-rework.sh", DATE, DATE, rules=[fail("/search/issues")])
        self.assert_failed_without_rows(result, "rework.csv", "rework-details.csv")

    def test_backfill_project_board_failure_fails_when_enabled(self):
        result = self.run_script("backfill-rework.sh", DATE, DATE, rules=[fail("graphql")],
                                 extra_env=PROJECTS_ON)
        self.assert_failed_without_rows(result, "rework.csv", "rework-details.csv")

    def test_backfill_skips_project_board_by_default(self):
        for extra_env in (None, {"REWORK_INCLUDE_PROJECTS": "0"}):
            with self.subTest(extra_env=extra_env):
                result = self.run_script("backfill-rework.sh", DATE, DATE, rules=[fail("graphql"), PROJECT_TOUCH],
                                         extra_env=extra_env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Skipping project board activity", result.stdout)
                self.assertNotIn("Fetching project board activity", result.stdout)
                self.assertEqual(self.data_lines("rework.csv"), [])

    def test_backfill_records_project_board_activity_when_enabled(self):
        result = self.run_script("backfill-rework.sh", DATE, DATE, rules=[PROJECT_TOUCH], extra_env=PROJECTS_ON)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Fetching project board activity", result.stdout)
        self.assertIn(f"{DATE},board-bot[bot],1,0,0.000", self.data_lines("rework.csv"))


class TestCommunity(ScriptCase):
    def test_failed_close_classification_is_not_other(self):
        rules = [
            ["is:issue is:closed", 0, "5\talice\tUser\tA title\thttps://example.test/5"],
            fail("graphql"),
        ]
        result = self.run_script("collect-community.sh", DATE, rules=rules)
        self.assert_failed_without_rows(result, "community.csv", "community-details.csv")

    def test_close_classification_that_fails_once_is_retried(self):
        rules = [
            ["is:issue is:closed", 0, "5\talice\tUser\tA title\thttps://example.test/5"],
            fail_once("graphql", "pr"),
        ]
        result = self.run_script("collect-community.sh", DATE, rules=rules)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rate limit", result.stderr.lower())
        self.assertEqual(len(self.data_lines("community.csv")), 1)
        self.assertEqual(len(self.data_lines("community-details.csv")), 1)

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
