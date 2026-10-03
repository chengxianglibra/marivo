"""Small real Event sources shared by isolated Runtime acceptance phases."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb

from tests.lazy_execution_fixtures import seed_execution_database

START = datetime(2026, 2, 1, tzinfo=timezone.utc)
END = datetime(2026, 2, 2, tzinfo=timezone.utc)
THROUGH = datetime(2026, 2, 3, tzinfo=timezone.utc)
OCCURRENCE_CANARY = 981730041


def seed_event_database(
    project: Path,
) -> Path:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        for name in ("started_rows", "finished_rows"):
            connection.execute(
                f"CREATE TABLE {name} (occurrence_id BIGINT, customer_id BIGINT, occurred_at TIMESTAMP)"
            )
        connection.execute(
            "INSERT INTO started_rows VALUES (?,1,'2026-02-01 00:00:00'), (?,1,'2026-02-01 02:00:00'), (?,2,'2026-02-01 12:00:00'), (?,3,'2026-02-02 00:00:00')",
            [OCCURRENCE_CANARY + i for i in range(4)],
        )
        connection.execute(
            "INSERT INTO finished_rows VALUES (?,1,'2026-02-01 03:00:00'), (?,1,'2026-02-02 01:00:00'), (?,2,'2026-02-03 00:00:00')",
            [OCCURRENCE_CANARY + 10 + i for i in range(3)],
        )
    return database
