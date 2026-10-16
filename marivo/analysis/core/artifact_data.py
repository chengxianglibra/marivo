"""Terminal output contracts, independent of semantic definitions and graphs.

Publication owns final pandas tables. Saved data owns only committed Parquet
receipts; neither representation retains execution state or an Arrow table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from math import isfinite
from typing import Literal

import pandas as pd

from marivo.analysis.core.data_types import DataColumn as DataColumn
from marivo.analysis.core.data_types import DataType as DataType
from marivo.analysis.core.data_types import DecimalDataType as DecimalDataType
from marivo.analysis.core.data_types import DurationDataType as DurationDataType
from marivo.analysis.core.data_types import ScalarDataType as ScalarDataType
from marivo.analysis.core.data_types import TimestampDataType as TimestampDataType
from marivo.analysis.core.model import reject
from marivo.analysis.evidence._dataset_types import (
    AssociationFindingValueV1,
    FindingValueV1,
    ForecastPointFindingValueV1,
    Scalar,
)
from marivo.analysis.materialization.contracts import LocalReceipt


def _text(value: str, location: str) -> None:
    if type(value) is not str or not value or any(c in value for c in "\n\r\t"):
        reject(
            "nonempty single-line data text", repr(value), "Use exact output metadata.", location
        )


@dataclass(frozen=True, slots=True)
class DataAxis:
    """A data token and complete key columns, never an Entity or Field Ref."""

    token: str
    columns: tuple[DataColumn, ...]

    def __post_init__(self) -> None:
        _text(self.token, "data.axis")
        if (
            type(self.columns) is not tuple
            or not self.columns
            or any(type(c) is not DataColumn for c in self.columns)
        ):
            reject(
                "complete immutable axis columns",
                repr(self.columns),
                "Preserve the complete output key.",
                "data.axis",
            )


@dataclass(frozen=True, slots=True)
class CalendarDataAxis(DataAxis):
    """Saved calendar coordinates retain their timezone without calendar logic."""

    timezone: str

    def __post_init__(self) -> None:
        DataAxis.__post_init__(self)
        _text(self.timezone, "data.calendar.timezone")


@dataclass(frozen=True, slots=True)
class DataSchema:
    axes: tuple[DataAxis | CalendarDataAxis, ...]
    values: tuple[DataColumn, ...]
    presence: tuple[DataColumn, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.axes) is not tuple
            or any(type(a) not in (DataAxis, CalendarDataAxis) for a in self.axes)
            or any(
                type(columns) is not tuple or any(type(c) is not DataColumn for c in columns)
                for columns in (self.values, self.presence)
            )
        ):
            reject(
                "immutable typed output columns and axes",
                type(self).__name__,
                "Freeze the output schema.",
                "data.schema",
            )
        tokens = tuple(axis.token for axis in self.axes)
        slots = tuple(column.slot_id for column in self.columns)
        names = tuple(column.name for column in self.values)
        if (
            len(set(tokens)) != len(tokens)
            or len(set(slots)) != len(slots)
            or len(set(names)) != len(names)
        ):
            reject(
                "distinct axis tokens, slots and value names",
                repr((tokens, slots, names)),
                "Assign one identity to each output column.",
                "data.schema",
            )
        if any(
            column.dtype != ScalarDataType("boolean") or column.nullable for column in self.presence
        ):
            reject(
                "nonnullable boolean presence columns",
                repr(self.presence),
                "Store coordinate presence separately from nullable values.",
                "data.presence",
            )

    @property
    def columns(self) -> tuple[DataColumn, ...]:
        return (*tuple(c for axis in self.axes for c in axis.columns), *self.values, *self.presence)

    @property
    def key_fields(self) -> tuple[str, ...]:
        return tuple(c.slot_id for axis in self.axes for c in axis.columns)


@dataclass(frozen=True, slots=True)
class OutputCoordinate:
    slot_id: str
    value: Scalar

    def __post_init__(self) -> None:
        _text(self.slot_id, "output.coordinate")
        _scalar(self.value)


def _scalar(value: Scalar) -> None:
    if (
        type(value) not in (str, int, float, bool, Decimal, date, datetime, type(None))
        or (isinstance(value, float) and not isfinite(value))
        or (isinstance(value, Decimal) and not value.is_finite())
    ):
        reject(
            "a finite output scalar without semantic identity",
            type(value).__name__,
            "Save an explicit output value, not a semantic object or graph.",
            "output.value",
        )


def _coordinates(coordinates: tuple[OutputCoordinate, ...]) -> None:
    if type(coordinates) is not tuple or any(type(c) is not OutputCoordinate for c in coordinates):
        reject(
            "immutable data coordinates",
            type(coordinates).__name__,
            "Reference output slots and scalar values only.",
            "output.coordinates",
        )


@dataclass(frozen=True, slots=True)
class OutputFinding:
    """An explicit method output, without a graph derivation or semantic subject."""

    finding_id: str
    value: FindingValueV1
    coordinates: tuple[OutputCoordinate, ...] = ()

    def __post_init__(self) -> None:
        _text(self.finding_id, "output.finding_id")
        if type(self.value) not in (AssociationFindingValueV1, ForecastPointFindingValueV1):
            reject(
                "a self-contained Finding value",
                type(self.value).__name__,
                "Publish the method's output value without a graph derivation.",
                "output.finding",
            )
        _coordinates(self.coordinates)


@dataclass(frozen=True, slots=True)
class OutputEvidence:
    """A declared, read or checked output fact, not an executable proof chain."""

    kind: Literal["declaration", "read", "check"]
    summary: str
    values: tuple[tuple[str, Scalar], ...] = ()
    coordinates: tuple[OutputCoordinate, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in ("declaration", "read", "check"):
            reject(
                "a declaration/read/check output fact",
                str(self.kind),
                "Report only actual declarations, reads or checks.",
                "output.evidence",
            )
        _text(self.summary, "output.evidence.summary")
        if type(self.values) is not tuple:
            reject(
                "immutable named scalar facts",
                type(self.values).__name__,
                "Save explicit output facts only.",
                "output.evidence",
            )
        for name, value in self.values:
            _text(name, "output.evidence.value")
            _scalar(value)
        _coordinates(self.coordinates)


@dataclass(frozen=True, slots=True)
class DataProjection:
    axis_token: str
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanonicalOrder:
    key_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParquetHandle:
    """Receipt for committed data; opening the handle never reads its rows."""

    receipt: LocalReceipt
    payload_digest: str = ""

    def __post_init__(self) -> None:
        if type(self.receipt) is not LocalReceipt:
            reject(
                "a committed local Parquet receipt",
                type(self.receipt).__name__,
                "Publish the output before constructing its data handle.",
                "artifact.rows",
            )


@dataclass(frozen=True, slots=True)
class SavedAttachment:
    """An explicit output table, encoded under the same Artifact transaction."""

    name: str
    schema: DataSchema
    rows: ParquetHandle

    def __post_init__(self) -> None:
        _text(self.name, "artifact.attachment")
        if type(self.schema) is not DataSchema or type(self.rows) is not ParquetHandle:
            reject(
                "saved output schema and receipt",
                self.name,
                "Publish the attachment.",
                "artifact.attachment",
            )


@dataclass(frozen=True, slots=True)
class ArtifactData:
    schema: DataSchema
    rows: ParquetHandle
    coordinate_projections: tuple[DataProjection, ...] = ()
    attachments: tuple[SavedAttachment, ...] = ()

    def __post_init__(self) -> None:
        if type(self.attachments) is not tuple or any(
            type(item) is not SavedAttachment for item in self.attachments
        ):
            reject(
                "immutable saved output attachments",
                type(self.attachments).__name__,
                "Publish output attachments only.",
                "artifact.attachments",
            )
        if len({item.name for item in self.attachments}) != len(self.attachments):
            reject(
                "distinct attachment names",
                repr(tuple(item.name for item in self.attachments)),
                "Name each output attachment once.",
                "artifact.attachments",
            )
        if type(self.schema) is not DataSchema or type(self.rows) is not ParquetHandle:
            reject(
                "a data schema and committed Parquet handle",
                type(self).__name__,
                "Keep publication tables outside saved Artifact data.",
                "artifact.data",
            )
        if type(self.coordinate_projections) is not tuple or any(
            type(p) is not DataProjection for p in self.coordinate_projections
        ):
            reject(
                "immutable data projections",
                type(self.coordinate_projections).__name__,
                "Preserve output projections only.",
                "artifact.projections",
            )
        for projection in self.coordinate_projections:
            if (
                projection.axis_token not in tuple(axis.token for axis in self.schema.axes)
                or type(projection.columns) is not tuple
                or not projection.columns
                or any(
                    column not in tuple(c.slot_id for c in self.schema.columns)
                    for column in projection.columns
                )
            ):
                reject(
                    "projections onto declared data axes and columns",
                    repr(projection),
                    "Reference the saved output layout.",
                    "artifact.projections",
                )

    @property
    def order(self) -> CanonicalOrder:
        return CanonicalOrder(self.schema.key_fields)


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    artifact_id: str
    session_id: str
    run_id: str
    data: ArtifactData

    def __post_init__(self) -> None:
        for name in ("artifact_id", "session_id", "run_id"):
            _text(getattr(self, name), f"artifact.{name}")
        if type(self.data) is not ArtifactData:
            reject(
                "committed output data",
                type(self.data).__name__,
                "Use the committed manifest.",
                "artifact.manifest",
            )


@dataclass(frozen=True, slots=True)
class OutputAttachment:
    name: str
    schema: DataSchema
    rows: pd.DataFrame

    def __post_init__(self) -> None:
        _text(self.name, "publication.attachment")
        _output_table(self.schema, self.rows)


def _output_table(schema: DataSchema, rows: pd.DataFrame) -> None:
    if type(schema) is not DataSchema or not isinstance(rows, pd.DataFrame):
        reject(
            "a final data schema and pandas DataFrame",
            type(rows).__name__,
            "Finish computation before requesting publication.",
            "artifact.publication",
        )
    if tuple(rows.columns) != tuple(column.slot_id for column in schema.columns):
        reject(
            "final pandas columns in frozen output slot order",
            repr(tuple(rows.columns)),
            "Project only the declared final output columns before publication.",
            "artifact.publication",
        )


@dataclass(frozen=True, slots=True)
class ArtifactPublication:
    """One invocation's final pandas output; consumed by the R2.5 writer."""

    artifact_id: str
    session_id: str
    run_id: str
    schema: DataSchema
    rows: pd.DataFrame
    attachments: tuple[OutputAttachment, ...] = ()
    findings: tuple[OutputFinding, ...] = ()
    evidence: tuple[OutputEvidence, ...] = ()

    def __post_init__(self) -> None:
        for name in ("artifact_id", "session_id", "run_id"):
            _text(getattr(self, name), f"publication.{name}")
        _output_table(self.schema, self.rows)
        for items, expected in (
            (self.attachments, OutputAttachment),
            (self.findings, OutputFinding),
            (self.evidence, OutputEvidence),
        ):
            if type(items) is not tuple or any(type(item) is not expected for item in items):
                reject(
                    "immutable typed output records",
                    type(items).__name__,
                    "Publish only explicit final outputs.",
                    "artifact.publication",
                )
