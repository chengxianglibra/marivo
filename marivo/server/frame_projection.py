"""Project only explicitly admitted public Frame facts and exact scalar carriers."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pyarrow as pa

from marivo.analysis.core import frame_interpretation as facts
from marivo.analysis.core.data_types import (
    DataType,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.frame_page import (
    FrameCalendarPeriod,
    FrameComparisonPeriod,
    FrameIntervalPeriod,
    FramePage,
    FramePairedPeriod,
    FramePeriod,
    FramePeriodItem,
)
from marivo.analysis.core.frame_schema import CoordinateExportColumn, FieldExportColumn, FrameSchema
from marivo.analysis.core.frame_source_reads import (
    FrameSourceRead,
    FrameSourceReadPeriod,
    FrameSourceReadScope,
)
from marivo.analysis.core.lineage import ColumnLineage, FrameLineage, SemanticObjectIdentity
from marivo.server.wire import JsonObject, JsonValue, ServiceError

_AXIS = "token name role keys at"
# This is a wire allowlist. Adding a member to a Python type cannot expose it over HTTP.
_FIELDS: dict[type[object], str] = {
    facts.FrameField: "slot_id name dtype nullable unit",
    facts.FrameInstantSourceTime: "kind axis physical_type consumption_timezone",
    facts.FrameWallClockSourceTime: "kind axis physical_type consumption_timezone timezone origin inherited_from",
    facts.FrameCivilDateSourceTime: "kind axis physical_type consumption_timezone timezone origin",
    facts.FrameBuiltinGrain: "kind unit step",
    facts.FrameCalendarEndpoint: "kind name at timezone grain",
    facts.FrameIntervalEndpoint: "kind name at timezone",
    facts.FrameRowAttributeTime: "kind field",
    facts.FrameOrdinaryAxisSchema: _AXIS,
    facts.FrameCalendarAxisSchema: _AXIS + " timezone grain",
    facts.FrameIntervalAxisSchema: _AXIS + " timezone",
    facts.FramePairedAxisSchema: _AXIS + " left right",
    facts.FrameAxisTime: "kind axis_token endpoint",
    facts.FrameComparedTime: "kind comparison_id endpoint",
    facts.FrameFixedTime: "kind start end timezone",
    facts.FrameAllHistoryWindow: "kind",
    facts.FrameSinceWindow: "kind start",
    facts.FrameToDateWindow: "kind grain",
    facts.FrameTrailingDurationWindow: "kind duration",
    facts.FrameTrailingPeriodsWindow: "kind count",
    facts.FrameReportWindowEndpoint: "kind",
    facts.FrameContributionSampleWindowEndpoint: "layer grain timezone kind",
    facts.FrameFieldSampleWindowEndpoint: "layer field grain timezone kind",
    facts.FrameClockedContribution: "output occurrence metric root field path anchor source report report_effect window samples report_axis report_cells window_endpoint kind",
    facts.FrameIndependentContribution: "output occurrence metric root source report report_effect window samples report_axis report_cells window_endpoint kind",
    facts.FrameObservedTimeSemantics: "contributions kind",
    facts.FrameDerivedTimeSemantics: "contributions kind",
    facts.FrameObservationContext: "kind subject time window",
    facts.FrameAxisGroup: "kind axis_token",
    facts.FrameFixedGroup: "kind fixed",
    facts.FrameStatisticContext: "kind statistic groups row_scope",
    facts.FrameNamedValueInput: "kind name subject",
    facts.FrameDerivedValueInput: "kind origins",
    facts.FrameShareContext: "kind numerator groups along row_scope",
    facts.FramePairStatisticContext: "kind statistic x y groups row_scope",
    facts.FrameAlignmentAxis: "axis_token endpoint",
    facts.FrameAlignmentSide: "values presence coordinates",
    facts.FrameAlignment: "input_id alignment_id left right",
    facts.FrameComparisonValue: "role name",
    facts.FrameComparisonGroup: "group_id name values current_context baseline_context",
    facts.FrameComparisonPresence: "current baseline enclosing",
    facts.FrameOrdinaryComparisonBinding: "kind",
    facts.FrameCalendarComparisonBinding: "kind current_axis_token current_endpoint baseline",
    facts.FrameIntervalComparisonBinding: "kind current_axis_token current_endpoint baseline",
    facts.FrameComparison: "input_id comparison_id alias groups binding presence current_fixed_axes baseline_fixed_axes",
    facts.FrameLevelTarget: "kind name context",
    facts.FrameChangeTarget: "kind comparison_id group_id",
    facts.FrameDecompositionField: "field_kind role name",
    facts.FrameDecomposition: "decomposition_id target groups row_scope outputs",
    ColumnLineage: "value_origins context_origins",
    FrameLineage: "input_id values axes presence",
    SemanticObjectIdentity: "kind path definition_id",
    FrameSchema: "input_id values presence axes layout fixed_axes value_contexts value_time_semantics source_times alignments comparisons decompositions",
    FrameSourceRead: "read_id completed_at scopes returned_rows",
    FrameSourceReadPeriod: "start end timezone",
    FrameSourceReadScope: "source period",
    FieldExportColumn: "kind name slot_id",
    CoordinateExportColumn: "kind name axis_token endpoint part",
}


def _unencodable(location: str) -> ServiceError:
    return ServiceError(
        "unencodable_value",
        "The saved field cannot be represented by the v1 API.",
        location,
        "Use a compatible Runtime or save a Frame with supported output types.",
        "analysis.analysis_frame.schema",
    )


def scalar(value: object, location: str, *, exact: bool) -> JsonValue:
    if isinstance(value, pa.Scalar):
        if not value.is_valid:
            return None
        if isinstance(value, pa.DurationScalar):
            return str(value.value)
        try:
            decoded = value.as_py()
            if (
                isinstance(value, pa.TimestampScalar)
                and value.type.unit == "ns"
                and decoded is pd.NaT
            ):
                # pandas reserves int64 minimum for NaT; Arrow keeps its validity
                # separately. Decode whole seconds and retain the exact fraction.
                seconds, nanoseconds = divmod(value.value, 1_000_000_000)
                stamp = pa.scalar(seconds, type=pa.timestamp("s", tz=value.type.tz)).as_py()
                whole = stamp.isoformat()
                return f"{whole[:19]}.{nanoseconds:09d}{whole[19:]}"
            return scalar(decoded, location, exact=exact)
        except (ValueError, OverflowError, KeyError):
            raise _unencodable(location) from None
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return str(value) if exact else value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {"float": "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")}
    if isinstance(value, Decimal):
        if value.is_finite():
            return format(value, "f")
        raise _unencodable(location)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, timedelta):
        ticks = (value.days * 86400 + value.seconds) * 1000000 + value.microseconds
        return {"unit": "us", "ticks": str(ticks)}
    if isinstance(value, tuple):
        return [scalar(item, location, exact=exact) for item in value]
    raise _unencodable(location)


def fact(value: object, location: str = "frame") -> JsonValue:
    """Encode the closed public fact graph, including complete key values."""
    if isinstance(value, ScalarDataType):
        return {"kind": value.name}
    if isinstance(value, DecimalDataType):
        return {"kind": "decimal", "precision": value.precision, "scale": value.scale}
    if isinstance(value, TimestampDataType):
        return {"kind": "timestamp", "unit": value.unit, "timezone": value.timezone}
    if isinstance(value, DurationDataType):
        return {"kind": "duration", "unit": value.unit}
    if isinstance(value, facts.FrameFixedAxis):
        return {
            "axis": fact(value.axis, location),
            "origins": fact(value.origins, location),
            "key": scalar(value.key, f"{location}.key", exact=True),
        }
    fields = _FIELDS.get(type(value))
    if fields is not None:
        result = {}
        for name in fields.split():
            item: object = getattr(value, name)
            field_location = f"{location}.{name}"
            result[name] = fact(item, field_location)
        return result
    if isinstance(value, tuple):
        return [fact(item, location) for item in value]
    if isinstance(value, Mapping):
        return {str(name): fact(item, f"{location}.{name}") for name, item in value.items()}
    return scalar(value, location, exact=False)


def typed_scalar(value: object, location: str, dtype: DataType | None = None) -> JsonObject:
    if dtype is None:
        if isinstance(value, bool):
            dtype = ScalarDataType("boolean")
        elif isinstance(value, str):
            dtype = ScalarDataType("string")
        elif isinstance(value, int):
            dtype = ScalarDataType("int64")
        elif isinstance(value, float):
            dtype = ScalarDataType("float64")
        else:
            raise _unencodable(location)
    return {"dtype": fact(dtype, location), "value": scalar(value, location, exact=True)}


def _period_endpoint(item: FramePeriod, dtypes: tuple[DataType, ...]) -> JsonObject:
    result: JsonObject = {
        "kind": item.kind,
        "timezone": item.timezone,
        "key": [
            typed_scalar(value, "periods.key", dtype)
            for value, dtype in zip(item.key, dtypes, strict=True)
        ],
    }
    if isinstance(item, FrameCalendarPeriod):
        result.update(
            label=typed_scalar(item.label, "periods.label"),
            ordinal=item.ordinal,
            nominal_ordinal=item.nominal_ordinal,
            selection=item.selection,
            cutoff=item.cutoff,
        )
    else:
        result.update(start=item.start.isoformat(), end=item.end.isoformat())
    return result


def period(
    item: FramePeriodItem,
    dtypes: tuple[DataType, ...],
    left: tuple[DataType, ...],
    right: tuple[DataType, ...],
) -> JsonObject:
    if isinstance(item, (FrameCalendarPeriod, FrameIntervalPeriod)):
        return _period_endpoint(item, dtypes)
    result: JsonObject = {
        "kind": item.kind,
        "key": [
            typed_scalar(value, "periods.key", dtype)
            for value, dtype in zip(item.key, dtypes, strict=True)
        ],
    }
    if isinstance(item, FramePairedPeriod):
        result.update(
            left=None if item.left is None else _period_endpoint(item.left, left),
            right=None if item.right is None else _period_endpoint(item.right, right),
        )
    elif isinstance(item, FrameComparisonPeriod):
        result.update(
            current=_period_endpoint(item.current, left),
            baseline=None if item.baseline is None else _period_endpoint(item.baseline, right),
            absent_reason=item.absent_reason,
        )
    return result


def page_rows(page: FramePage) -> Iterator[list[JsonValue]]:
    rows = page.to_pandas()
    columns = [
        iter(pa.array(rows[column.name]))
        if isinstance(rows[column.name].dtype, pd.ArrowDtype)
        else iter(rows[column.name])
        for column in page.layout
    ]
    for _ in range(len(page)):
        row: list[JsonValue] = []
        for column, series in zip(page.layout, columns, strict=True):
            value = next(series)
            location = f"data.rows.{column.name}"
            if isinstance(column, CoordinateExportColumn) and column.part == "label":
                if isinstance(value, pa.Scalar):
                    value = value.as_py()
                row.append(
                    None if value is None or value is pd.NA else typed_scalar(value, location)
                )
            else:
                row.append(scalar(value, location, exact=True))
        yield row
