"""Single-read transport of checked fine results and ephemeral original inputs."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

from marivo.analysis.compiler.original import (
    ComponentLayout,
)
from marivo.analysis.core.data_types import DurationDataType, ScalarDataType
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import normalize_stage_frame, source_columns


@dataclass(frozen=True, slots=True, eq=False)
class ComponentFrame:
    """One source occurrence's complete actual inputs, confined to an invocation."""

    layout: ComponentLayout
    table: pd.DataFrame
    domain: pd.DataFrame | None = None

    def __post_init__(self) -> None:
        expected = tuple(column.slot_id for column in self.layout.raw_columns)
        if tuple(self.table.columns) != expected:
            raise _invalid(f"original columns {tuple(self.table.columns)!r}; expected {expected!r}")
        identity = [column.slot_id for column, _ in self.layout.identity]
        if self.table.loc[:, identity].isna().any().any():
            raise _invalid("missing contribution identity in original inputs")
        if self.table.duplicated([*self.layout.keys, *identity]).any():
            raise _invalid("duplicate complete contribution identity in one fine coordinate")
        if self.domain is not None and (
            tuple(self.domain.columns) != self.layout.keys
            or (self.layout.keys and self.domain.duplicated(list(self.layout.keys)).any())
        ):
            raise _invalid("original coordinate support differs from the complete fine keys")

    @property
    def coordinates(self) -> pd.DataFrame:
        return (
            self.domain
            if self.domain is not None
            else self.table.loc[:, list(self.layout.keys)].drop_duplicates()
        )


def _invalid(received: str) -> MaterializationError:
    return MaterializationError(
        expected="complete invocation-local original inputs from one checked source statement",
        received=received,
        repair="Retain the original complete contribution identities and fine coordinate mapping.",
        stage="query_components",
        help_target="analysis_frame.compute",
    )


def complete_components(
    layout: ComponentLayout, frame: pd.DataFrame
) -> tuple[pd.DataFrame, ComponentFrame]:
    """Separate one actual source batch into its fine result and sufficient input."""
    names = [column.slot_id for column in layout.columns]
    missing = {*names, layout.present} - set(frame.columns)
    if missing:
        raise _invalid(f"source batch missing columns {tuple(sorted(missing))!r}")
    raw_names = {
        *(column.slot_id for column, _ in layout.identity),
        *(column.slot_id for column in layout.raw_columns if column.slot_id not in layout.keys),
        layout.present,
    }
    summary = [name for name in frame.columns if name not in raw_names]
    fine = frame.loc[:, summary].drop_duplicates().reset_index(drop=True)
    if layout.keys and fine.duplicated(list(layout.keys)).any():
        raise _invalid("different completed fine values for one coordinate")
    if not layout.keys and len(fine) != 1:
        raise _invalid("non-singleton completed scalar input")
    raw = frame.loc[
        frame[layout.present].astype("boolean").fillna(False),
        [column.slot_id for column in layout.raw_columns],
    ].copy()
    for column in layout.raw_columns:
        if isinstance(column.dtype, DurationDataType):
            # Source preparation represents exact Duration values as ticks.
            # Keep ticks here; the ordinary fine-result boundary owns Duration
            # conversion, and the original reducer rounds only its final value.
            raw[column.slot_id] = raw[column.slot_id].astype("int64[pyarrow]")
    actual = {
        column.slot_id: column
        for column in source_columns(
            raw,
            layout.raw_columns,
            (*layout.keys, *(column.slot_id for column, _ in layout.identity)),
        )
    }
    payload = layout.payload
    from marivo.analysis.compiler.original import AggregatePayload, WeightedPayload

    if isinstance(payload, AggregatePayload):
        payload = replace(payload, value=actual[payload.value.slot_id])
    elif isinstance(payload, WeightedPayload):
        payload = replace(
            payload, value=actual[payload.value.slot_id], weight=actual[payload.weight.slot_id]
        )
    layout = replace(
        layout,
        columns=source_columns(fine, layout.columns),
        identity=tuple((actual[column.slot_id], name) for column, name in layout.identity),
        payload=payload,
    )
    raw = normalize_stage_frame(
        raw,
        tuple(
            replace(column, dtype=ScalarDataType("int64"))
            if isinstance(column.dtype, DurationDataType)
            else column
            for column in layout.raw_columns
        ),
        (*layout.keys, *(column.slot_id for column, _ in layout.identity)),
    )
    domain_columns = tuple(column for column in layout.columns if column.slot_id in layout.keys)
    domain = normalize_stage_frame(
        fine.loc[:, list(layout.keys)].drop_duplicates().reset_index(drop=True),
        domain_columns,
        layout.keys,
    )
    return fine, ComponentFrame(
        layout,
        raw.reset_index(drop=True),
        domain,
    )
