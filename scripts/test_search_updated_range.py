#!/usr/bin/env python3
"""Unit tests for search_updated_range in lib.sh (fake gh, no GitHub calls)."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from datetime import date, timedelta
from pathlib import Path

LIB = Path(__file__).parent / "lib.sh"

# Stands in for `gh api /search/issues`. Items come from FAKE_ITEMS as
# [number, [updated timestamps...]]; an item matches a window if any of its
# timestamps falls in it. Like the real API, paginated results stop at 1000.
FAKE_GH = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, re, sys
    args = sys.argv[1:]
    fields = {}
    for i, a in enumerate(args):
        if a == "-f":
            k, v = args[i + 1].split("=", 1)
            fields[k] = v
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps(fields) + "\\n")
    start, end = re.search(r"updated:(\\S+)\\.\\.(\\S+)", fields["q"]).groups()
    items = [n for n, dates in json.loads(os.environ["FAKE_ITEMS"])
             if any(start <= d <= end for d in dates)]
    if fields.get("per_page") == "1":
        print(len(items))
    else:
        for n in items[:1000]:
            print(f"{n}\\thttps://github.com/o/r/issues/{n}")
    """
)


def spread(start: date, days: int, per_day: int, first: int = 1) -> list:
    """per_day items a day, one a minute from 00:00:00Z."""
    items, n = [], first
    for d in range(days):
        day = (start + timedelta(days=d)).isoformat()
        for i in range(per_day):
            items.append([n, [f"{day}T{i // 60:02d}:{i % 60:02d}:00Z"]])
            n += 1
    return items


class TestSearchUpdatedRange(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.log = Path(self.tmp.name) / "log"
        self.log.touch()
        self.path = f"{bindir}{os.pathsep}{os.environ['PATH']}"

    def tearDown(self):
        self.tmp.cleanup()

    def run_search(self, items, start, end):
        env = dict(os.environ, PATH=self.path, FAKE_ITEMS=json.dumps(items),
                   FAKE_LOG=str(self.log))
        return subprocess.run(
            ["bash", "-c", f'source "{LIB}"; search_updated_range o/r {start} {end}'],
            env=env, capture_output=True, text=True, timeout=60,
        )

    def fetched_windows(self):
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [c["q"] for c in calls if c.get("per_page") != "1"]

    def test_small_range_is_one_search(self):
        items = spread(date(2026, 10, 1), 3, 5)
        r = self.run_search(items, "2026-10-01", "2026-10-03")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.stdout.splitlines()), 15)
        self.assertEqual(
            self.fetched_windows(),
            ["repo:o/r updated:2026-10-01T00:00:00Z..2026-10-03T23:59:59Z"],
        )

    def test_range_over_cap_is_split_and_complete(self):
        items = spread(date(2026, 10, 1), 10, 150)  # 1500 items
        r = self.run_search(items, "2026-10-01", "2026-10-10")
        self.assertEqual(r.returncode, 0, r.stderr)
        numbers = [int(line.split("\t")[0]) for line in r.stdout.splitlines()]
        self.assertEqual(sorted(numbers), list(range(1, 1501)))
        self.assertGreater(len(self.fetched_windows()), 1)

    def test_item_in_two_windows_is_printed_once(self):
        items = spread(date(2026, 10, 1), 10, 150)
        items.append([9999, ["2026-10-01T23:00:00Z", "2026-10-10T23:00:00Z"]])
        r = self.run_search(items, "2026-10-01", "2026-10-10")
        self.assertEqual(r.returncode, 0, r.stderr)
        numbers = [line.split("\t")[0] for line in r.stdout.splitlines()]
        self.assertEqual(numbers.count("9999"), 1)
        self.assertEqual(len(numbers), 1501)

    def test_one_day_over_cap_is_split_by_time(self):
        items = spread(date(2026, 10, 1), 1, 1200)  # one a minute for 20 hours
        r = self.run_search(items, "2026-09-25", "2026-10-02")
        self.assertEqual(r.returncode, 0, r.stderr)
        numbers = [int(line.split("\t")[0]) for line in r.stdout.splitlines()]
        self.assertEqual(sorted(numbers), list(range(1, 1201)))

    def test_one_second_at_cap_fails(self):
        items = [[n, ["2026-10-01T12:00:00Z"]] for n in range(1, 1001)]
        items += spread(date(2026, 10, 2), 1, 5, first=2000)
        r = self.run_search(items, "2026-10-01", "2026-10-02")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("search cap exceeded", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_empty_range(self):
        r = self.run_search([], "2026-10-01", "2026-10-05")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")
        self.assertEqual(self.fetched_windows(), [])


if __name__ == "__main__":
    unittest.main()
