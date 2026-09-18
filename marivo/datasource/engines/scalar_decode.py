"""Preserve exact observed scalar cells during dataframe conversion."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

import ibis.expr.schema as sch
import pandas as pd

from marivo.datasource.errors import DatasourcePreviewError, repair


class ScalarCursor(Protocol):
    def fetchall(self) -> Sequence[Sequence[object]]: ...


def checked_rows(cursor: ScalarCursor, schema: sch.Schema) -> list[tuple[object, ...]]:
    """Check returned cells only; this does not prove unsampled source values."""
    rows = [tuple(row) for row in cursor.fetchall()]
    boolean_positions = tuple(i for i, dtype in enumerate(schema.types) if dtype.is_boolean())
    for row in rows:
        for i in boolean_positions:
            value = row[i]
            if value is not None and not (
                type(value) is bool or (type(value) is int and value in (0, 1))
            ):
                raise DatasourcePreviewError(
                    message="Observed Boolean storage violates its declared representation.",
                    expected="Boolean or exact integer 0/1, or NULL",
                    received=f"invalid Boolean storage in column {schema.names[i]!r}",
                    location="datasource.scalar_decode",
                    repair=repair(
                        kind="configure",
                        canonical_id="source_column",
                        action="Correct this column's Boolean storage before retrying.",
                        preserves_evidence=False,
                    ),
                )
    return rows


def checked_dataframe(
    cursor: ScalarCursor,
    schema: sch.Schema,
    convert: Callable[[pd.DataFrame, sch.Schema], object],
) -> pd.DataFrame:
    """Preserve nullable integers before the backend's dataframe conversion."""
    rows = checked_rows(cursor, schema)
    frame = pd.DataFrame.from_records(rows, columns=schema.names, coerce_float=True)
    for index, (name, dtype) in enumerate(schema.items()):
        if dtype.is_integer():
            # Build from original cells, never from the inferred float column.
            pandas_type = str(dtype).replace("uint", "UInt").replace("int", "Int").lstrip("!")
            try:
                frame[name] = pd.Series([row[index] for row in rows], dtype=pandas_type)
            except (TypeError, ValueError, OverflowError) as cause:
                raise DatasourcePreviewError(
                    message="Observed integer storage cannot preserve the declared type.",
                    expected=f"exact {dtype} values or NULL",
                    received=f"incompatible integer storage in column {name!r}",
                    location="datasource.scalar_decode",
                    repair=repair(
                        kind="configure",
                        canonical_id="source_column",
                        action="Correct the integer values or column binding before retrying.",
                        preserves_evidence=False,
                    ),
                ) from cause
    result = convert(frame, schema)
    if not isinstance(result, pd.DataFrame):
        raise DatasourcePreviewError(
            message="Backend scalar conversion returned an invalid result.",
            expected="a converted dataframe",
            received="a non-dataframe converter result",
            location="datasource.scalar_decode",
            repair=repair(
                kind="configure",
                canonical_id="source_column",
                action="Verify the backend column bindings and converter before retrying.",
                preserves_evidence=False,
            ),
        )
    return result
