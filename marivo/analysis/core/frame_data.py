"""Independently owned completed Frame data and exact private carrier variants."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias

import pandas as pd

from marivo._definition.types import LiteralValue
from marivo._kernel.input_context import Owner
from marivo.analysis.binding.graph import BoundGraph
from marivo.analysis.core.frame_expression import (
    AlignFrameValues,
    FrameOperationKind,
    NamedFrameComparison,
)
from marivo.analysis.core.frame_method_facts import FrameMethodFacts
from marivo.analysis.core.frozen_calendar import FrozenAttachment, validate_frozen_coordinates
from marivo.analysis.methods.carriers.attachments import OutputAttachment
from marivo.analysis.methods.carriers.data_validation import normalize_complete_frame
from marivo.analysis.model.artifact_data import ArtifactData, SavedFrameMethodFacts
from marivo.analysis.model.data_schema import (
    CalendarComparisonBinding,
    CalendarDataAxis,
    DataAxis,
    DataSchema,
    PairedDataAxis,
)
from marivo.analysis.model.errors import reject
from marivo.analysis.model.frame_contracts import FrameKeyInput, FrameNodeLike, StoredFrameIdentity
from marivo.analysis.model.frame_metadata import FrameResultMetadata, StoredFrameMetadata
from marivo.analysis.model.frame_structure import (
    PendingDataSchema,
    coordinate_attachments,
    require_complete_structure,
)
from marivo.analysis.model.lineage import FrameLineage, empty, validate
from marivo.analysis.model.unique_keys import UNKNOWN_UNIQUE_KEYS, UniqueKeys

if TYPE_CHECKING:
    from marivo.datasource.spi import DatasourceConnectionConfig
    from marivo.semantic.spi import CompiledSemanticState


def _table(
    schema: DataSchema, rows: pd.DataFrame, unique_keys: UniqueKeys = UNKNOWN_UNIQUE_KEYS
) -> pd.DataFrame:
    if not isinstance(rows, pd.DataFrame) or tuple(rows.columns) != tuple(  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
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
    result = normalize_complete_frame(
        independent, schema.columns, schema.key_fields, unique_keys=unique_keys
    )
    if not schema.axes and not unique_keys.proves(()) and len(result) > 1:
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


def normalize_completed_data(
    schema: DataSchema,
    rows: pd.DataFrame,
    attachments: tuple[OutputAttachment, ...],
    *,
    unique_keys: UniqueKeys = UNKNOWN_UNIQUE_KEYS,
) -> tuple[pd.DataFrame, tuple[OutputAttachment, ...]]:
    """Validate complete data and detach its typed rows and coordinate tables."""
    require_complete_structure(schema)
    if type(attachments) is not tuple or any(type(a) is not OutputAttachment for a in attachments):
        reject(
            "immutable typed coordinate attachments",
            type(attachments).__name__,
            "Extract complete data attachments before returning a memory root.",
            "frame.attachments",
        )
    _attachments(schema, tuple(attachment.name for attachment in attachments))
    normalized_rows = _table(schema, rows, unique_keys)
    normalized_attachments = tuple(
        OutputAttachment(
            attachment.name, attachment.schema, _table(attachment.schema, attachment.rows)
        )
        for attachment in attachments
    )
    validate_frozen_coordinates(
        schema,
        normalized_rows,
        tuple(FrozenAttachment(a.name, a.schema, a.rows) for a in normalized_attachments),
        unique_keys=UniqueKeys(tuple(c.slot_id for c in schema.columns), (schema.key_fields,)),
    )
    # Calendar pages address the validated original ordinal by physical offset.
    # Canonicalize once at the completed-data boundary, including directories
    # used only by a saved endpoint, rather than sorting on every bounded read.
    calendars: set[str] = set()

    def collect(axis: DataAxis) -> None:
        if isinstance(axis, CalendarDataAxis):
            assert axis.frozen is not None
            calendars.add(axis.frozen.directory_name)
            if axis.frozen.direct is not None:
                collect(axis.frozen.direct.axis)
        elif isinstance(axis, PairedDataAxis):
            collect(axis.left)
            collect(axis.right)

    for axis in schema.axes:
        collect(axis)
    for fact in schema.comparisons:
        if isinstance(fact.binding, CalendarComparisonBinding):
            collect(fact.binding.baseline)
    normalized_attachments = tuple(
        OutputAttachment(
            attachment.name,
            attachment.schema,
            attachment.rows.sort_values("ordinal", kind="stable").reset_index(drop=True)
            if attachment.name in calendars
            else attachment.rows,
        )
        for attachment in normalized_attachments
    )
    return normalized_rows, normalized_attachments


@dataclass(frozen=True, slots=True, init=False, repr=False)
class CompletedFrameData:
    """Concrete schema and private owned rows; no graph or source state."""

    schema: DataSchema
    _rows: pd.DataFrame
    _attachments: tuple[OutputAttachment, ...]
    unique_keys: UniqueKeys

    def __init__(
        self,
        schema: DataSchema,
        rows: pd.DataFrame,
        attachments: tuple[OutputAttachment, ...] = (),
        *,
        unique_keys: UniqueKeys = UNKNOWN_UNIQUE_KEYS,
    ) -> None:
        normalized_rows, normalized_attachments = normalize_completed_data(
            schema, rows, attachments, unique_keys=unique_keys
        )
        object.__setattr__(self, "schema", schema)
        object.__setattr__(self, "_rows", normalized_rows)
        object.__setattr__(self, "_attachments", normalized_attachments)
        object.__setattr__(
            self,
            "unique_keys",
            unique_keys.project(tuple(c.slot_id for c in schema.columns)).add(schema.key_fields),
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
    frame_key_inputs: tuple[FrameKeyDependency, ...] = ()


@dataclass(frozen=True, slots=True)
class LiteralFrameBinding:
    graph: BoundGraph


@dataclass(frozen=True, slots=True)
class OperationFrameBinding:
    operation: FrameOperationKind
    inputs: tuple[FrameNode, ...]


FrameExpressionRoot: TypeAlias = SourceFrameBinding | LiteralFrameBinding | OperationFrameBinding


@dataclass(frozen=True, slots=True)
class ExpressionFrameBacking:
    root: FrameExpressionRoot


@dataclass(frozen=True, slots=True)
class CompletedFrameResult:
    data: CompletedFrameData
    metadata: FrameResultMetadata
    method_facts: tuple[FrameMethodFacts, ...] = ()

    def __post_init__(self) -> None:
        if self.metadata.lineage is None:
            object.__setattr__(
                self, "metadata", replace(self.metadata, lineage=empty(self.data.schema))
            )
        assert self.metadata.lineage is not None
        validate(self.metadata.lineage, self.data.schema)
        if (
            type(self.data) is not CompletedFrameData
            or type(self.metadata) is not FrameResultMetadata
            or type(self.method_facts) is not tuple
            or any(type(item) is not FrameMethodFacts for item in self.method_facts)
            or len({item.origin_result_id for item in self.method_facts}) != len(self.method_facts)
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

    @property
    def method_facts(self) -> tuple[FrameMethodFacts, ...]:
        return self.result.method_facts


@dataclass(frozen=True, slots=True)
class StoredFrameBacking(StoredFrameIdentity):
    """A precise committed input; no decoded rows or producer graph."""

    artifact_id: str
    data: ArtifactData
    metadata: StoredFrameMetadata
    method_facts: tuple[SavedFrameMethodFacts, ...] = ()
    description: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.artifact_id) is not str
            or not self.artifact_id
            or type(self.data) is not ArtifactData
            or type(self.metadata) is not StoredFrameMetadata
            or (self.description is not None and type(self.description) is not str)
            or type(self.method_facts) is not tuple
            or any(type(item) is not SavedFrameMethodFacts for item in self.method_facts)
            or len({item.origin_result_id for item in self.method_facts}) != len(self.method_facts)
        ):
            reject(
                "an exact committed Artifact and result descriptor",
                repr(self.artifact_id),
                "Open committed metadata.",
                "frame.stored",
            )
        if self.metadata.lineage is None:
            reject(
                "saved column lineage",
                "missing lineage",
                "Open a complete current-format Frame.",
                "frame.lineage",
            )
        validate(self.metadata.lineage, self.data.schema)
        require_complete_structure(self.data.schema)
        _attachments(
            self.data.schema, tuple(attachment.name for attachment in self.data.attachments)
        )


@dataclass(frozen=True, slots=True)
class SourceMetric:
    binding: SourceFrameBinding
    path: tuple[str, ...]
    axis_positions: tuple[tuple[int, int], ...]
    fixed_axes: tuple[tuple[int, tuple[LiteralValue, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class DerivationTarget:
    mode: Literal["level", "absolute", "relative"]
    current: SourceMetric
    baseline: SourceMetric | None = None
    pairing: NamedFrameComparison | AlignFrameValues | None = None
    align_side: Literal["left", "right"] | None = None


@dataclass(frozen=True, slots=True)
class DerivedOutput:
    path: tuple[str, ...]
    target: DerivationTarget


@dataclass(frozen=True, slots=True)
class FrameDerivation:
    outputs: tuple[DerivedOutput, ...]
    rejections: tuple[tuple[tuple[str, ...], str], ...] = ()


class FrameLocalRead(Protocol):
    """Local authority used by evaluation to read an exact captured input."""

    @property
    def owner(self) -> Owner: ...
    @property
    def report_timezone_name(self) -> str: ...
    def read_complete(self, backing: StoredFrameBacking) -> CompletedFrameResult: ...


FrameBacking: TypeAlias = ExpressionFrameBacking | MemoryFrameBacking | StoredFrameBacking
FrameNode: TypeAlias = FrameNodeLike[
    DataSchema | PendingDataSchema,
    FrameBacking,
    FrameLocalRead,
    FrameLineage,
    FrameDerivation | None,
]
FrameKeyDependency: TypeAlias = FrameKeyInput[FrameNode]


def key_checkpoint(data: CompletedFrameData) -> CompletedFrameData:
    """Copy only complete coordinates and their required frozen directories."""
    schema = DataSchema(data.schema.axes, ())
    names = set(required_attachments(schema))
    return CompletedFrameData(
        schema,
        data.copy_rows().loc[:, list(schema.key_fields)],
        tuple(a for a in data.copy_attachments() if a.name in names),
        unique_keys=data.unique_keys.project(tuple(c.slot_id for c in schema.columns)),
    )
