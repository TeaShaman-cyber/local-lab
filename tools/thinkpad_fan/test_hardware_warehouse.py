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

    def test_task_uses_gui_interpreter(self):
        import xml.etree.ElementTree as ET

        from hardware_task import NS, task_xml
        root=ET.fromstring(task_xml("S-1-5-21-1","2026-11-28T12:00:00"))
        self.assertTrue(root.findtext(".//{"+NS+"}Command").endswith("pythonw.exe"))

    def test_native_children_are_windowless(self):
        from unittest.mock import patch

        import hardware_warehouse as h
        with patch.object(h.subprocess,"CREATE_NO_WINDOW",0x08000000,create=True), patch.object(h.subprocess,"run") as run:
            h.command(["logman","query","-ets"])
            self.assertEqual(run.call_args.kwargs["creationflags"],0x08000000)

    def test_snapshot_pointer_is_safe(self):
        from hardware_snapshot import generation_name
        self.assertEqual(generation_name(42),"generation-42")
        for bad in [-1,0,"../bad",True]:
            with self.assertRaises(ValueError):
                generation_name(bad)

    def test_portable_snapshot_has_no_raw_payload(self):
        from hardware_snapshot import TABLES
        self.assertNotIn("event,",TABLES["events"])
        self.assertNotIn("xml",TABLES["windows_events"])
        self.assertIn("timestamp_ns",TABLES["events"])
        self.assertIn("run_id",TABLES["polls"])

    def test_snapshot_publish_failure_keeps_previous_pointer(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock

        from hardware_snapshot import publish
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            previous={"schema":1,"generation":"generation-1"}
            (root/"latest.json").write_text(json.dumps(previous))
            c=Mock()
            c.execute.side_effect=[None,RuntimeError("copy failure"),None]
            with self.assertRaises(RuntimeError):
                publish(c,root)
            self.assertEqual(json.loads((root/"latest.json").read_text()),previous)


def test_reboot_import_preserves_existing_run_and_rejects_unmarked_reset(tmp_path):
    import duckdb
    import pytest
    from hardware_warehouse import FIELDS, import_polls, init

    def poll(n, tick, stamp):
        r=dict.fromkeys(FIELDS,0)
        r.update(seq=n,now=tick,begin_ms=tick-100,end_ms=tick-10)
        return f"[{stamp}] EcPair full ".encode()+",".join(str(r[k]) for k in FIELDS).encode()+b"\n"

    old=poll(1,84000000,"28.11.2026 15:09:47")+poll(2,84001000,"28.11.2026 15:09:48")
    marker=b"[28.11.2026 15:14:02] Port backend: PawnIO 2.2.0 with LpcThinkpadEC.bin\n"
    new=poll(1,22843,"28.11.2026 15:14:03")+poll(2,23843,"28.11.2026 15:14:04")
    path=tmp_path/"TPFanControl.log"
    path.write_bytes(old)
    with duckdb.connect() as c:
        init(c)
        import_polls(c,path)
        prior=c.execute("select run_id from polls limit 1").fetchone()[0]
        path.write_bytes(old+marker+new)
        import_polls(c,path)
        import_polls(c,path)
        assert c.execute("select count(*),count(distinct run_id) from polls").fetchone()==(4,2)
        assert c.execute("select count(*) from polls where run_id=?",[prior]).fetchone()==(2,)
        assert c.execute("select count(*) from poll_wall_times").fetchone()==(4,)
        path.write_bytes(old+new)
        with pytest.raises(ValueError):
            import_polls(c,path)
        assert c.execute("select count(*) from polls").fetchone()==(4,)


def test_tick_collision_never_matches_different_boot():
    from pathlib import Path

    import duckdb
    from hardware_warehouse import init
    with duckdb.connect() as c:
        init(c)
        c.execute("insert into polls(run_id,seq,begin_ms,end_ms,now) values ('a',1,1100,1200,1250),('b',1,1100,1200,1250)")
        c.execute("insert into poll_wall_times values ('a',1,11000000000),('b',1,99000000000)")
        c.execute("insert into trace_anchors values ('trace-a',1000,2000,10000000000,0)")
        c.execute(Path(__file__).with_name("hardware_analysis.sql").read_text())
        assert c.execute("select run_id,sha from matched").fetchall()==[("a","trace-a")]
