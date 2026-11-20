"""Per-process disk I/O diagnostics for issue #274.

Why: C: showed ~98% active time with under 2 MB/s, i.e. many small
operations. Task Manager and windows_exporter show volume totals, not the
process issuing them. On Windows there are two honest ways to attribute this:

1. Read-only, no elevation: `GetProcessIoCounters` via `process_io.py`.
   Cumulative read/write/other operation counts and bytes, per process.
   Two samples a few seconds apart give ops/s and bytes/op. It cannot name
   files, stacks or volumes and skips protected processes. It runs anywhere.

2. Elevated, first-party ETW: WPR/WPA with the DiskIO/DiskIOInit/Filename
   keywords and DiskReadInit/DiskWriteInit/DiskFlushInit stacks. This gives
   issuing process, file path and call stack. Requires administrator, and
   writes a temporary ETL file (keep it off C: while that disk is hot).

Example (no elevation):
    python tools/nvme_diagnose/process_io.py --seconds 8 --top 20
    python tools/nvme_diagnose/process_io.py --seconds 8 --sort avg --json

Interpretation: a process with high ops/s and low bytes/s is the candidate
for small-operation churn. Compare with volume totals in Grafana:
    windows_logical_disk_reads_total{host="theseus-laptop",volume="C:"}
    windows_logical_disk_writes_total{host="theseus-laptop",volume="C:"}
    ohm_hdd_celsius{host="theseus-laptop",hardware="SAMSUNG MZALQ512HALU-000L1"}

Elevated capture (run from administrative PowerShell, ETL to non-system disk):
    powershell -ExecutionPolicy Bypass -File tools/nvme_diagnose/capture.ps1 -RunDir E:\\diagnostics\\nvme-274\\run01
Then open the ETL with Windows Performance Analyzer and use Disk Usage /
File I/O views. The profile and script are checked by tests for structure
only; a live capture is an operator action, not part of CI.
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


class ProfileStructureTests(unittest.TestCase):
    def test_wpr_profile_declares_needed_keywords(self):
        profile = Path(__file__).with_name("nvme-disk.wprp")
        root = ET.parse(profile).getroot()
        self.assertEqual(root.tag, "WindowsPerformanceRecorder")
        keywords = {k.get("Value") for k in root.iter("Keyword")}
        self.assertTrue({"DiskIO", "DiskIOInit", "Filename", "FileIO"}.issubset(keywords))
        stacks = {s.get("Value") for s in root.iter("Stack")}
        self.assertIn("DiskReadInit", stacks)
        profiles = {p.get("Id") for p in root.iter("Profile")}
        self.assertIn("NvmeDisk.Verbose.Memory", profiles)

    def test_capture_script_references_only_own_profile_and_validates_run_dir(self):
        script = Path(__file__).with_name("capture.ps1").read_text(encoding="utf-8")
        self.assertIn("'nvme-disk.wprp') + '!NvmeDisk'", script)
        self.assertIn("-skipPdbGen", script)
        self.assertIn("nvme-274", script)
        self.assertIn("-instancename", script)
        self.assertIn("-cancel", script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
