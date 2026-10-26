"""Create a neutral local fixture in the copied example project."""

import sys
from datetime import date, timedelta

import duckdb

start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
split = start + timedelta(days=1)
end = split + timedelta(days=1)

with duckdb.connect("readings.duckdb") as connection:
    connection.execute("""
        CREATE OR REPLACE TABLE readings (
            reading_id BIGINT PRIMARY KEY,
            observed_at DATE,
            group_name VARCHAR,
            value DOUBLE
        )
    """)
    connection.executemany(
        "INSERT INTO readings VALUES (?, ?, ?, ?)",
        [
            (1, start, "alpha", 0.0),
            (2, start, "beta", None),
            (3, start, "gamma", 2.0),
            (4, start, "delta", 4.0),
            (5, start, "left_only", 6.0),
            (6, split, "alpha", 1.0),
            (7, split, "beta", 3.0),
            (8, split, "gamma", 3.0),
            (9, split, "delta", 5.0),
            (10, split, "right_only", 7.0),
        ],
    )

print(f"Fixture interval: [{start}, {end}); split: {split}")
print(f"Run axes.py with the same start: {start}")
