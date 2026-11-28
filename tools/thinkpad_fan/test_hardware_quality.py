"""Exercise the actual DuckDB quality projection, not a Python reimplementation."""
from pathlib import Path

import duckdb
from hardware_warehouse import init


def test_tach_quality_preserves_words_and_rejects_uncertain_measurements(tmp_path):
    with duckdb.connect() as c:
        init(c)
        # Fixture: one fresh, ordered, successful same-epoch poll per row.
        rows = [
            (1, 2500, 1, 1, 2520, 1, 1, 1500, 1, 1, 1, 1100),
            (2, 44718, 1, 0, 2520, 1, 1, 1500, 1, 1, 1, 1100),
            (3, 2500, 1, 1, 2520, 0, 1, 1500, 1, 1, 1, 1100),
            (4, 2500, 0, 1, 2520, 1, 0, 1500, 1, 1, 1, 1100),
            (5, 2500, 1, 1, 2800, 1, 1, 1500, 1, 1, 1, 1100),
            (6, 0, 1, 1, 100, 1, 1, 1500, 1, 1, 1, 1100),
            (7, 0, 1, 1, 0, 1, 1, 1500, 1, 1, 1, 1100),
            (8, 2500, 1, 1, 2520, 1, 1, 3001, 1, 1, 1, 1100),
            (9, 2500, 1, 1, 2520, 1, 1, 1500, 0, 1, 1, 1100),
            (10, 2500, 1, 1, 2520, 1, 1, 1500, 1, 0, 1, 1100),
            (11, 2500, 1, 1, 2520, 1, 1, 1500, 1, 1, 2, 1100),
            (12, 2500, 1, 1, 2520, 1, 1, 1500, 1, 1, 1, 900),
            (13, 8192, 1, 1, -1, 1, 1, 1500, 1, 1, 1, 1100),
            (14, 2500, 1, 1, 2700, 1, 1, 3000, 1, 1, 1, 1100),
            (15, 0, 1, 1, 0, 0, 1, 1500, 1, 1, 1, 1100),
            (16, 2500, None, 1, 2520, 1, 1, 1500, 1, 1, 1, 1100),
            (17, 2500, 1, 1, 2520, 1, 1, 1050, 1, 1, 1, 1100),
        ]
        c.executemany(
            """INSERT INTO polls(run_id,epoch,begin_ms,seq,rpm_a,lo_a,hi_a,
            rpm_b,lo_b,hi_b,now,ok,ordered,poll_epoch,end_ms)
            VALUES ('fixture',1,1000,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        expected = [
            (1, 'dual', 2520, 'B', 20),
            (2, 'single', 2520, 'B', None),
            (3, 'single', 2500, 'A', None),
            (4, 'unknown', None, None, None),
            (5, 'disagree', None, None, 300),
            (6, 'disagree', None, None, 100),
            (7, 'dual', 0, 'B', 0),
            (8, 'stale', None, None, 20),
            (9, 'stale', None, None, 20),
            (10, 'stale', None, None, 20),
            (11, 'stale', None, None, 20),
            (12, 'stale', None, None, 20),
            (13, 'unknown', None, None, None),
            (14, 'dual', 2700, 'B', 200),
            (15, 'single', 0, 'A', None),
            (16, 'single', 2520, 'B', None),
            (17, 'stale', None, None, 20),
        ]
        query = """SELECT seq,tach_quality,tach_rpm,tach_source,tach_delta
                   FROM poll_tach_quality ORDER BY seq"""
        assert c.execute(query).fetchall() == expected
        assert c.execute("SELECT rpm_a,hi_a FROM polls WHERE seq=2").fetchone() == (44718, 0)
        # The same portable SQL operates over the raw Parquet projection.
        path = tmp_path / "polls.parquet"
        c.execute("COPY polls TO ? (FORMAT PARQUET)", [str(path)])
    with duckdb.connect() as portable:
        portable.read_parquet(str(path)).create_view("polls")
        sql = Path(__file__).with_name("hardware_quality.sql").read_text()
        portable.execute(sql)
        assert portable.execute(query).fetchall() == expected
