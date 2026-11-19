from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.state_db_backup.verify import (
    RECEIPT_VERSION,
    build_receipt,
    write_receipt,
)


class VerificationReceiptTests(unittest.TestCase):
    def test_receipt_binds_snapshot_bytes_and_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "state.db"
            snapshot.write_bytes(b"verified snapshot")
            result = {
                "ok": True,
                "checked": "2026-11-19T20:00:00",
                "integrity": "ok",
                "messages": 2,
                "sessions": 1,
                "newest": 1001.0,
                "gap_messages": 0,
                "gap_hours": 0.0,
                "detail": "snapshot is usable",
            }
            receipt = build_receipt(snapshot, result)
            self.assertEqual(receipt["receipt_version"], RECEIPT_VERSION)
            self.assertEqual(receipt["source"]["snapshot_name"], "state.db")
            self.assertEqual(
                receipt["source"]["snapshot_sha256"], hashlib.sha256(snapshot.read_bytes()).hexdigest()
            )
            self.assertEqual(receipt["verification"]["messages"], 2)
            self.assertTrue(receipt["verification"]["ok"])

            path = Path(tmp) / "state.db.receipt.json"
            write_receipt(path, receipt)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), receipt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
