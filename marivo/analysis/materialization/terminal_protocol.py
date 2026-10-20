"""Closed terminal-output codec; no semantic, graph or continuation variants."""

from __future__ import annotations

from dataclasses import dataclass, is_dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import UnionType
from typing import Literal, TypeVar, Union, get_args, get_origin, get_type_hints

from marivo.analysis.core.artifact_data import (
    ArtifactData,
    ArtifactManifest,
    BuiltinCalendarData,
    CalendarDataAxis,
    DataAxis,
    DataColumn,
    DataSchema,
    DecimalDataType,
    DurationDataType,
    FrozenCalendarData,
    FrozenIntervalData,
    IntervalDataAxis,
    NamedCalendarData,
    OutputCoordinate,
    OutputEvidence,
    OutputFinding,
    PairedDataAxis,
    ParquetHandle,
    SavedAttachment,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_audit import (
    EvaluationRecord,
    FrameAudit,
    FrameResultMetadata,
    LiteralInput,
    MemoryInput,
    SourceInput,
    StoredInput,
)
from marivo.analysis.errors import AnalysisError
from marivo.analysis.evidence._dataset_types import (
    AssociationFindingValueV1,
    AssociationLagV1,
    ForecastPointFindingValueV1,
    NoAssociationLagV1,
)
from marivo.analysis.materialization.contracts import (
    FileEntry,
    LocalReceipt,
    _array,
    _obj,
    _text,
    canonical_json,
    parse_json,
)
from marivo.analysis.materialization.errors import IntegrityError


def invalid(received: str) -> IntegrityError:
    return IntegrityError(
        expected="a complete Store 13 Frame output v2 value",
        received=received,
        repair="Preserve the selected Store and output files. Inspect the exact terminal record; old graph/continuation formats cannot be migrated or replayed.",
        stage="terminal_output",
        help_target="analysis_frame",
    )


@dataclass(frozen=True, slots=True)
class InvocationInput:
    """An existing completed result selected for publication, never execution."""

    result_id: str

    def __post_init__(self) -> None:
        if type(self.result_id) is not str or not self.result_id:
            raise invalid("expected the exact completed result ID")


_RECORDS = (
    ArtifactManifest,
    ArtifactData,
    DataSchema,
    DataAxis,
    CalendarDataAxis,
    IntervalDataAxis,
    FrozenIntervalData,
    BuiltinCalendarData,
    NamedCalendarData,
    FrozenCalendarData,
    PairedDataAxis,
    DataColumn,
    ScalarDataType,
    DecimalDataType,
    TimestampDataType,
    DurationDataType,
    ParquetHandle,
    SavedAttachment,
    LocalReceipt,
    FileEntry,
    OutputCoordinate,
    OutputFinding,
    OutputEvidence,
    InvocationInput,
    FrameResultMetadata,
    FrameAudit,
    EvaluationRecord,
    SourceInput,
    LiteralInput,
    MemoryInput,
    StoredInput,
    AssociationFindingValueV1,
    ForecastPointFindingValueV1,
    AssociationLagV1,
    NoAssociationLagV1,
)
_TYPES = {record.__name__: record for record in _RECORDS}
T = TypeVar("T", ArtifactManifest, OutputFinding, OutputEvidence, InvocationInput)
FORMAT = "marivo.frame_output/v2"

# One closed Store13 field inventory. No older field set or dataclass fallback.
_FIELDS = {
    "ArtifactManifest": (
        "store_id",
        "artifact_id",
        "session_id",
        "run_id",
        "data",
        "metadata",
        "semantics_version",
    ),
    "ArtifactData": ("schema", "rows", "attachments"),
    "DataSchema": (
        "axes",
        "values",
        "presence",
        "projection_columns",
        "value_paths",
        "presence_paths",
    ),
    "DataAxis": ("token", "columns", "kind", "name", "at"),
    "CalendarDataAxis": ("token", "columns", "kind", "name", "at", "timezone", "frozen"),
    "IntervalDataAxis": ("token", "columns", "kind", "name", "at", "frozen"),
    "FrozenIntervalData": ("directory_name", "rule_version"),
    "BuiltinCalendarData": ("unit", "step"),
    "NamedCalendarData": ("identity", "snapshot_digest", "level"),
    "FrozenCalendarData": ("definition", "directory_name", "rule_version"),
    "PairedDataAxis": ("token", "columns", "kind", "name", "at", "left", "right", "mapping_name"),
    "DataColumn": ("slot_id", "name", "dtype", "nullable", "unit"),
    "ScalarDataType": ("name",),
    "DecimalDataType": ("precision", "scale"),
    "TimestampDataType": ("unit", "timezone"),
    "DurationDataType": ("unit",),
    "ParquetHandle": ("receipt", "payload_digest"),
    "SavedAttachment": ("name", "schema", "rows"),
    "LocalReceipt": (
        "project_relative_path",
        "file_manifest",
        "realized_row_count",
        "realized_byte_count",
        "parquet_contract_version",
    ),
    "FileEntry": ("relative_path", "size_bytes"),
    "OutputCoordinate": ("slot_id", "value"),
    "OutputFinding": ("finding_id", "value", "coordinates"),
    "OutputEvidence": ("kind", "summary", "values", "coordinates"),
    "InvocationInput": ("result_id",),
    "FrameResultMetadata": ("result_id", "audit", "findings"),
    "FrameAudit": ("source_facts", "evaluation"),
    "EvaluationRecord": ("completed_at", "inputs", "operations", "checks"),
    "SourceInput": ("input_id",),
    "LiteralInput": ("input_id",),
    "MemoryInput": ("input_id", "result_id"),
    "StoredInput": ("input_id", "result_id", "store_id", "session_id", "artifact_id"),
    "AssociationFindingValueV1": (
        "method",
        "coefficient",
        "input_observation_count",
        "null_pair_count",
        "complete_pair_count",
        "lag",
        "causal_claim",
        "kind",
    ),
    "ForecastPointFindingValueV1": (
        "model",
        "interval_level",
        "horizon_ordinal",
        "forecast_value",
        "interval_lower",
        "interval_upper",
        "training_row_count",
        "interval_method",
        "kind",
    ),
    "AssociationLagV1": (
        "lag_offset",
        "selected_for_pair",
        "matched_observation_count",
        "lag_boundary_drop_count",
        "kind",
    ),
    "NoAssociationLagV1": ("kind",),
}


def _wire_fields(record: type[object]) -> tuple[str, ...]:
    return _FIELDS[record.__name__]


def _encode(value: object) -> object:
    if value is None or type(value) in (str, int, float, bool):
        return value
    if isinstance(value, Decimal):
        return {"scalar": "Decimal", "value": str(value)}
    if isinstance(value, (date, datetime)):
        return {"scalar": type(value).__name__, "value": value.isoformat()}
    if type(value) is tuple:
        return [_encode(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type) and type(value) in _RECORDS:
        return {
            "record": type(value).__name__,
            "fields": {name: _encode(getattr(value, name)) for name in _wire_fields(type(value))},
        }
    raise invalid("unsupported terminal output value")


def _matches(value: object, annotation: object) -> bool:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, UnionType):
        return any(_matches(value, arg) for arg in args)
    if origin is Literal:
        return any(type(value) is type(arg) and value == arg for arg in args)
    if origin is tuple:
        if type(value) is not tuple:
            return False
        if len(args) == 2 and args[1] is Ellipsis:
            return all(_matches(item, args[0]) for item in value)
        return len(value) == len(args) and all(
            _matches(item, arg) for item, arg in zip(value, args, strict=True)
        )
    return isinstance(annotation, type) and type(value) is annotation


U = TypeVar("U")


def _construct(constructor: type[U], values: dict[str, object]) -> U:
    return constructor(**values)


def _decode(value: object) -> object:
    if value is None or type(value) in (str, int, float, bool):
        return value
    if type(value) is list:
        return tuple(_decode(item) for item in _array(value))
    if not isinstance(value, dict):
        raise invalid("invalid terminal output encoding")
    if "scalar" in value:
        obj = _obj(value, "scalar value")
        text = _text(obj["value"])
        try:
            if obj["scalar"] == "Decimal":
                number = Decimal(text)
                if number.is_finite() and str(number) == text:
                    return number
            if obj["scalar"] == "date":
                day = date.fromisoformat(text)
                if day.isoformat() == text:
                    return day
            if obj["scalar"] == "datetime":
                instant = datetime.fromisoformat(text)
                if instant.isoformat() == text:
                    return instant
        except (ValueError, InvalidOperation):
            pass
        raise invalid("invalid terminal scalar")
    obj = _obj(value, "record fields")
    record = _TYPES.get(_text(obj["record"]))
    if record is None:
        raise invalid("unknown terminal record; graph formats are not supported")
    body = _obj(obj["fields"], " ".join(_wire_fields(record)))
    hints = get_type_hints(record)
    decoded = {name: _decode(item) for name, item in body.items()}
    if any(not _matches(item, hints[name]) for name, item in decoded.items()):
        raise invalid("terminal record field differs from its exact type")
    return _construct(record, decoded)


def encode(value: ArtifactManifest | OutputFinding | OutputEvidence | InvocationInput) -> str:
    return canonical_json({"format": FORMAT, "value": _encode(value)})


def decode(text: str, expected: type[T]) -> T:
    try:
        envelope = _obj(parse_json(text), "format value")
        if envelope["format"] != FORMAT:
            raise invalid("expected Frame output v2; obsolete output format received")
        value = _decode(envelope["value"])
        if not isinstance(value, expected) or type(value) is not expected:
            raise invalid("terminal envelope has the wrong output record type")
        return value
    except IntegrityError as error:
        if error.stage == "terminal_output":
            raise
        raise invalid(str(error.received)) from None
    except AnalysisError as error:
        raise invalid(str(error.received)) from None
