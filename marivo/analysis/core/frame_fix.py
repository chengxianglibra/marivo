"""Typed single-member restriction of one complete ordinary coordinate."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pyarrow as pa

from marivo._definition.types import DefinitionGraphError, LiteralValue, check_literal
from marivo.analysis.core.data_schema import DataAxis
from marivo.analysis.core.data_types import DataColumn
from marivo.analysis.core.frame_structure import PendingDataAxis, PendingDataColumn, axis_role
from marivo.analysis.core.frozen_calendar import FrozenKey, _arrow_type, frozen_key_rows
from marivo.analysis.core.model import reject
from marivo.analysis.core.value_types import literal_kind
from marivo.analysis.errors import AnalysisError


@dataclass(frozen=True, slots=True)
class FixFrameValues:
    position: int
    key: tuple[LiteralValue, ...]


def validate_fixed_key(
    axis: DataAxis | PendingDataAxis, key: tuple[LiteralValue, ...]
) -> FrozenKey | None:
    """Check known constraints without preparing or reading an input."""
    if axis_role(axis) not in ("field", "identity") or len(key) != len(axis.columns):
        reject(
            f"one complete field or identity axis key in {tuple(c.name for c in axis.columns)!r} order",
            repr((axis_role(axis), key)),
            "Select a complete ordinary axis; retain time and paired axes when filtering.",
            "frame.fix",
            canonical_id="analysis_frame.fix",
        )
    columns: tuple[DataColumn | PendingDataColumn, ...] = axis.columns
    for column, value in zip(columns, key, strict=True):
        try:
            check_literal(value, role="Frame fixed key")
        except DefinitionGraphError as error:
            reject(
                "an immutable finite coordinate literal",
                str(error),
                "Supply a typed complete coordinate key.",
                "frame.fix",
                canonical_id="analysis_frame.fix",
            )
        if (
            isinstance(column, PendingDataColumn)
            and column.kind != "unresolved"
            and value is not None
            and literal_kind(value) != column.kind
        ):
            reject(
                f"a {column.kind} key for {column.name}",
                type(value).__name__,
                "Supply a literal matching the captured coordinate kind.",
                "frame.fix",
                canonical_id="analysis_frame.fix",
            )
        if value is None and column.nullable is False:
            reject(
                f"a non-null key for {column.name}",
                "None",
                "Supply a key matching the selected coordinate's nullability.",
                "frame.fix",
                canonical_id="analysis_frame.fix",
            )
    if isinstance(axis, PendingDataAxis):
        return None
    try:
        cells: dict[str, pd.Series] = {}
        for component, value in zip(axis.columns, key, strict=True):
            target = _arrow_type(component.dtype)
            # Arrow's Python scalar inference narrows pandas temporal scalars to
            # microseconds before a safe cast can detect the lost precision.
            carrier = (
                pd.Series([value]) if isinstance(value, (pd.Timestamp, pd.Timedelta)) else [value]
            )
            values = pa.array(carrier, from_pandas=True)
            if value is not None and (
                values.type.id != target.id
                or (pa.types.is_timestamp(target) and values.type.tz != target.tz)
            ):
                raise ValueError(f"{component.name}: {values.type} is not {target}")
            cells[component.slot_id] = pd.Series(
                pd.arrays.ArrowExtensionArray(values.cast(target, safe=True))
            )
        rows = pd.DataFrame(cells)
        return frozen_key_rows(rows, axis.columns)[0]
    except (AnalysisError, pa.ArrowException, ValueError, TypeError, OverflowError) as error:
        reject(
            f"a lossless complete key for {tuple(c.name for c in axis.columns)!r} "
            f"with types {tuple(c.dtype for c in axis.columns)!r}",
            str(error.received) if isinstance(error, AnalysisError) else str(error),
            "Use a scalar for one component or a complete tuple in axis.key_names order.",
            "frame.fix",
            canonical_id="analysis_frame.fix",
        )
