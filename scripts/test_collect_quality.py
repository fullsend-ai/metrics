#!/usr/bin/env python3
"""Unit tests for collect-quality helpers (no GitHub calls)."""
from __future__ import annotations

import csv
import importlib.util
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "collect_quality", Path(__file__).parent / "collect-quality.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def gh_result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["gh"], returncode, stdout=stdout, stderr=stderr)


STREAM_CANCEL = 'Post "https://api.github.com/search/issues": stream error: stream ID 5; CANCEL'
GATEWAY_TIMEOUT = "HTTP 504: Gateway Timeout (https://api.github.com/repos/x/y/issues/1)"


class TestGhJsonRetry(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(mod.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def run_gh(self, *results, retries=5):
        with mock.patch.object(mod.subprocess, "run", side_effect=list(results)) as run:
            try:
                return mod.gh_json(["api", "x"], retries=retries), run.call_count
            except RuntimeError as err:
                return err, run.call_count

    def test_retries_5xx_and_stream_errors_then_succeeds(self):
        data, calls = self.run_gh(
            gh_result(1, stderr=STREAM_CANCEL),
            gh_result(1, stderr=GATEWAY_TIMEOUT),
            gh_result(0, stdout='{"labels": []}'),
        )
        self.assertEqual(data, {"labels": []})
        self.assertEqual(calls, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_rate_limit_still_retried(self):
        data, calls = self.run_gh(
            gh_result(1, stderr="API rate limit exceeded"),
            gh_result(0, stdout="[]"),
        )
        self.assertEqual(data, [])
        self.assertEqual(calls, 2)

    def test_persistent_failure_raises_after_limit(self):
        err, calls = self.run_gh(*[gh_result(1, stderr=STREAM_CANCEL)] * 4, retries=4)
        self.assertIsInstance(err, RuntimeError)
        self.assertIn("CANCEL", str(err))
        self.assertEqual(calls, 4)
        self.assertEqual(self.sleep.call_count, 3)

    def test_not_found_is_not_retried(self):
        err, calls = self.run_gh(gh_result(1, stderr="HTTP 404: Not Found"))
        self.assertIsInstance(err, RuntimeError)
        self.assertEqual(calls, 1)
        self.sleep.assert_not_called()


def write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


class TestRangeRebuild(unittest.TestCase):
    def test_range_looks_up_linked_issues_without_label_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            aggregate = tmp_path / "pr-type.csv"
            details = tmp_path / "pr-type-details.csv"
            output = tmp_path / "quality.csv"
            counts = {name: "0" for name in mod.TYPES}
            write_csv(aggregate, ["date", "repo", *mod.TYPES], [
                {"date": "2026-11-01", "repo": "fullsend", **counts, "fix": "2"},
                {"date": "2026-11-02", "repo": "fullsend", **counts, "fix": "1"},
            ])
            write_csv(details, ["date", "repo", "pr_type", "title", "issue_number"], [
                {"date": "2026-11-01", "repo": "fullsend", "pr_type": "fix", "title": "fix: a", "issue_number": "10"},
                {"date": "2026-11-01", "repo": "fullsend", "pr_type": "fix", "title": "fix: b", "issue_number": "11"},
                {"date": "2026-11-02", "repo": "fullsend", "pr_type": "fix", "title": "fix: c", "issue_number": "10"},
            ])
            calls = []

            def gh(args, retries=5):
                calls.append(args)
                if args[0] == "search":
                    raise AssertionError("range mode must not use a capped label search")
                number = args[1].rsplit("/", 1)[1]
                return {"labels": [{"name": "Bug"}] if number == "10" else []}

            with mock.patch.multiple(mod, AGGREGATE=aggregate, DETAILS=details, OUTPUT=output,
                                     gh_json=gh, git_revert_counts=lambda start, end: Counter()):
                mod.rebuild({"2026-11-01", "2026-11-02"})

            rows = {row["date"]: row for row in csv.DictReader(output.open())}
            self.assertEqual(rows["2026-11-01"]["defect_prs"], "1")
            self.assertEqual(rows["2026-11-02"]["defect_prs"], "1")
            # Each linked issue is fetched once and cached across the range.
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
