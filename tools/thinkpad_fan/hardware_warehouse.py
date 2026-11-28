"""Bounded local Windows hardware telemetry warehouse; never controls devices."""
import argparse
import base64
import ctypes
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path("D:/theseus-hardware")
SOURCE = Path("E:/tpfan-qa/f4212a7-full-poll/TPFanControl.log")
GIB = 1024**3
BUDGET = 5*GIB
HEADROOM = GIB
RESERVE = 10*GIB
SESSION = "TheseusHardwareWarehouse"
FIELDS = """seq ok dispatch begin_ms end_ms handler now epoch poll_epoch ordered mode level ack ec_max ta ra tb rb rpm_a lo_a hi_a rpm_b lo_b hi_b cpu_valid cpu_time package hottest command_ok post_level post_pending""".split()
NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
CHANNELS = {
    "System": ("Netwtw06", "Netwtw04", "Netwtw08", "Netwtw10", "NDIS",
               "Microsoft-Windows-NDIS", "Microsoft-Windows-Kernel-Power",
               "Microsoft-Windows-Power-Troubleshooter", "Microsoft-Windows-Kernel-PnP",
               "Microsoft-Windows-Kernel-Processor-Power", "Microsoft-Windows-Kernel-Acpi",
               "Microsoft-Windows-WHEA-Logger", "ACPI", "disk", "stornvme", "storahci",
               "Microsoft-Windows-StorPort", "Microsoft-Windows-USB-USBHUB3"),
    "Microsoft-Windows-WLAN-AutoConfig/Operational": (),
}
LIMIT = 5000

def room(used, free, budget, reserve, headroom):
    return used+headroom <= budget and free-headroom >= reserve

def check_room(root):
    used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    if not room(used, shutil.disk_usage(root).free, BUDGET, RESERVE, HEADROOM):
        raise RuntimeError("storage budget/reserve reached; collection paused, no deletion")

def utc_ns(t):
    m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(?:Z|\+00:00)", t)
    if not m:
        raise ValueError(t)
    d = dt.datetime.fromisoformat(m[1])-dt.datetime(1970,1,1)
    return (d.days*86400+d.seconds)*10**9+int((m[2] or "").ljust(9,"0"))

def event_rows(xml, channel):
    root = ET.fromstring(xml)
    rows = []
    for e in root.iter(NS+"Event"):
        s = e.find(NS+"System")
        if s is None or s.findtext(NS+"Channel") != channel:
            raise ValueError("event channel mismatch")
        raw = ET.tostring(e, encoding="unicode")
        rows.append((hashlib.sha256(raw.encode()).hexdigest(),channel,
                     int(s.findtext(NS+"EventRecordID")),s.find(NS+"Provider").get("Name"),
                     int(s.findtext(NS+"EventID")),utc_ns(s.find(NS+"TimeCreated").get("SystemTime")),raw))
    return rows

def parse_polls(raw):
    rows=[]
    for line in raw.splitlines(keepends=True):
        if b"EcPair full " not in line or not line.endswith(b"\n"):
            continue
        m=re.fullmatch(rb"\[[^\r\n]+\] EcPair full (-?\d+(?:,-?\d+){30})\r?\n",line)
        if not m:
            raise ValueError("malformed full poll")
        row=tuple(map(int,m[1].split(b",")))
        if row[0]<=0 or (rows and row[0]<=rows[-1][0]):
            raise ValueError("sequence reset/duplicate: split service runs")
        rows.append(row)
    return rows

def command(args, timeout=30):
    return subprocess.run(args,capture_output=True,timeout=timeout,check=False,
                          creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))

def checked(args, timeout=30):
    r=command(args,timeout)
    if r.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} rc={r.returncode}")
    return r.stdout

def connect(root):
    import duckdb
    return duckdb.connect(str(root/"hardware.duckdb"),config={
        "temp_directory":str(root/"tmp"),"max_temp_directory_size":"256MB",
        "memory_limit":"512MB","threads":2})

def insert(c, table, names, rows, conflict=False):
    import pyarrow as pa
    if not rows:
        return
    batch=pa.table({k:[r[i] for r in rows] for i,k in enumerate(names)})
    c.register("incoming",batch)
    try:
        c.execute("insert into "+table+" select * from incoming"+(" on conflict do nothing" if conflict else ""))
    finally:
        c.unregister("incoming")

def init(c):
    from smart_events import init_smart
    init_smart(c)
    c.execute("create table if not exists imports(sha varchar primary key, source varchar, bytes bigint,n bigint)")
    c.execute("create table if not exists events(sha varchar, ordinal bigint, timestamp_ns bigint, provider_id varchar, provider_name varchar,event_id integer,version integer,task integer,opcode integer,level integer,event json,primary key(sha,ordinal))")
    c.execute("create table if not exists polls(run_id varchar,"+",".join(k+" bigint" for k in FIELDS)+",primary key(run_id,seq))")
    c.execute(Path(__file__).with_name("hardware_quality.sql").read_text())
    c.execute("create table if not exists poll_snapshots(sha varchar primary key,run_id varchar,bytes bigint,n bigint)")
    c.execute("create table if not exists trace_anchors(sha varchar primary key,tick_start bigint,tick_end bigint,offset_ns bigint,drift_ns bigint)")
    c.execute("create table if not exists windows_events(event_key varchar primary key,channel varchar,record_id bigint,provider varchar,event_id integer,timestamp_ns bigint,xml varchar)")
    c.execute("create table if not exists windows_batches(channel varchar,query_ns bigint,lookback_ms bigint,n bigint,capped boolean)")
    c.execute("create table if not exists capture_batches(sha varchar primary key,started_ns bigint,ended_ns bigint,events_lost bigint,buffers_lost bigint,mode varchar)")
    c.execute("create or replace view hardware_timeline as select timestamp_ns,provider_name as provider,event_id,'ETW' as source from events union all select timestamp_ns,provider,event_id,'EventLog' from windows_events")

def import_windows(c):
    for channel, providers in CHANNELS.items():
        last=c.execute("select max(query_ns) from windows_batches where channel=? and not capped",[channel]).fetchone()[0]
        now=time.time_ns()
        lookback=min(7*86400000,max(3600000,(now-last)//1000000+3600000)) if last else 7*86400000
        cond=" and ("+" or ".join('Provider[@Name="'+p+'"]' for p in providers)+")" if providers else ""
        query=f"*[System[TimeCreated[timediff(@SystemTime)<={lookback}]{cond}]]"
        data=checked(["wevtutil","qe",channel,"/q:"+query,"/rd:true",f"/c:{LIMIT}","/f:xml","/e:Events","/uni:true"],45)
        if len(data)>32*1024**2:
            raise RuntimeError("event query exceeds 32MiB")
        rows=event_rows(data.decode("utf-16"),channel) if data.strip() else []
        c.execute("begin")
        try:
            insert(c,"windows_events",["event_key","channel","record_id","provider","event_id","timestamp_ns","xml"],rows,True)
            c.execute("insert into windows_batches values (?,?,?,?,?)",[channel,now,lookback,len(rows),len(rows)>=LIMIT])
            c.execute("commit")
        except Exception:
            c.execute("rollback")
            raise
        print("EVENTLOG",channel,len(rows),"CAPPED" if len(rows)>=LIMIT else "OK",flush=True)

def import_polls(c, source):
    from smart_events import import_smart, parse_smart
    from snapshot import shared_open
    with shared_open(source) as f:
        raw=f.read(128*1024**2+1)
    if len(raw)>128*1024**2:
        raise RuntimeError("poll snapshot exceeds 128MiB; split before import")
    rows=parse_polls(raw)
    if not rows:
        raise RuntimeError("no full polls")
    run=hashlib.sha256((str(source)+"|"+",".join(map(str,rows[0]))).encode()).hexdigest()
    smart = parse_smart(raw, [dict(zip(FIELDS, row)) for row in rows])
    # Preserve the pilot identity (same path and first row).
    c.execute("begin")
    try:
        # Reject conflicting reimports instead of silently masking changed evidence.
        import pyarrow as pa
        c.register("incoming_polls",pa.table({"run_id":[run]*len(rows),**{k:[r[i] for r in rows] for i,k in enumerate(FIELDS)}}))
        try:
            comparison=" or ".join("p."+k+" is distinct from b."+k for k in FIELDS[1:])
            if c.execute("select count(*) from polls p join incoming_polls b using(run_id,seq) where "+comparison).fetchone()[0]:
                raise ValueError("conflicting poll evidence")
            c.execute("insert into polls select * from incoming_polls on conflict do nothing")
        finally:
            c.unregister("incoming_polls")
        import_smart(c, run, smart)
        c.execute("insert into poll_snapshots values (?,?,?,?) on conflict do nothing",[hashlib.sha256(raw).hexdigest(),run,len(raw),len(rows)])
        c.execute("commit")
    except Exception:
        c.execute("rollback")
        raise
    print("POLLS",len(rows),flush=True)

def anchor():
    tick=ctypes.windll.kernel32.GetTickCount64
    tick.restype=ctypes.c_ulonglong
    a=tick()
    w=time.time_ns()
    b=tick()
    return {"tick_before":a,"tick_after":b,"unix_ns":w}

def offset(a):
    return a["unix_ns"]-(a["tick_before"]+a["tick_after"])*500000

def capture(root, seconds):
    # Dedicated name; if it already exists, do not take over an unknown writer.
    if command(["logman","query",SESSION,"-ets"]).returncode==0:
        raise RuntimeError("owned session already running; inspect before recovery")
    folder=root/"etl"/("segment-"+str(time.time_ns()))
    folder.mkdir()
    path=folder/"hardware.etl"
    profile=Path(__file__).with_name("hardware_providers.txt")
    shutil.copyfile(profile,folder/"providers.txt")
    checked(["logman","start",SESSION,"-pf",str(folder/"providers.txt"),"-o",str(path),"-f","bincirc","-max","16","-ets"])
    try:
        a=anchor()
        session=checked(["logman","query",SESSION,"-ets"])
        (folder/"session.txt").write_bytes(session)
        if any(line.split()[0].encode().lower() not in session.lower()
               for line in (folder/"providers.txt").read_text().splitlines() if line.strip()):
            raise RuntimeError("provider readback mismatch")
        time.sleep(seconds)
        b=anchor()
    finally:
        checked(["logman","stop",SESSION,"-ets"])
    (folder/"anchors.json").write_text(json.dumps([a,b]))
    return folder

def json_default(v):
    if isinstance(v,bytes):
        return {"encoding":"base64","data":base64.b64encode(v).decode("ascii")}
    raise TypeError(type(v).__name__)

def import_trace(c,folder):
    from pyetwkit._core import EtlReader
    p=folder/"hardware.etl"
    if p.stat().st_size>32*1024**2:
        raise ValueError("trace exceeds input bound")
    h=hashlib.sha256(p.read_bytes()).hexdigest()
    if c.execute("select count(*) from imports where sha=?",[h]).fetchone()[0]:
        return
    a,b=json.loads((folder/"anchors.json").read_text())
    drift=abs(offset(b)-offset(a))
    events=EtlReader(str(p)).read_all()
    if not events or len(events)>200000:
        raise RuntimeError("trace empty or exceeds event bound; retained")
    rows=[]
    lost=buflost=None
    for i,e in enumerate(events):
        v=e.to_dict()
        prop=v["properties"]
        if v["provider_name"]=="MSNT_SystemTrace" and "EventsLost" in prop:
            lost,buflost=prop["EventsLost"],prop["BuffersLost"]
        rows.append((h,i,utc_ns(v["timestamp"]),v["provider_id"],v["provider_name"],v["event_id"],v["version"],v["task"],v["opcode"],v["level"],json.dumps(v,default=json_default,ensure_ascii=False)))
    c.execute("begin")
    try:
        insert(c,"events",["sha","ordinal","timestamp_ns","provider_id","provider_name","event_id","version","task","opcode","level","event"],rows)
        c.execute("insert into imports values (?,?,?,?)",[h,str(p),p.stat().st_size,len(rows)])
        c.execute("insert into capture_batches values (?,?,?,?,?,?)",[h,a["unix_ns"],b["unix_ns"],lost,buflost,"circular16MiB"])
        if drift<=32000000 and b["tick_before"]>a["tick_after"]:
            c.execute("insert into trace_anchors values (?,?,?,?,?)",[h,a["tick_after"],b["tick_before"],offset(a),drift])
        c.execute("commit")
    except Exception:
        c.execute("rollback")
        raise
    print("ETL",len(rows),"lost",lost,buflost,"drift_ns",drift,flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seconds",type=int,default=240)
    p.add_argument("--import-only",action="store_true")
    a=p.parse_args()
    if not 1<=a.seconds<=240:
        p.error("seconds must be 1..240")
    check_room(ROOT)
    if not a.import_only:
        capture(ROOT,a.seconds)
    with connect(ROOT) as c:
        init(c)
        for folder in sorted((ROOT/"etl").glob("segment-*")):
            check_room(ROOT)
            if (folder/"anchors.json").is_file():
                import_trace(c,folder)
        check_room(ROOT)
        import_windows(c)
        import_polls(c,SOURCE)
        c.execute("checkpoint")
        print("WAREHOUSE",c.execute("select (select count(*) from events),(select count(*) from windows_events),(select count(*) from polls)").fetchone(),flush=True)

if __name__=="__main__":
    # pythonw has no standard streams. Keep one bounded per-run diagnostic log.
    if sys.stdout is None or sys.stderr is None:
        stream=(ROOT/"warehouse-run.log").open("w",encoding="utf-8",buffering=1)
        sys.stdout=sys.stderr=stream
    started=time.time_ns()
    status={"started_ns":started,"result":"FAILED"}
    try:
        main()
        status["result"]="OK"
    except Exception as exc:
        status["error"]=str(exc)[:2000]
        raise
    finally:
        status["ended_ns"]=time.time_ns()
        temp=ROOT/"last-run.tmp"
        temp.write_text(json.dumps(status))
        temp.replace(ROOT/"last-run.json")
