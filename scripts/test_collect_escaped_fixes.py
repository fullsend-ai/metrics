#!/usr/bin/env python3
"""Unit tests for collect-escaped-fixes helpers (no GitHub calls)."""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "collect_escaped_fixes", Path(__file__).parent / "collect-escaped-fixes.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def pr(repo, number, closed, title="fix: x", body=""):
    return {"repo": repo, "number": number, "closedAt": closed, "title": title, "body": body}


def no_sha(repo, sha):
    return None


class TestDetect(unittest.TestCase):
    def test_named_local_qualified_and_url(self):
        body = "See #10, fullsend-ai/agents#20 and https://github.com/fullsend-ai/fullsend/pull/30"
        found = mod.detect(pr("fullsend", 99, "", body=body), {}, no_sha)
        self.assertEqual(found, {"fullsend#10": "named", "agents#20": "named", "fullsend#30": "named"})

    def test_ignores_other_orgs_markdown_and_self(self):
        body = "## Notes\nother/repo#5, color &#35;12, this PR #99"
        self.assertEqual(mod.detect(pr("fullsend", 99, "", body=body), {}, no_sha), {})

    def test_causal_with_named(self):
        found = mod.detect(pr("agents", 50, "", body="This regression was introduced in #40."), {}, no_sha)
        self.assertEqual(found, {"agents#40": "causal+named"})

    def test_causal_sha_resolves_to_pr(self):
        found = mod.detect(
            pr("agents", 50, "", body="Broken by commit abc1234 last week."), {},
            lambda repo, sha: "agents#41" if sha == "abc1234" else None,
        )
        self.assertEqual(found, {"agents#41": "causal"})

    def test_named_in_linked_issue(self):
        found = mod.detect(pr("agents", 50, "", title="fix(#49): x"), {}, no_sha, "Caused by #44")
        # #49 is the issue itself; pair_rows drops it because it is not a merged PR.
        self.assertEqual(found, {"agents#49": "causal+named", "agents#44": "causal+named"})

    def test_revert_by_title_and_sha(self):
        by_title = {("fullsend", "feat: add thing"): "fullsend#7"}
        found = mod.detect(pr("fullsend", 9, "", title='Revert "feat: add thing"'), by_title, no_sha)
        self.assertEqual(found, {"fullsend#7": "revert"})
        found = mod.detect(
            pr("fullsend", 9, "", title="fix: revert cache", body="This reverts commit deadbeef1."),
            {}, lambda repo, sha: "fullsend#8",
        )
        self.assertEqual(found, {"fullsend#8": "revert"})

    def test_repo_shorthand(self):
        body = "Regressed in agents#20; see fullsend#30, other/agents#5 and myagents#6"
        found = mod.detect(pr("fullsend", 99, "", body=body), {}, no_sha)
        self.assertEqual(found, {"agents#20": "causal+named", "fullsend#30": "causal+named"})

    def test_causal_sha_qualified_with_its_repo(self):
        seen = []
        mod.detect(pr("fullsend", 50, "", body="Introduced by commit fullsend-ai/agents@abc1234"), {},
                   lambda repo, sha: seen.append((repo, sha)))
        self.assertEqual(seen, [("agents", "abc1234")])

    def test_dependency_bumps_skipped(self):
        self.assertEqual(mod.detect(pr("fullsend", 9, "", title="chore(deps): bump x", body="#3"), {}, no_sha), {})


class TestLinkedIssues(unittest.TestCase):
    def setUp(self):
        self.pr_type = mod._load_pr_type()

    def test_reads_every_closed_issue(self):
        issues = {
            "repos/fullsend-ai/fullsend/issues/5": {"title": "first", "body": "nothing here"},
            "repos/fullsend-ai/agents/issues/6": {"title": "second", "body": "broken by #40"},
        }
        linked = mod.gh_linked_issue_text(self.pr_type, issues.get)
        fix = pr("fullsend", 50, "", title="fix(#5): x", body="Closes fullsend-ai/agents#6")
        found = mod.detect(fix, {}, no_sha, linked(fix))
        self.assertIn("agents#40", found)

    def test_url_closing_reference_reads_the_issue(self):
        issues = {"repos/fullsend-ai/fullsend/issues/20": {"title": "bug", "body": "Introduced by #10"}}
        linked = mod.gh_linked_issue_text(self.pr_type, issues.get)
        fix = pr("fullsend", 50, "", body="Fixes https://github.com/fullsend-ai/fullsend/issues/20")
        self.assertIn("fullsend#10", mod.detect(fix, {}, no_sha, linked(fix)))

    def test_commit_in_a_cross_repo_issue_resolves_in_that_repo(self):
        issues = {"repos/fullsend-ai/agents/issues/20": {"title": "bug", "body": "Introduced by commit abc1234"}}
        linked = mod.gh_linked_issue_text(self.pr_type, issues.get)
        fix = pr("fullsend", 50, "", body="Closes fullsend-ai/agents#20")
        resolve = lambda repo, sha: "agents#10" if (repo, sha) == ("agents", "abc1234") else None
        self.assertEqual(mod.detect(fix, {}, resolve, linked(fix)).get("agents#10"), "causal")
        # Same issue text in the fixing PR's own repo stays unqualified.
        self.assertEqual(mod.qualify("Introduced by commit abc1234", "fullsend"),
                         "Introduced by commit fullsend-ai/fullsend@abc1234")

    def test_lookup_failure_raises_instead_of_dropping_evidence(self):
        def fail(args):
            raise RuntimeError("gh: Bad Gateway (HTTP 502)")
        self.pr_type.gh_json = fail
        linked = mod.gh_linked_issue_text(self.pr_type)
        with self.assertRaises(RuntimeError):
            linked(pr("fullsend", 50, "", title="fix(#5): x"))
        with self.assertRaises(RuntimeError):
            mod.gh_sha_lookup(self.pr_type)("fullsend", "abc1234")

    def test_missing_issue_or_commit_is_not_an_error(self):
        for message in ("gh: Not Found (HTTP 404)", "gh: This issue was deleted (HTTP 410)"):
            def missing(args):
                raise RuntimeError(message)
            self.pr_type.gh_json = missing
            self.assertEqual(mod.gh_linked_issue_text(self.pr_type)(pr("fullsend", 50, "", title="fix(#5): x")), "")
            self.assertIsNone(mod.gh_sha_lookup(self.pr_type)("fullsend", "abc1234"))


class TestPairWindow(unittest.TestCase):
    def test_only_earlier_merged_prs_within_30_days(self):
        merged = {
            "fullsend#1": pr("fullsend", 1, "2026-06-01T00:00:00Z"),
            "fullsend#2": pr("fullsend", 2, "2026-05-01T00:00:00Z"),
            "fullsend#4": pr("fullsend", 4, "2026-07-01T00:00:00Z"),
        }
        fix = pr("fullsend", 3, "2026-06-30T12:00:00Z", body="fixes #1, #2, #4 and issue #5")
        rows = mod.pair_rows([fix], merged, no_sha)
        self.assertEqual([(r["original"], r["days_between"]) for r in rows], [("fullsend#1", "29.5")])


class TestRates(unittest.TestCase):
    def setUp(self):
        # Week of 2026-06-01 (Mon): three PRs; week of 2026-06-08 not closed by 2026-07-10.
        self.details = [
            {"date": "2026-06-01", "repo": "fullsend", "number": "1", "pr_type": "feat"},
            {"date": "2026-06-03", "repo": "fullsend", "number": "2", "pr_type": "fix"},
            {"date": "2026-06-07", "repo": "fullsend", "number": "3", "pr_type": "feat"},
            {"date": "2026-06-08", "repo": "fullsend", "number": "4", "pr_type": "feat"},
        ]
        self.candidates = [
            {"fix": "fullsend#10", "original": "fullsend#1", "days_between": "5.0"},
            {"fix": "fullsend#11", "original": "fullsend#1", "days_between": "6.0"},
            {"fix": "fullsend#12", "original": "fullsend#2", "days_between": "2.0"},
            {"fix": "fullsend#13", "original": "fullsend#3", "days_between": "1.0"},
            {"fix": "fullsend#14", "original": "fullsend#4", "days_between": "1.0"},
            {"fix": "fullsend#15", "original": "fullsend#2", "days_between": "3.0"},
            {"fix": "fullsend#16", "original": "fullsend#3", "days_between": "4.0"},
        ]
        self.judgments = [
            {"fix": "fullsend#10", "original": "fullsend#1", "verdict": "defect_from_original", "severity": "high"},
            {"fix": "fullsend#11", "original": "fullsend#1", "verdict": "defect_from_original", "severity": "low"},
            {"fix": "fullsend#12", "original": "fullsend#2", "verdict": "unknown", "severity": ""},
            {"fix": "fullsend#14", "original": "fullsend#4", "verdict": "defect_from_original", "severity": ""},
            {"fix": "fullsend#15", "original": "fullsend#2", "verdict": "later_change", "severity": ""},
            {"fix": "fullsend#16", "original": "fullsend#3", "verdict": "not_related", "severity": ""},
        ]

    def test_rate_counts_distinct_originals_and_closed_weeks_only(self):
        rows = mod.rates(self.candidates, self.judgments, self.details, date(2026, 7, 10))
        by_key = {(r["week"], r["pr_type"]): r for r in rows}
        self.assertNotIn(("2026-06-08", "all"), by_key)
        total = by_key[("2026-06-01", "all")]
        self.assertEqual(
            (total["merged_prs"], total["escaped_prs"], total["severe_prs"], total["unjudged_candidates"]),
            (3, 1, 1, 2),  # fullsend#2 unknown + fullsend#3 unjudged; later_change/not_related add nothing
        )
        self.assertEqual(total["escaped_fix_rate"], "0.3333")
        self.assertEqual(by_key[("2026-06-01", "feat")]["escaped_fix_rate"], "0.5000")
        self.assertEqual(by_key[("2026-06-01", "fix")]["escaped_fix_rate"], "0.0000")

    def test_window_closes_36_days_after_week_start(self):
        self.assertFalse(mod.rates(self.candidates, self.judgments, self.details, date(2026, 7, 6)))
        self.assertTrue(mod.rates(self.candidates, self.judgments, self.details, date(2026, 7, 7)))

    def test_sync_adds_unjudged_and_drops_stale(self):
        judgments = mod.sync_judgments(
            self.candidates[:1],
            [{"fix": "a#1", "original": "a#0", "verdict": "unjudged"},
             {"fix": "a#2", "original": "a#0", "verdict": "not_related"}],
        )
        self.assertEqual(
            [(j["fix"], j["verdict"]) for j in judgments],
            [("a#2", "not_related"), ("fullsend#10", "unjudged")],
        )


def hook(answer: object, code: int = 0) -> str:
    """A FIX_JUDGE_CMD that prints answer, writes a workflow command to stderr and exits with code."""
    script = f"import sys; sys.stdin.read(); print({json.dumps(json.dumps(answer))}); " \
             f"sys.stderr.write('::warning::x\\n'); sys.exit({code})"
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"


class TestJudge(unittest.TestCase):
    def setUp(self):
        self.candidates = [{"fix": "fullsend#10", "original": "fullsend#1"}]

    def run_judge(self, command, diff="diff --git a b"):
        judgments = [{"fix": "fullsend#10", "original": "fullsend#1", "verdict": "unjudged",
                      "severity": "", "judged_by": "", "judged_at": ""}]
        with mock.patch.dict(os.environ, {"FIX_JUDGE_CMD": command}), mock.patch.object(mod, "pr_diff", lambda key: diff):
            mod.judge(self.candidates, judgments)
        return judgments[0]

    def test_verdict_is_recorded_as_ai_with_normalized_severity(self):
        row = self.run_judge(hook({"verdict": "defect_from_original", "severity": "High", "judged_by": "Guy"}))
        self.assertEqual((row["verdict"], row["severity"], row["judged_by"]),
                         ("defect_from_original", "high", "ai (FIX_JUDGE_CMD)"))

    def test_bad_answers_leave_the_pair_unjudged(self):
        for answer in (["defect_from_original"], "x", 3, {"verdict": "defect_from_original", "severity": "critical"},
                       {"verdict": "maybe"}):
            self.assertEqual(self.run_judge(hook(answer))["verdict"], "unjudged", answer)
        self.assertEqual(self.run_judge(hook({"verdict": "not_related"}, code=1))["verdict"], "unjudged")

    def test_failed_diff_download_leaves_the_pair_unjudged(self):
        self.assertEqual(self.run_judge(hook({"verdict": "not_related"}), diff=None)["verdict"], "unjudged")

    def test_log_safe(self):
        self.assertEqual(mod.log_safe("::add-mask::secret\nline\x1b[0m"), ": :add-mask: :secret line [0m")


class TestCollect(unittest.TestCase):
    """collect() and rebuild() against temporary CSVs, with GitHub stubbed."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.paths = {name: tmp / f"{name}.csv" for name in ("CANDIDATES", "JUDGMENTS", "OUTPUT", "DETAILS", "AGGREGATE")}
        self.paths["THROUGH"] = tmp / "through.txt"
        self.patches = [mock.patch.object(mod, name, path) for name, path in self.paths.items()]
        for patch in self.patches:
            patch.start()
        self.pr_type = mod._load_pr_type()
        self.pr_type.gh_json = lambda args: None
        self.merged = [
            {"number": 1, "closedAt": "2026-06-01T10:00:00Z", "title": "feat: a", "body": ""},
            {"number": 2, "closedAt": "2026-06-05T10:00:00Z", "title": "fix: a", "body": "regression from #1"},
        ]
        self.pr_type.fetch_merged_range = lambda repo, start, end: [dict(p) for p in self.merged] if repo == "fullsend" else []
        mod.write(mod.CANDIDATES, mod.CANDIDATE_HEADER, [
            {"fix": "fullsend#9", "original": "fullsend#8", "detected_by": "named", "days_between": "1.0",
             "fix_merged_at": "2026-06-05T01:00:00Z", "original_merged_at": "2026-06-04T01:00:00Z"},  # replaced
            {"fix": "fullsend#7", "original": "fullsend#6", "detected_by": "named", "days_between": "1.0",
             "fix_merged_at": "2026-05-20T01:00:00Z", "original_merged_at": "2026-05-19T01:00:00Z"},  # kept
        ])
        self.loader = mock.patch.object(mod, "_load_pr_type", lambda: self.pr_type)
        self.loader.start()

    def tearDown(self):
        for patch in self.patches + [self.loader]:
            patch.stop()

    def test_replaces_only_rows_in_range_and_advances_through(self):
        mod.THROUGH.write_text("2026-06-04\n")
        mod.collect(date(2026, 6, 5), date(2026, 6, 6))
        self.assertEqual([(r["fix"], r["original"]) for r in mod.read(mod.CANDIDATES)],
                         [("fullsend#7", "fullsend#6"), ("fullsend#2", "fullsend#1")])
        self.assertEqual(mod.read_through(), date(2026, 6, 6))

    def test_a_gap_does_not_advance_through(self):
        mod.THROUGH.write_text("2026-06-01\n")
        mod.collect(date(2026, 6, 5), date(2026, 6, 6))
        self.assertEqual(mod.read_through(), date(2026, 6, 1))

    def test_file_untouched_when_a_lookup_raises(self):
        def fail(args):
            raise RuntimeError("gh: Bad Gateway (HTTP 502)")
        self.pr_type.gh_json = fail
        self.merged[1]["title"] = "fix(#5): a"  # closes an issue, so it is looked up
        before = mod.CANDIDATES.read_text()
        with self.assertRaises(RuntimeError):
            mod.collect(date(2026, 6, 5), date(2026, 6, 5))
        self.assertEqual(mod.CANDIDATES.read_text(), before)
        self.assertFalse(mod.THROUGH.exists())

    def test_weeks_close_by_the_collected_date_not_the_pr_type_date(self):
        mod.write(mod.AGGREGATE, ["date"], [{"date": "2026-08-01"}])
        mod.write(mod.DETAILS, ["date", "repo", "number", "pr_type"],
                  [{"date": "2026-06-01", "repo": "fullsend", "number": "1", "pr_type": "feat"}])
        mod.THROUGH.write_text("2026-07-06\n")  # one day short of the week of 2026-06-01 closing
        mod.rebuild()
        self.assertEqual(mod.read(mod.OUTPUT), [])
        mod.THROUGH.write_text("2026-07-07\n")
        mod.rebuild()
        self.assertEqual([r["week"] for r in mod.read(mod.OUTPUT) if r["pr_type"] == "all"], ["2026-06-01"])


if __name__ == "__main__":
    unittest.main()
