#!/usr/bin/env python3
"""Shell collectors skip non-public repos (stubbed gh, no GitHub calls).

A fake `gh` on PATH serves fixture JSON and applies the script's own --jq
filter with jq, so the filters in lib.sh and the rework scripts are exercised
as written.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
DAY = "2026-11-10"

ORG_REPOS = [
    {"name": "pub", "visibility": "public", "archived": False, "fork": False},
    {"name": "priv", "visibility": "private", "archived": False, "fork": False},
    {"name": "internal", "visibility": "internal", "archived": False, "fork": False},
    {"name": "old", "visibility": "public", "archived": True, "fork": False},
    {"name": "forked", "visibility": "public", "archived": False, "fork": True},
    {"name": "unknown", "archived": False, "fork": False},
]


def board_item(repo, visibility, number):
    content = {
        "number": number,
        "repository": {"name": repo, "visibility": visibility},
        "url": f"https://github.com/fullsend-ai/{repo}/issues/{number}",
        "timelineItems": {"nodes": [
            {"createdAt": f"{DAY}T12:00:00Z", "actor": {"login": "agent[bot]", "type": "Bot"}},
        ]},
    }
    return {"updatedAt": f"{DAY}T12:00:00Z", "content": content}


BOARD = {"data": {"organization": {"projectsV2": {"nodes": [{"items": {"nodes": [
    board_item("pub", "PUBLIC", 1),
    board_item("priv", "PRIVATE", 2),
    board_item("internal", "INTERNAL", 3),
    {"updatedAt": f"{DAY}T12:00:00Z", "content": {}},  # draft issue
]}}]}}}}

FAKE_GH = """#!/usr/bin/env bash
filter="" fixture="$GH_FIXTURES/other.json"
while (( $# )); do
  case "$1" in
    --jq) filter="$2"; shift ;;
    graphql) fixture="$GH_FIXTURES/graphql.json" ;;
    /orgs/*/repos) fixture="$GH_FIXTURES/repos.json" ;;
  esac
  shift
done
jq -r "${filter:-.}" "$fixture"
"""


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "needs bash and jq")
class TestShellVisibility(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        fixtures = self.tmp / "fixtures"
        fixtures.mkdir()
        (fixtures / "repos.json").write_text(json.dumps(ORG_REPOS))
        (fixtures / "graphql.json").write_text(json.dumps(BOARD))
        (fixtures / "other.json").write_text(json.dumps({"items": []}))
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GH_FIXTURES": str(fixtures),
        }

    def run_bash(self, *args):
        return subprocess.run(
            ["bash", *args], cwd=self.work, env=self.env,
            capture_output=True, text=True, check=True,
        ).stdout

    def detail_repos(self):
        lines = (self.work / "docs" / "rework-details.csv").read_text().splitlines()[1:]
        return sorted(line.split(",")[2] for line in lines)

    def test_list_repos_keeps_only_public(self):
        out = self.run_bash("-c", f'source "{SCRIPTS}/lib.sh"; list_repos')
        self.assertEqual(out.split(), ["pub"])

    def test_collect_rework_drops_non_public_board_items(self):
        self.run_bash(str(SCRIPTS / "collect-rework.sh"), DAY)
        self.assertEqual(self.detail_repos(), ["pub"])

    def test_backfill_rework_drops_non_public_board_items(self):
        self.run_bash(str(SCRIPTS / "backfill-rework.sh"), DAY, DAY)
        self.assertEqual(self.detail_repos(), ["pub"])


if __name__ == "__main__":
    unittest.main()
