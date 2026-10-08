"""Shared builders for distribution fixtures tests."""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from tests.datasource.source_cases import SourceData

Carrier = Literal["int64", "float64", "string", "boolean", "date", "timestamp", "decimal"]


def distribution_data(
    backend: str, rows: list[tuple[int, str, int | None]], carrier: Carrier = "int64"
) -> SourceData:
    types = {
        "int64": "BIGINT",
        "float64": "DOUBLE",
        "string": "VARCHAR(10)",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
        "decimal": "DECIMAL(38,6)",
    }
    if backend == "postgres":
        types["float64"] = "DOUBLE PRECISION"
    native = {
        "int64": "Int64",
        "float64": "Float64",
        "string": "String",
        "boolean": "Bool",
        "date": "Date",
        "timestamp": "DateTime64(6, 'UTC')",
        "decimal": "Decimal(38,6)",
    }
    values: list[int | float | str | bool | date | datetime | Decimal | None] = []
    literals: list[str] = []
    value: int | float | str | bool | date | datetime | Decimal | None
    for _, _, number in rows:
        if number is None:
            value, literal = None, "NULL"
        elif carrier in ("int64", "float64"):
            value, literal = number, str(number)
        elif carrier == "decimal":
            value = Decimal("9007199254740993.000001" if number == 2 else "9007199254740993.000002")
            literal = str(value)
        elif carrier == "string":
            value = "Other" if number == 2 else "x"
            literal = repr(value)
        elif carrier == "boolean":
            value = number == 6
            literal = "TRUE" if value else "FALSE"
        elif carrier == "date":
            value = date(2026, 8, 1 if number == 2 else 2)
            literal = ("DATE " if backend == "trino" else "") + repr(value.isoformat())
        else:
            value = datetime(2026, 8, 1, 0, 0, number, tzinfo=timezone.utc)
            literal = ("TIMESTAMP " if backend == "trino" else "") + repr(
                value.replace(tzinfo=None).isoformat(sep=" ")
            )
        values.append(value)
        literals.append(literal)
    return SourceData(
        f"id BIGINT, owner VARCHAR(10), amount {types[carrier]}, happened TIMESTAMP",
        ",".join(
            f"({identity},'{owner}',{literal},"
            + ("TIMESTAMP " if backend == "trino" else "")
            + "'2026-08-01 00:00:00')"
            for (identity, owner, _), literal in zip(rows, literals, strict=True)
        ),
        f"id Int64, owner String, amount Nullable({native[carrier]}), happened DateTime64(6, 'UTC')",
        [
            {
                "id": identity,
                "owner": owner,
                "amount": value,
                "happened": datetime(2026, 8, 1, tzinfo=timezone.utc),
            }
            for (identity, owner, _), value in zip(rows, values, strict=True)
        ],
    )
