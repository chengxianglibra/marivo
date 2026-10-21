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
from marivo.analysis.core.data_types import require_data_text
from marivo.analysis.core.frame_audit import FrameResultMetadata
from marivo.analysis.core.frame_method_facts import FrameMethodTarget
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
        require_data_text(self.name, "artifact.attachment")
        if type(self.schema) is not DataSchema or type(self.rows) is not ParquetHandle:
            reject(
                "saved output schema and receipt",
                self.name,
                "Publish the attachment.",
                "artifact.attachment",
            )


METHOD_PAYLOAD_SCHEMA = DataSchema(
    (DataAxis("method.entry", (DataColumn("entry", "entry", ScalarDataType("int64"), False),)),),
    (
        DataColumn("kind", "kind", ScalarDataType("string"), False),
        DataColumn("record", "record", ScalarDataType("string"), False),
    ),
    (),
    value_paths=(("kind",), ("record",)),
)


@dataclass(frozen=True, slots=True)
class SavedFrameMethodFacts:
    """Bounded owned method descriptor; construction never opens payload files."""

    kind: Literal["attribute"]
    origin_result_id: str
    output_schema: DataSchema
    parent_axis_count: int
    parent_count: int
    coordinate_count: int
    coordinate_row_count: int
    facts_digest: str
    payload: SavedAttachment
    parent_schema: DataSchema
    targets: tuple[FrameMethodTarget, ...]

    def __post_init__(self) -> None:
        require_data_text(self.origin_result_id, "artifact.method.origin")
        if (
            type(self.targets) is not tuple
            or not self.targets
            or any(type(target) is not FrameMethodTarget for target in self.targets)
        ):
            reject(
                "immutable closed original target directory",
                type(self.targets).__name__,
                "Preserve the complete target mapping.",
                "artifact.method",
            )
        if (
            type(self.parent_schema) is not DataSchema
            or self.parent_schema.axes != self.output_schema.axes[: self.parent_axis_count]
        ):
            reject(
                "the original complete parent schema",
                self.origin_result_id,
                "Preserve the captured parent coordinate layout.",
                "artifact.method",
            )
        counts = (
            self.parent_axis_count,
            self.parent_count,
            self.coordinate_count,
            self.coordinate_row_count,
        )
        if (
            type(self.kind) is not str
            or self.kind != "attribute"
            or type(self.output_schema) is not DataSchema
            or any(type(value) is not int or value < 0 for value in counts)
            or type(self.payload) is not SavedAttachment
            or type(self.facts_digest) is not str
            or len(self.facts_digest) != 64
            or any(c not in "0123456789abcdef" for c in self.facts_digest)
        ):
            reject(
                "a closed method descriptor with exact nonnegative counts and digest",
                type(self).__name__,
                "Retain the exact owned method attachment and descriptor.",
                "artifact.method",
            )
        require_complete_structure(self.output_schema)
        require_complete_structure(self.parent_schema)
        from marivo.analysis.core.frozen_calendar import coordinate_attachment_schemas

        if (
            self.parent_axis_count > len(self.output_schema.axes)
            or (self.parent_axis_count == 0 and self.parent_count > 1)
            or self.coordinate_count
            != len(
                dict(
                    (
                        *coordinate_attachment_schemas(self.output_schema),
                        *coordinate_attachment_schemas(self.parent_schema),
                    )
                )
            )
            or (self.coordinate_count == 0 and self.coordinate_row_count != 0)
            or self.payload.schema != METHOD_PAYLOAD_SCHEMA
            or self.payload.rows.receipt.realized_row_count
            != self.parent_count + self.coordinate_count
        ):
            reject(
                "method counts and fixed attachment schema matching the original output",
                self.origin_result_id,
                "Preserve the complete descriptor without embedding parent or coordinate rows.",
                "artifact.method",
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
    method_facts: tuple[SavedFrameMethodFacts, ...] = ()
    semantics_version: Literal["frame.v5"] = "frame.v5"

    @property
    def result_id(self) -> str:
        return self.metadata.result_id

    def __post_init__(self) -> None:
        for name in ("store_id", "artifact_id", "session_id", "run_id"):
            require_data_text(getattr(self, name), f"artifact.{name}")
        if (
            type(self.data) is not ArtifactData
            or type(self.metadata) is not FrameResultMetadata
            or self.semantics_version != "frame.v5"
            or type(self.method_facts) is not tuple
            or any(type(item) is not SavedFrameMethodFacts for item in self.method_facts)
        ):
            reject(
                "committed output data",
                type(self.data).__name__,
                "Use the committed manifest.",
                "artifact.manifest",
            )
        if len({item.origin_result_id for item in self.method_facts}) != len(self.method_facts):
            reject(
                "unique method origins",
                self.artifact_id,
                "Deduplicate equal origins before publication.",
                "artifact.methods",
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
        require_data_text(self.name, "publication.attachment")
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
