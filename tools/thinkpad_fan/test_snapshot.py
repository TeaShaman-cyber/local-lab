import json
import tempfile
import unittest
from pathlib import Path

from snapshot import export_file


class SnapshotTest(unittest.TestCase):
    def test_append_restart_and_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"test.txt"
            spool=Path(tmp)/"spool"
            p.write_bytes(b"first\npartial")
            self.assertEqual(export_file(p,spool,"samples"),1)
            self.assertEqual(export_file(p,spool,"samples"),0)
            with p.open("ab") as f:
                f.write(b" line\n")
            self.assertEqual(export_file(p,spool,"samples"),1)
            rows=[json.loads(line)["raw"] for batch in sorted(spool.glob("*.jsonl")) for line in batch.read_text().splitlines()]
            self.assertEqual(rows,["first","partial line"])

    def test_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"test.txt"
            spool=Path(tmp)/"spool"
            p.write_bytes(b"first\n")
            self.assertEqual(export_file(p,spool,"samples"),1)
            p.rename(p.with_suffix(".old"))
            p.write_bytes(b"second\n")
            self.assertEqual(export_file(p,spool,"samples"),1)
            self.assertEqual(export_file(p,spool,"samples"),0)

    def test_truncate(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"test.txt"
            spool=Path(tmp)/"spool"
            p.write_bytes(b"old long line\n")
            self.assertEqual(export_file(p,spool,"samples"),1)
            p.write_bytes(b"new\n")
            self.assertEqual(export_file(p,spool,"samples"),1)
            self.assertEqual(export_file(p,spool,"samples"),0)
