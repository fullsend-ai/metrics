#!/usr/bin/env python3
"""Unit tests for collect-trust-rollup helpers (no I/O)."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "collect_trust_rollup", Path(__file__).parent / "collect-trust-rollup.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)

rollup = mod.rollup
_mean = mod._mean
load_scorecards = mod.load_scorecards


def _card(repo, role, when, statuses, scores=None, blocking=None, decision="sufficient"):
    scores = scores or {}
    evidence = {}
    for name, status in statuses.items():
        sig = {"status": status}
        if name in scores:
            sig["score"] = scores[name]
        evidence[name] = sig
    return {
        "subject": {"repo": repo, "agent_role": role, "generated_at": when},
        "evidence": evidence,
        "composition": {"decision": decision, "blocking": blocking or []},
    }


class TestMean(unittest.TestCase):
    def test_ignores_non_numeric_and_bool(self):
        self.assertEqual(_mean([1.0, None, "x", True, 3.0]), 2.0)

    def test_empty_is_blank(self):
        self.assertEqual(_mean([None, "x"]), "")


class TestLoadScorecards(unittest.TestCase):
    def _load(self, payload):
        import json, tempfile, os
        fd, path = tempfile.mkstemp(suffix=".json")
        os.write(fd, json.dumps(payload).encode())
        os.close(fd)
        try:
            return load_scorecards(Path(path))
        finally:
            os.unlink(path)

    def test_dict_wrapper(self):
        self.assertEqual(self._load({"scorecards": [{"a": 1}]}), [{"a": 1}])

    def test_bare_list(self):
        self.assertEqual(self._load([{"a": 1}]), [{"a": 1}])

    def test_dict_without_scorecards_key_rejected(self):
        # An unexpected object shape must fail loudly, not roll up zero cards.
        with self.assertRaises(ValueError):
            self._load({"cards": [{"a": 1}]})


class TestRollup(unittest.TestCase):
    def setUp(self):
        self.cards = [
            _card("o/a", "review", "2026-08-20T00:00:00Z",
                  {"config_health": "pass", "behavioral_eval": "pass",
                   "audit_integrity": "pass", "track_record": "partial", "drift": "pass"},
                  scores={"config_health": 0.94, "behavioral_eval": 0.88},
                  blocking=["track_record"], decision="insufficient"),
            _card("o/a", "review", "2026-08-24T00:00:00Z",
                  {"config_health": "pass", "behavioral_eval": "pass",
                   "audit_integrity": "pass", "track_record": "pass", "drift": "pass"},
                  scores={"config_health": 0.96, "behavioral_eval": 0.90},
                  blocking=[], decision="sufficient"),
        ]

    def test_one_row_per_group(self):
        rows = rollup(self.cards)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["repo"], rows[0]["agent_role"]), ("o/a", "review"))
        self.assertEqual(rows[0]["scorecards"], 2)

    def test_latest_decision_uses_most_recent(self):
        rows = rollup(self.cards)
        self.assertEqual(rows[0]["latest_decision"], "sufficient")

    def test_pass_rate_over_all_signals(self):
        # 5 + 5 signals, one partial -> 9/10.
        rows = rollup(self.cards)
        self.assertEqual(rows[0]["signal_pass_rate"], 0.9)

    def test_avg_scores(self):
        rows = rollup(self.cards)
        self.assertEqual(rows[0]["avg_config_health"], 0.95)
        self.assertEqual(rows[0]["avg_behavioral_eval"], 0.89)

    def test_blocking_union_sorted(self):
        rows = rollup(self.cards)
        self.assertEqual(rows[0]["blocking_signals"], "track_record")

    def test_latest_respects_utc_offsets(self):
        # "23:00+05:00" (18:00Z) sorts after "20:00Z" as raw text but is
        # actually earlier; the parsed-instant comparison must pick 20:00Z.
        cards = [
            _card("o/c", "code", "2026-08-24T23:00:00+05:00",
                  {"config_health": "pass"}, decision="earlier"),
            _card("o/c", "code", "2026-08-24T20:00:00Z",
                  {"config_health": "pass"}, decision="latest"),
        ]
        rows = rollup(cards)
        self.assertEqual(rows[0]["latest_decision"], "latest")

    def test_groups_split_by_role_and_repo(self):
        cards = self.cards + [_card("o/b", "triage", "2026-08-24T00:00:00Z",
                                    {"config_health": "pass"})]
        rows = rollup(cards)
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
