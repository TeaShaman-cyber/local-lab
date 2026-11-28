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

    def test_single_digit_hour_and_midnight(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"test.txt"
            spool = Path(tmp)/"spool"
            p.write_bytes(b"23:59:59;70\n0:00:00;71\n9:12:01;72\n09:12:02;73\nheader\n")
            self.assertEqual(export_file(p, spool, "csv"), 4)
            rows = [json.loads(line)["raw"] for batch in spool.glob("*.jsonl") for line in batch.read_text().splitlines()]
            self.assertEqual(rows, ["23:59:59;70", "0:00:00;71", "9:12:01;72", "09:12:02;73"])
            self.assertEqual(export_file(p, spool, "csv"), 0)

    def test_temperature_shadow_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"test.log"
            spool = Path(tmp)/"spool"
            lines = [
                "[28.11.2026 9:12:04] EcPair tick=62446640 a=09d1/66 obf=1,1,1 b=09d0/65 obf=1,1,1",
                "[28.11.2026 9:12:04] EcTempShadow tick=62446640 value=66 source=A quality=dual",
                "[28.11.2026 9:12:05] EcTempShadow tick=62447640 value=-1 source=none quality=unknown",
                "[28.11.2026 9:12:06] EcTempShadowOther private arbitrary line",
                "[28.11.2026 9:12:07] unrelated diagnostic",
            ]
            p.write_bytes(("\n".join(lines)+"\n").encode())
            self.assertEqual(export_file(p, spool, "events"), 3)
            rows = [json.loads(line)["raw"] for batch in spool.glob("*.jsonl") for line in batch.read_text().splitlines()]
            self.assertEqual(rows, lines[:3])
            self.assertEqual(export_file(p, spool, "events"), 0)
