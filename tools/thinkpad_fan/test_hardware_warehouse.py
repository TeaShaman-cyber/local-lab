import unittest

from hardware_warehouse import event_rows, parse_polls, room, utc_ns


class WarehouseTest(unittest.TestCase):
    def test_budget_reserves_headroom(self):
        self.assertTrue(room(100, 1000, 500, 200, 100))
        self.assertFalse(room(450, 1000, 500, 200, 100))
        self.assertFalse(room(100, 250, 500, 200, 100))
    def test_timestamp_preserves_submicroseconds(self):
        self.assertEqual(utc_ns("1970-01-01T00:00:01.123456789Z"),1123456789)
    def test_event_identity_survives_id_reuse(self):
        def xml(t):
            return f'<Events><Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="Netwtw06"/><EventID>6000</EventID><TimeCreated SystemTime="{t}"/><EventRecordID>1</EventRecordID><Channel>System</Channel></System></Event></Events>'
        a=event_rows(xml("2026-11-28T08:00:00.1234567Z"),"System")
        b=event_rows(xml("2026-11-28T08:00:01.1234567Z"),"System")
        self.assertEqual(a,event_rows(xml("2026-11-28T08:00:00.1234567Z"),"System"))
        self.assertNotEqual(a[0][0],b[0][0])
        self.assertEqual(a[0][3],"Netwtw06")
    def test_poll_gap_and_partial_tail(self):
        def row(n):return b"[date] EcPair full "+b",".join([str(n).encode()]+[b"0"]*30)+b"\n"
        self.assertEqual([r[0] for r in parse_polls(row(1)+row(3)+row(4)[:-1])],[1,3])
        with self.assertRaises(ValueError):
            parse_polls(row(1)+row(1))

    def test_task_is_bounded_and_does_not_wake(self):
        import xml.etree.ElementTree as ET

        from hardware_task import NS, task_xml
        root=ET.fromstring(task_xml("S-1-5-21-1","2026-11-28T12:00:00"))
        def get(n):
            return root.findtext(".//{"+NS+"}"+n)
        self.assertEqual(get("Interval"),"PT5M")
        self.assertEqual(get("MultipleInstancesPolicy"),"IgnoreNew")
        self.assertEqual(get("WakeToRun"),"false")
        self.assertEqual(get("ExecutionTimeLimit"),"PT10M")
        self.assertIn("--seconds 240",get("Arguments"))
