#!/usr/bin/env python3
"""Unit tests for collect-fix-likelihood helpers (no GitHub calls)."""
from __future__ import annotations

import importlib.util
import unittest
from datetime import date, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "collect_fix_likelihood", Path(__file__).parent / "collect-fix-likelihood.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def history(repo="fullsend", weeks=6, per_week=10, title="fix: x"):
    """per_week PRs a week, sizes 1..weeks*per_week, all before 2026-06-01."""
    start = date(2026, 6, 1) - timedelta(weeks=weeks)
    details, sizes = [], {}
    for n in range(weeks * per_week):
        number = n + 1
        details.append({"date": (start + timedelta(weeks=n // per_week)).isoformat(),
                        "repo": repo, "number": str(number), "title": title})
        sizes[f"{repo}#{number}"] = number
    return details, sizes


class TestBands(unittest.TestCase):
    def test_too_little_history_is_unable_to_assess(self):
        details, sizes = history(weeks=3)  # 30 PRs, under MIN_HISTORY
        banded = mod.bands(details, sizes)
        self.assertEqual({b for b, _ in banded.values()}, {"unable_to_assess"})

    def test_size_third_and_riskier_type(self):
        details, sizes = history()  # 60 earlier PRs of size 1..60: cuts at about 20 and 40
        new = [("small-feat", 5, "feat: x"), ("big-fix", 55, "fix(api): x"), ("big-feat", 55, "feat(ui)!: x"),
               ("mid-fix", 30, "fix: x"), ("big-untyped", 55, "Add a thing"), ("big-refactor", 55, "refactor: x"),
               ("big-build", 55, "build(deps): x")]
        for i, (_, size, title) in enumerate(new):
            details.append({"date": "2026-06-01", "repo": "fullsend", "number": str(100 + i), "title": title})
            sizes[f"fullsend#{100 + i}"] = size
        banded = mod.bands(details, sizes)
        got = {name: banded[f"fullsend#{100 + i}"][0] for i, (name, _, _) in enumerate(new)}
        self.assertEqual(got, {"small-feat": "middle", "big-fix": "middle", "big-feat": "higher",
                               "mid-fix": "lower", "big-untyped": "higher", "big-refactor": "middle",
                               "big-build": "higher"})

    def test_same_week_prs_are_not_their_own_history(self):
        details, sizes = history(weeks=5)  # exactly MIN_HISTORY earlier PRs
        details.append({"date": "2026-06-01", "repo": "fullsend", "number": "900", "title": "fix: x"})
        details.append({"date": "2026-06-02", "repo": "fullsend", "number": "901", "title": "fix: x"})
        sizes["fullsend#900"] = sizes["fullsend#901"] = 1000
        banded = mod.bands(details, sizes)
        self.assertEqual(banded["fullsend#900"], banded["fullsend#901"])
        self.assertEqual(banded["fullsend#900"][1], 1.0)

    def test_repos_have_their_own_thirds(self):
        details, sizes = history(repo="fullsend")
        other, other_sizes = history(repo="agents")
        details += other
        sizes.update({k: v * 100 for k, v in other_sizes.items()})
        details.append({"date": "2026-06-01", "repo": "agents", "number": "999", "title": "feat: x"})
        sizes["agents#999"] = 500  # big in fullsend's terms, small in agents'
        self.assertEqual(mod.bands(details, sizes)["agents#999"][0], "middle")


class TestOutcomes(unittest.TestCase):
    def test_medium_plus_within_window_and_open_pairs(self):
        candidates = [
            {"fix": "fullsend#10", "original": "fullsend#1", "days_between": "3"},
            {"fix": "fullsend#11", "original": "fullsend#2", "days_between": "3"},
            {"fix": "fullsend#12", "original": "fullsend#3", "days_between": "3"},
            {"fix": "fullsend#13", "original": "fullsend#4", "days_between": "40"},
            {"fix": "fullsend#14", "original": "fullsend#5", "days_between": "3"},
        ]
        judgments = [
            {"fix": "fullsend#10", "original": "fullsend#1", "verdict": "defect_from_original", "severity": "medium"},
            {"fix": "fullsend#11", "original": "fullsend#2", "verdict": "defect_from_original", "severity": "low"},
            {"fix": "fullsend#12", "original": "fullsend#3", "verdict": "unknown", "severity": ""},
            {"fix": "fullsend#13", "original": "fullsend#4", "verdict": "defect_from_original", "severity": "high"},
        ]
        fixed, open_pairs = mod.outcomes(candidates, judgments)
        self.assertEqual(fixed, {"fullsend#1"})
        self.assertEqual(dict(open_pairs), {"fullsend#3": 1, "fullsend#5": 1})


class TestCheck(unittest.TestCase):
    def test_auc_with_ties(self):
        self.assertEqual(mod.auc([1, 2, 3, 4], [False, False, True, True]), 1.0)
        self.assertEqual(mod.auc([1, 1, 1, 1], [False, True, False, True]), 0.5)
        self.assertEqual(mod.auc([4, 3, 2, 1], [False, False, True, True]), 0.0)

    def test_too_few_fixes(self):
        row = mod.check("agents", [(0.5, True)] * 3 + [(0.1, False)] * 50, 2)
        self.assertEqual((row["auc"], row["fixed_in_riskiest_30pct"], row["left_out_open"]),
                         ("too_few_fixes", "too_few_fixes", 2))

    def test_riskiest_30pct(self):
        prs = [(2.0, True)] * 20 + [(0.0, False)] * 80 + [(0.0, True)] * 5
        row = mod.check("all", prs, 0)
        self.assertEqual(row["fixed_in_riskiest_30pct"], "0.800")  # 20 of 25 fixed PRs are in the top 32


if __name__ == "__main__":
    unittest.main()
