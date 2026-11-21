"""Tests for I/O delta math (pure, no Windows API required).

Example input/output: before {1: 100 ops, 10KB}, after {1: 110 ops, 11KB},
2s -> 5 ops/s, 512 B/s, 102.4 B/op. A process present only in the second
sample, a renamed PID, and a counter that went backwards are all skipped.
"""

from __future__ import annotations

import unittest

from tools.nvme_diagnose.process_io import _delta


def snap(pid, name, ops, op_bytes):
    return {
        pid: {
            "name": name,
            "read_ops": ops,
            "write_ops": 0,
            "other_ops": 0,
            "read_bytes": op_bytes,
            "write_bytes": 0,
            "other_bytes": 0,
        }
    }


class ProcessIoDeltaTests(unittest.TestCase):
    def test_subtracts_two_samples(self):
        before = snap(1, "a.exe", 100, 10 * 1024)
        after = snap(1, "a.exe", 110, 11 * 1024)
        rows = _delta(before, after, 2.0)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["total_ops"], 10)
        self.assertEqual(row["total_bytes"], 1024)
        self.assertEqual(row["ops_per_s"], 5.0)
        self.assertEqual(row["bytes_per_s"], 512.0)
        self.assertEqual(row["avg_bytes_per_op"], 102.4)

    def test_skips_new_renamed_and_wrapped(self):
        before = snap(1, "a.exe", 100, 1000)
        before.update(snap(2, "gone.exe", 5, 500))
        before.update(snap(3, "same.exe", 10, 100))
        after = snap(1, "a.exe", 110, 1100)
        after.update(snap(4, "new.exe", 7, 700))
        after.update(snap(3, "renamed.exe", 12, 120))
        after.update(snap(5, "wrapped.exe", 1, 10))
        before[5] = {
            "name": "wrapped.exe",
            "read_ops": 9,
            "write_ops": 0,
            "other_ops": 0,
            "read_bytes": 90,
            "write_bytes": 0,
            "other_bytes": 0,
        }
        rows = _delta(before, after, 1.0)
        self.assertEqual([r["pid"] for r in rows], [1])

    def test_zero_operations_have_zero_average(self):
        before = snap(1, "idle.exe", 7, 70)
        after = snap(1, "idle.exe", 7, 70)
        row = _delta(before, after, 1.0)[0]
        self.assertEqual(row["avg_bytes_per_op"], 0.0)
        self.assertEqual(row["ops_per_s"], 0.0)

    def test_zero_seconds_never_divides_by_zero(self):
        row = _delta(snap(1, "a", 1, 1), snap(1, "a", 2, 2), 0.0)[0]
        self.assertEqual(row["ops_per_s"], 0.0)
        self.assertEqual(row["bytes_per_s"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
