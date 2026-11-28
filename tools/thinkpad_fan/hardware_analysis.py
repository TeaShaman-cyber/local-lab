"""Read a pinned, verified portable snapshot. No host API or database writer."""
import hashlib
import json
import re
from pathlib import Path


def main():
    import argparse

    import duckdb
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,default=Path("/snapshot"))
    a=p.parse_args()
    pointer=json.loads((a.root/"latest.json").read_text())
    name=pointer["generation"]
    if pointer["schema"]!=1 or not re.fullmatch(r"generation-[1-9][0-9]*",name):
        raise ValueError("invalid snapshot pointer")
    folder=a.root/name
    manifest=json.loads((folder/"manifest.json").read_text())
    if manifest["schema"]!=1 or manifest["generation"]!=name:
        raise ValueError("snapshot manifest mismatch")
    tables=("events","windows_events","polls","trace_anchors","capture_batches","imports")
    if "smart_events" in manifest["tables"]:
        tables += ("smart_events",)
    if "r17_rows" in manifest["tables"]:
        tables += ("r17_rows",)
    with duckdb.connect(config={"memory_limit":"512MB","threads":1,"max_temp_directory_size":"96MB"}) as c:
        for table in tables:
            path=folder/(table+".parquet")
            expected=manifest["tables"][table]
            with path.open("rb") as f:
                if hashlib.file_digest(f,"sha256").hexdigest()!=expected["sha256"]:
                    raise ValueError("snapshot checksum mismatch")
            c.read_parquet(str(path)).create_view(table)
            if c.execute(f"select count(*) from {table}").fetchone()[0]!=expected["rows"]:
                raise ValueError("snapshot count mismatch")
        print(name,json.dumps({k:v["rows"] for k,v in manifest["tables"].items()}))
        c.execute(Path(__file__).with_name("hardware_quality.sql").read_text())
        sql=Path(__file__).with_name("hardware_analysis.sql").read_text()
        for statement in c.extract_statements(sql):
            result=c.execute(statement.query)
            if result.description and statement.type==duckdb.StatementType.SELECT:
                print([x[0] for x in result.description])
                print(*result.fetchall(),sep="\n")
        if "smart_events" in tables:
            result = c.execute("""select kind,parse_status,count(*) as events,count(seq) as linked
                                  from smart_events group by all order by kind,parse_status""")
            print([x[0] for x in result.description])
            print(*result.fetchall(),sep="\n")
        else:
            print("Smart event coverage: unavailable (legacy snapshot)")
        if "r17_rows" in tables:
            result = c.execute("""select quality,count(*) as n,count(floor_pwm) as qualified_floor,
                                  min(floor_pwm),max(floor_pwm) from r17_rows group by quality order by quality""")
            print([x[0] for x in result.description])
            print(*result.fetchall(),sep="\n")
        else:
            print("R17 row coverage: unavailable (legacy snapshot)")

if __name__=="__main__":
    main()
