"""Terminal output contracts, independent of semantic definitions and graphs.

Publication owns final pandas tables. Saved data owns only committed Parquet
receipts; neither representation retains execution state or an Arrow table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from marivo.analysis.core.data_schema import BuiltinCalendarData as BuiltinCalendarData
from marivo.analysis.core.data_schema import CalendarDataAxis as CalendarDataAxis
from marivo.analysis.core.data_schema import DataAxis as DataAxis
from marivo.analysis.core.data_schema import DataSchema as DataSchema
from marivo.analysis.core.data_schema import FrozenCalendarData as FrozenCalendarData
from marivo.analysis.core.data_schema import FrozenIntervalData as FrozenIntervalData
from marivo.analysis.core.data_schema import IntervalDataAxis as IntervalDataAxis
from marivo.analysis.core.data_schema import NamedCalendarData as NamedCalendarData
from marivo.analysis.core.data_schema import PairedDataAxis as PairedDataAxis
from marivo.analysis.core.data_types import DataColumn as DataColumn
from marivo.analysis.core.data_types import DataType as DataType
from marivo.analysis.core.data_types import DecimalDataType as DecimalDataType
from marivo.analysis.core.data_types import DurationDataType as DurationDataType
from marivo.analysis.core.data_types import ScalarDataType as ScalarDataType
from marivo.analysis.core.data_types import TimestampDataType as TimestampDataType
from marivo.analysis.core.frame_audit import FrameResultMetadata
from marivo.analysis.core.frame_structure import require_complete_structure
from marivo.analysis.core.model import reject
from marivo.analysis.core.output_records import (
    OutputCoordinate as OutputCoordinate,
)
from marivo.analysis.core.output_records import (
    OutputEvidence as OutputEvidence,
)
from marivo.analysis.core.output_records import (
    OutputFinding as OutputFinding,
)
from marivo.analysis.materialization.contracts import LocalReceipt


def _text(value: str, location: str) -> None:
    if type(value) is not str or not value or any(c in value for c in "\n\r\t"):
        reject(
            "nonempty single-line data text", repr(value), "Use exact output metadata.", location
        )


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


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    store_id: str
    artifact_id: str
    session_id: str
    run_id: str
    data: ArtifactData
    metadata: FrameResultMetadata
    semantics_version: Literal["frame.v1"] = "frame.v1"

    @property
    def result_id(self) -> str:
        return self.metadata.result_id

    def __post_init__(self) -> None:
        for name in ("store_id", "artifact_id", "session_id", "run_id"):
            _text(getattr(self, name), f"artifact.{name}")
        if (
            type(self.data) is not ArtifactData
            or type(self.metadata) is not FrameResultMetadata
            or self.semantics_version != "frame.v1"
        ):
            reject(
                "committed output data",
                type(self.data).__name__,
                "Use the committed manifest.",
                "artifact.manifest",
            )
        require_complete_structure(self.data.schema)
        from marivo.analysis.core.frozen_calendar import coordinate_attachment_schemas

        layouts = dict(coordinate_attachment_schemas(self.data.schema))
        if set(layouts) != {part.name for part in self.data.attachments} or any(
            layouts[part.name] != part.schema for part in self.data.attachments
        ):
            reject(
                "complete frozen attachment layouts",
                self.artifact_id,
                "Retain exactly the declared coordinate attachments.",
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
