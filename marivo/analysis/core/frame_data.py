"""Independently owned completed Frame data and exact private carrier variants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

import pandas as pd

from marivo.analysis.core.artifact_data import (
    ArtifactData,
    DataSchema,
    OutputAttachment,
)
from marivo.analysis.core.frame_audit import FrameResultMetadata
from marivo.analysis.core.frame_calendar import FrozenSourceCalendar
from marivo.analysis.core.frame_expression import FrameOperationKind
from marivo.analysis.core.frame_key_inputs import FrameKeyInput
from marivo.analysis.core.frame_structure import coordinate_attachments, require_complete_structure
from marivo.analysis.core.frozen_calendar import FrozenAttachment, validate_frozen_coordinates
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.graph_exchange import normalize_complete_frame

if TYPE_CHECKING:
    from marivo.analysis.dsl.frame import AnalysisFrame
    from marivo.datasource.runtime import DatasourceConnectionConfig
    from marivo.semantic._compiled_state import CompiledSemanticState


def _table(schema: DataSchema, rows: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(rows, pd.DataFrame) or tuple(rows.columns) != tuple(
        column.slot_id for column in schema.columns
    ):
        reject(
            "complete rows in concrete schema slot order",
            type(rows).__name__,
            "Extract only the complete current output slots.",
            "frame.data",
        )
    # Copy each typed array without importing arbitrary producer attrs. The stage
    # normalizer can otherwise reuse source Series backing storage.
    independent = pd.DataFrame(
        {column.slot_id: rows[column.slot_id].array.copy() for column in schema.columns},
        index=pd.RangeIndex(len(rows)),
    )
    result = normalize_complete_frame(independent, schema.columns, schema.key_fields)
    if not schema.axes and len(result) > 1:
        reject(
            "at most one row in a zero-axis domain",
            str(len(result)),
            "Keep one complete key per row; use axes for a larger domain.",
            "frame.data",
        )
    return result


def required_attachments(schema: DataSchema) -> tuple[str, ...]:
    return coordinate_attachments(schema)


def _attachments(schema: DataSchema, names: tuple[str, ...]) -> None:
    if len(set(names)) != len(names) or set(required_attachments(schema)) != set(names):
        reject(
            "exactly the required unique coordinate attachments",
            repr(names),
            "Retain each required frozen calendar directory and paired mapping once.",
            "frame.attachments",
        )


@dataclass(frozen=True, slots=True, init=False, repr=False)
class CompletedFrameData:
    """Concrete schema and private owned rows; no graph or source state."""

    schema: DataSchema
    _rows: pd.DataFrame
    _attachments: tuple[OutputAttachment, ...]

    def __init__(
        self, schema: DataSchema, rows: pd.DataFrame, attachments: tuple[OutputAttachment, ...] = ()
    ) -> None:
        require_complete_structure(schema)
        if type(attachments) is not tuple or any(
            type(a) is not OutputAttachment for a in attachments
        ):
            reject(
                "immutable typed coordinate attachments",
                type(attachments).__name__,
                "Extract complete data attachments before returning a memory root.",
                "frame.attachments",
            )
        _attachments(schema, tuple(attachment.name for attachment in attachments))
        object.__setattr__(self, "schema", schema)
        object.__setattr__(self, "_rows", _table(schema, rows))
        object.__setattr__(
            self,
            "_attachments",
            tuple(
                OutputAttachment(
                    attachment.name, attachment.schema, _table(attachment.schema, attachment.rows)
                )
                for attachment in attachments
            ),
        )
        validate_frozen_coordinates(
            self.schema,
            self._rows,
            tuple(FrozenAttachment(a.name, a.schema, a.rows) for a in self._attachments),
        )

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def copy_rows(self) -> pd.DataFrame:
        return self._rows.copy(deep=True)

    def preview_rows(self, n: int) -> pd.DataFrame:
        return self._rows.iloc[:n].copy(deep=True)

    def copy_attachments(self) -> tuple[OutputAttachment, ...]:
        return tuple(
            OutputAttachment(attachment.name, attachment.schema, attachment.rows.copy(deep=True))
            for attachment in self._attachments
        )


@dataclass(frozen=True, slots=True)
class SourceFrameBinding:
    graph: BoundGraph
    state: CompiledSemanticState
    connection: DatasourceConnectionConfig
    calendars: tuple[FrozenSourceCalendar, ...] = ()
    frame_key_inputs: tuple[FrameKeyInput, ...] = ()


@dataclass(frozen=True, slots=True)
class LiteralFrameBinding:
    graph: BoundGraph


@dataclass(frozen=True, slots=True)
class OperationFrameBinding:
    operation: FrameOperationKind
    inputs: tuple[AnalysisFrame, ...]


FrameExpressionRoot: TypeAlias = SourceFrameBinding | LiteralFrameBinding | OperationFrameBinding


@dataclass(frozen=True, slots=True)
class ExpressionFrameBacking:
    root: FrameExpressionRoot


@dataclass(frozen=True, slots=True)
class CompletedFrameResult:
    data: CompletedFrameData
    metadata: FrameResultMetadata

    def __post_init__(self) -> None:
        if (
            type(self.data) is not CompletedFrameData
            or type(self.metadata) is not FrameResultMetadata
        ):
            reject(
                "complete independent data and result metadata",
                type(self).__name__,
                "Finish evaluation before binding memory.",
                "frame.result",
            )


@dataclass(frozen=True, slots=True)
class MemoryFrameBacking:
    result: CompletedFrameResult

    @property
    def data(self) -> CompletedFrameData:
        return self.result.data

    @property
    def metadata(self) -> FrameResultMetadata:
        return self.result.metadata


@dataclass(frozen=True, slots=True)
class StoredFrameBacking:
    """A precise committed input; no decoded rows or producer graph."""

    artifact_id: str
    data: ArtifactData
    metadata: FrameResultMetadata

    def __post_init__(self) -> None:
        if (
            type(self.artifact_id) is not str
            or not self.artifact_id
            or type(self.data) is not ArtifactData
            or type(self.metadata) is not FrameResultMetadata
        ):
            reject(
                "an exact committed Artifact and result descriptor",
                repr(self.artifact_id),
                "Open committed metadata.",
                "frame.stored",
            )
        require_complete_structure(self.data.schema)
        _attachments(
            self.data.schema, tuple(attachment.name for attachment in self.data.attachments)
        )
