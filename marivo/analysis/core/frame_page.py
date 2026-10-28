"""Detached immutable pages of completed Frame output."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import TypeAlias

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from marivo.analysis.core.frame_schema import FrameExportColumn
from marivo.render import Card, RenderableResult

FrameKeyScalar: TypeAlias = (
    bool
    | str
    | int
    | float
    | Decimal
    | pa.Date32Scalar
    | pa.TimestampScalar
    | pa.DurationScalar
    | None
)


def detached_rows(rows: pd.DataFrame) -> pd.DataFrame:
    """Copy selected Arrow buffers too; pandas deep copies retain their parents."""
    values: dict[str, pd.Series] = {}
    for name in rows.columns:
        column = rows[name]
        if isinstance(column.dtype, pd.ArrowDtype):
            array = pa.array(column, from_pandas=True)
            copied = pc.take(array, pa.array(range(len(array)), type=pa.int64()))
            values[name] = pd.Series(pd.arrays.ArrowExtensionArray(copied))
        else:
            values[name] = column.reset_index(drop=True).copy(deep=True)
    return pd.DataFrame(values, index=pd.RangeIndex(len(rows)))


@dataclass(frozen=True, slots=True, repr=False)
class FrameReadStats(RenderableResult):
    """Captured work counters for one data-page read.

    Acquired from FramePage.read_stats; direct construction is unsupported.
    Fields capture decoded data/coordinate rows and receipt-hash byte counts.

    Example:
        ``page.read_stats.show()``.

    Constraints:
        Counts include scanned records and repeated scans; they do not
        measure all disk I/O, elapsed time or peak memory.
    """

    data_rows_read: int
    coordinate_rows_read: int
    receipt_bytes_hashed: int

    def _repr_identity(self) -> str:
        return (
            f"FrameReadStats data_rows={self.data_rows_read} "
            f"coordinate_rows={self.coordinate_rows_read} bytes_hashed={self.receipt_bytes_hashed}"
        )

    def _card(self) -> Card:
        return (
            Card(identity=self._repr_identity(), available=(".show()",))
            .field("data_rows_read", str(self.data_rows_read))
            .field("coordinate_rows_read", str(self.coordinate_rows_read))
            .field("receipt_bytes_hashed", str(self.receipt_bytes_hashed))
        )


@dataclass(frozen=True, slots=True, repr=False)
class FramePage(RenderableResult):
    """One detached, ordered projection of a fixed Frame result.

    Acquired with ``frame.read_page(...)``; direct construction is unsupported.
    Fields contain immutable layout, complete keys, pagination and read statistics.

    Example:
        ``rows = fixed.read_page(limit=100).to_pandas()``.

    Constraints:
        Retains only this page, with no Frame, reader or complete audit.
        Coordinates are scoped to input_id; RangeIndex is not a business key.
    """

    input_id: str
    layout: tuple[FrameExportColumn, ...]
    coordinates: Mapping[str, tuple[FrameKeyScalar, ...]]
    limit: int
    total_count: int
    has_more: bool
    next_cursor: str | None
    read_stats: FrameReadStats
    _rows: pd.DataFrame = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "coordinates", MappingProxyType(dict(self.coordinates)))
        object.__setattr__(self, "_rows", detached_rows(self._rows))

    def __len__(self) -> int:
        """Return this page's actual row count without reading data."""
        return len(self._rows)

    def to_pandas(self) -> pd.DataFrame:
        """Return an isolated copy of this page's table.

        Args: None.
        Returns: A DataFrame in layout order, with empty attrs and a page-local RangeIndex.
        Example: ``rows = page.to_pandas()``.
        Constraints: Reads no files and does not change captured read_stats.
        """
        return detached_rows(self._rows)

    def _repr_identity(self) -> str:
        return (
            f"FramePage input={self.input_id[:24]} rows={len(self)} "
            f"total={self.total_count} has_more={self.has_more}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(".layout", ".coordinates", ".read_stats", ".to_pandas()", ".show()"),
        ).field("limit", str(self.limit))
        if self.next_cursor is not None:
            card.field("next_cursor", self.next_cursor)
        return card
