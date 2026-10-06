"""Shared builders for distribution fixtures tests."""

from datetime import datetime, timezone

from tests.datasource.source_cases import SourceData


def distribution_data(backend: str, rows: list[tuple[int, str, int | None]]) -> SourceData:
    return SourceData(
        "id BIGINT, owner VARCHAR(10), amount BIGINT, happened TIMESTAMP",
        ",".join(
            f"({identity},'{owner}',{number if number is not None else 'NULL'},"
            + ("TIMESTAMP " if backend == "trino" else "")
            + "'2026-08-01 00:00:00')"
            for identity, owner, number in rows
        ),
        "id Int64, owner String, amount Nullable(Int64), happened DateTime64(6, 'UTC')",
        [
            {
                "id": identity,
                "owner": owner,
                "amount": number,
                "happened": datetime(2026, 8, 1, tzinfo=timezone.utc),
            }
            for identity, owner, number in rows
        ],
    )
