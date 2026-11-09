"""Complete immutable plain-table results acquired by exact saved reference."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.analysis.core.result_reference import DataFrameReference
from marivo.render import Card, RenderableResult


@dataclass(frozen=True, init=False, repr=False)
class DataFrameResult(RenderableResult):
    """Complete fixed table and saved metadata, acquired with ``mv.read_dataframe``.

    Fields: ref is the exact saved identity; committed_at is the UTC save time;
    row_count includes zero-column rows; schema is an ordered Arrow schema without
    internal metadata. kind is ``dataframe``.
    Example: ``result = mv.read_dataframe(reference); table = result.to_pandas()``.
    Constraints: Immutable, source-free and detached from files. Direct construction
    is unsupported. Save time does not establish business freshness or coverage.
    """

    ref: DataFrameReference
    committed_at: datetime
    row_count: int
    schema: pa.Schema
    _table: pa.Table = field(repr=False, compare=False)

    def __new__(cls) -> DataFrameResult:
        raise TypeError("Acquire a DataFrameResult from read_dataframe().")

    @classmethod
    def _create(
        cls,
        reference: DataFrameReference,
        committed_at: datetime,
        row_count: int,
        table: pa.Table,
    ) -> DataFrameResult:
        value = object.__new__(cls)
        object.__setattr__(value, "ref", reference)
        object.__setattr__(value, "committed_at", committed_at)
        object.__setattr__(value, "row_count", row_count)
        object.__setattr__(value, "schema", table.schema)
        object.__setattr__(value, "_table", table)
        return value

    @property
    def kind(self) -> Literal["dataframe"]:
        """Return the fixed result kind. Example: ``result.kind``. No I/O."""
        return "dataframe"

    def to_pandas(self) -> pd.DataFrame:
        """Export complete saved values as an isolated pandas table.

        Args: None.

        Returns:
            A DataFrame with Arrow-backed columns, empty attrs and an unnamed
            default RangeIndex. Zero-column tables retain their logical row count.

        Example:
            ``table = result.to_pandas()``.

        Constraints:
            No I/O. Each call detaches data buffers; edits cannot change
            the result or other exports. Preserves saved values and types, rather
            than the original pandas dtype containers or attrs. Memory grows with
            the complete table; conversions for arithmetic remain explicit.
        """
        if not len(self.schema):
            return pd.DataFrame(index=pd.RangeIndex(self.row_count))
        # pandas deep copies retain Arrow buffers. Take detaches them for each export.
        table = pc.take(self._table, pa.array(range(self.row_count), type=pa.int64()))
        rows: pd.DataFrame = table.to_pandas(types_mapper=pd.ArrowDtype, ignore_metadata=True)
        return rows

    def _repr_identity(self) -> str:
        return f"DataFrameResult kind={self.kind} result_id={self.ref.result_id!r}"

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".schema", ".to_pandas()"))
        card.field("committed_at", self.committed_at.isoformat())
        card.field("row_count", str(self.row_count))
        card.field("column_count", str(len(self.schema)))
        card.table(
            ("name", "dtype"),
            (
                (self.schema[index].name, str(self.schema[index].type))
                for index in range(min(12, len(self.schema)))
            ),
            label="columns",
            row_count=len(self.schema),
            recovery="Read .schema for all ordered columns and exact types.",
        )
        return card
