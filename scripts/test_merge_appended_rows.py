#!/usr/bin/env python3
"""Tests for merge_appended_rows in lib.sh (no GitHub calls)."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).parent / "lib.sh"
LOCALES = ("C", "C.UTF-8", "en_US.UTF-8")

HEADER = "date,bot,items_touched,items_reworked,rework_rate"
# Already in C order, with keys that collate differently under en_US.UTF-8
# (upper case before lower case, "_" and "." prefixes).
EXISTING = [
    "2026-05-01,Zed[bot],1,0,0.000",
    "2026-05-01,__aggregate__,2,0,0.000",
    "2026-05-01,fullsend-ai-code[bot],1,0,0.000",
    "2026-05-04,Zed[bot],1,1,1.000",
    "2026-05-04,__aggregate__,1,1,1.000",
]
# Appended in collection order (not sorted), as a backfill writes them.
NEW = [
    "2026-05-03,fullsend-ai-code[bot],2,1,0.500",
    "2026-05-03,__aggregate__,3,1,0.333",
    "2026-05-02,__aggregate__,1,0,0.000",
    "2026-05-02,Zed[bot],1,0,0.000",
    "2026-05-03,Zed[bot],1,0,0.000",
]


def merge(lines: list[str], existing_lines: int, *keys: str, locale: str = "C") -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "data.csv"
        path.write_text("".join(f"{line}\n" for line in lines))
        env = dict(os.environ, LC_ALL=locale, LANG=locale)
        subprocess.run(
            ["bash", "-c", 'source "$0"; merge_appended_rows "$@"', str(LIB), str(path), str(existing_lines), *keys],
            check=True,
            env=env,
            cwd=tmp,
        )
        return path.read_text().splitlines()


class TestMergeAppendedRows(unittest.TestCase):
    def test_existing_rows_keep_positions(self):
        out = merge([HEADER, *EXISTING, *NEW], 1 + len(EXISTING), "-k1,1", "-k2,2")
        self.assertEqual(
            out,
            [
                HEADER,
                *EXISTING[:3],
                "2026-05-02,Zed[bot],1,0,0.000",
                "2026-05-02,__aggregate__,1,0,0.000",
                "2026-05-03,Zed[bot],1,0,0.000",
                "2026-05-03,__aggregate__,3,1,0.333",
                "2026-05-03,fullsend-ai-code[bot],2,1,0.500",
                *EXISTING[3:],
            ],
        )
        self.assertEqual([line for line in out if line in EXISTING], EXISTING)

    def test_same_result_in_every_locale(self):
        results = {loc: merge([HEADER, *EXISTING, *NEW], 1 + len(EXISTING), "-k1,1", "-k2,2", locale=loc) for loc in LOCALES}
        self.assertEqual(len({tuple(r) for r in results.values()}), 1, results)

    def test_no_new_rows_is_a_no_op(self):
        lines = [HEADER, *EXISTING, *NEW]
        first = merge(lines, 1 + len(EXISTING), "-k1,1", "-k2,2")
        self.assertEqual(merge(first, len(first), "-k1,1", "-k2,2"), first)

    def test_unsorted_existing_rows_do_not_move(self):
        existing = ["2026-05-01,b,x", "2026-05-01,a,x", "2026-05-03,a,x"]
        out = merge(["date,repo,v", *existing, "2026-05-02,a,new"], 4, "-k1,1", "-k2,2")
        self.assertEqual(out, ["date,repo,v", *existing[:2], "2026-05-02,a,new", existing[2]])

    def test_date_key_is_stable(self):
        existing = ["2026-05-01,z,old", "2026-05-02,b,old"]
        new = ["2026-05-02,y,new", "2026-05-01,c,new", "2026-05-02,a,new"]
        out = merge(["date,repo,v", *existing, *new], 3, "-k1,1")
        self.assertEqual(
            out,
            ["date,repo,v", "2026-05-01,z,old", "2026-05-01,c,new", "2026-05-02,b,old", "2026-05-02,y,new", "2026-05-02,a,new"],
        )

    def test_header_only_file(self):
        out = merge(["date,repo,v", "2026-05-02,b,x", "2026-05-01,a,x"], 1, "-k1,1", "-k2,2")
        self.assertEqual(out, ["date,repo,v", "2026-05-01,a,x", "2026-05-02,b,x"])


if __name__ == "__main__":
    unittest.main()
