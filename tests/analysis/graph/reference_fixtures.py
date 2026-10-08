"""Shared builders for reference fixtures tests."""

from datetime import datetime, timezone

from tests.datasource.source_cases import SourceData


def reference_data(backend: str, last_amount: int = 4) -> SourceData:
    facts = [
        (9007199254740992, 1, "a", 2),
        (9007199254740993, 2, "a", None),
        (9007199254740993, 1, "b", last_amount),
    ]
    return SourceData(
        "id BIGINT, revision BIGINT, tenant VARCHAR(10), amount BIGINT, happened TIMESTAMP",
        ",".join(
            f"({identity},{revision},'{tenant}',{amount if amount is not None else 'NULL'},"
            + ("TIMESTAMP " if backend == "trino" else "")
            + "'2026-08-01 00:00:00')"
            for identity, revision, tenant, amount in facts
        ),
        "id Int64, revision Int64, tenant String, amount Nullable(Int64), happened DateTime64(6, 'UTC')",
        [
            {
                "id": identity,
                "revision": revision,
                "tenant": tenant,
                "amount": amount,
                "happened": datetime(2026, 8, 1, tzinfo=timezone.utc),
            }
            for identity, revision, tenant, amount in facts
        ],
    )
