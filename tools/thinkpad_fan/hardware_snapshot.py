"""Publish an immutable, metadata-only analysis snapshot from the local writer."""
import hashlib
import json
import time
from pathlib import Path

TABLES = {
    "events": "sha,ordinal,timestamp_ns,provider_id,provider_name,event_id,version,task,opcode,level",
    "windows_events": "event_key,channel,record_id,provider,event_id,timestamp_ns",
    "polls": "run_id,seq,ok,dispatch,begin_ms,end_ms,handler,now,epoch,poll_epoch,ordered,mode,level,ack,ec_max,ta,ra,tb,rb,rpm_a,lo_a,hi_a,rpm_b,lo_b,hi_b,cpu_valid,cpu_time,package,hottest,command_ok,post_level,post_pending",
    "trace_anchors": "sha,tick_start,tick_end,offset_ns,drift_ns",
    "capture_batches": "sha,started_ns,ended_ns,events_lost,buffers_lost,mode",
    "imports": "sha,bytes,n",
    "smart_events": "run_id,byte_offset,tick,kind,seq,payload,parse_status,line_sha256",
}

def generation_name(value):
    if type(value) is not int or value<=0:
        raise ValueError("invalid generation")
    return f"generation-{value}"

def literal(value):
    return "'"+str(value).replace("'","''")+"'"

def publish(c, root: Path):
    root.mkdir(exist_ok=True)
    generation=generation_name(time.time_ns())
    folder=root/generation
    folder.mkdir()
    manifest={"schema":1,"generation":generation,"tables":{}}
    c.execute("begin")
    try:
        for table,columns in TABLES.items():
            if table == "smart_events" and not c.execute(
                    "select count(*) from information_schema.tables where table_name='smart_events'"
            ).fetchone()[0]:
                continue
            path=folder/(table+".parquet")
            c.execute(f"COPY (SELECT {columns} FROM {table}) TO {literal(path)} (FORMAT PARQUET, COMPRESSION ZSTD)")
            count=c.execute(f"select count(*) from {table}").fetchone()[0]
            assert c.execute("select count(*) from read_parquet(?)",[str(path)]).fetchone()[0]==count
            with path.open("rb") as f:
                digest=hashlib.file_digest(f,"sha256").hexdigest()
            manifest["tables"][table]={"rows":count,"bytes":path.stat().st_size,"sha256":digest}
        c.execute("commit")
    except Exception:
        c.execute("rollback")
        raise
    (folder/"manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
    temp=root/(generation+".tmp")
    temp.write_text(json.dumps({"schema":1,"generation":generation}),encoding="utf-8")
    temp.replace(root/"latest.json")
    return manifest

def main():
    import argparse

    import duckdb
    from hardware_warehouse import ROOT, check_room
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,default=ROOT)
    a=p.parse_args()
    check_room(a.root)
    with duckdb.connect(str(a.root/"hardware.duckdb"),read_only=True) as c:
        print(json.dumps(publish(c,a.root/"analysis")))

if __name__=="__main__":
    main()
