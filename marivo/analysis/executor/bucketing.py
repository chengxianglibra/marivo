"""Bucket start, report-local bucketing, and bucket post-processing helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

import ibis
import pandas as pd

from marivo.analysis.errors import WindowInvalidError
from marivo.analysis.executor.string_time import _classify_strptime_format, _parse_string_column
from marivo.analysis.executor.windowing import (
    UTC_ZONE,
    BackendDatetimeDecodePolicy,
    EffectiveTimeContext,
    _bucket_output_kind,
    _coerce_bound_datetime,
    _is_day_partition_meta,
    _is_hour_only_partition_meta,
    _resolve_required_prefix_time_field,
    _validate_time_field_dtype,
    _validate_time_field_timezone,
    effective_time_context,
)
from marivo.analysis.windows.grain import _TRUNCATE_CODE, Grain
from marivo.analysis.windows.spec import AbsoluteWindow
from marivo.datasource.engines.base import GENERIC_PROFILE, EngineProfile

_logger = logging.getLogger("marivo.analysis.executor")


def bucket_start_expr(local_ts: Any, grain: Grain) -> Any:
    """Bucket-start expression for a session-local timestamp/date expression."""
    if grain.count == 1:
        if grain.is_day:
            return local_ts.cast("date").name("bucket_start")
        return local_ts.truncate(_TRUNCATE_CODE[grain.unit]).name("bucket_start")
    width = grain.width_seconds()
    day_start = local_ts.truncate("D")
    offset = ((local_ts.epoch_seconds() - day_start.epoch_seconds()) // width) * width
    return (day_start + offset.as_interval("s")).name("bucket_start")


def _timestamp_expr_in_report_timezone(
    ts_expr: Any,
    *,
    column_tz: ZoneInfo,
    report_tz: ZoneInfo,
    window: AbsoluteWindow,
) -> Any:
    start_utc = _coerce_bound_datetime(window.start, tz=report_tz, bound_name="start")
    end_utc = _coerce_bound_datetime(window.end, tz=report_tz, bound_name="end")
    boundaries = _timezone_offset_boundaries(
        start_utc,
        end_utc,
        column_tz=column_tz,
        report_tz=report_tz,
    )
    if len(boundaries) <= 2:
        return _shift_timestamp_expr(
            ts_expr,
            shift_seconds=_report_local_shift_seconds(
                start_utc,
                column_tz=column_tz,
                report_tz=report_tz,
            ),
        )

    branches: list[tuple[Any, Any]] = []
    for segment_start, segment_end in pairwise(boundaries):
        lower = _instant_as_naive_in_zone(segment_start, column_tz)
        upper = _instant_as_naive_in_zone(segment_end, column_tz)
        shifted = _shift_timestamp_expr(
            ts_expr,
            shift_seconds=_report_local_shift_seconds(
                segment_start,
                column_tz=column_tz,
                report_tz=report_tz,
            ),
        )
        branches.append(
            (
                (ts_expr >= ibis.timestamp(lower.isoformat()))
                & (ts_expr < ibis.timestamp(upper.isoformat())),
                shifted,
            )
        )

    fallback = _shift_timestamp_expr(
        ts_expr,
        shift_seconds=_report_local_shift_seconds(
            start_utc,
            column_tz=column_tz,
            report_tz=report_tz,
        ),
    )
    first, *rest = branches
    return ibis.cases(first, *rest, else_=fallback)


def _report_local_shift_seconds(
    instant_utc: datetime,
    *,
    column_tz: ZoneInfo,
    report_tz: ZoneInfo,
) -> int:
    column_offset = instant_utc.astimezone(column_tz).utcoffset()
    report_offset = instant_utc.astimezone(report_tz).utcoffset()
    column_seconds = int(column_offset.total_seconds()) if column_offset is not None else 0
    report_seconds = int(report_offset.total_seconds()) if report_offset is not None else 0
    return report_seconds - column_seconds


def _shift_timestamp_expr(ts_expr: Any, *, shift_seconds: int) -> Any:
    if shift_seconds == 0:
        return ts_expr
    return ts_expr + ibis.interval(seconds=shift_seconds)


def _instant_as_naive_in_zone(instant_utc: datetime, tz: ZoneInfo) -> datetime:
    return instant_utc.astimezone(tz).replace(tzinfo=None)


def _timezone_offset_boundaries(
    start_utc: datetime,
    end_utc: datetime,
    *,
    column_tz: ZoneInfo,
    report_tz: ZoneInfo,
) -> list[datetime]:
    if end_utc <= start_utc:
        return [start_utc, end_utc]

    boundaries = {start_utc, end_utc}
    for tz in {column_tz, report_tz}:
        boundaries.update(_timezone_offset_transitions(start_utc, end_utc, tz))
    return sorted(boundaries)


def _timezone_offset_transitions(
    start_utc: datetime,
    end_utc: datetime,
    tz: ZoneInfo,
) -> list[datetime]:
    transitions: list[datetime] = []
    step = timedelta(hours=1)
    left = start_utc
    left_offset = left.astimezone(tz).utcoffset()
    cursor = min(left + step, end_utc)
    while cursor <= end_utc:
        cursor_offset = cursor.astimezone(tz).utcoffset()
        if cursor_offset != left_offset:
            transition = _bisect_timezone_transition(left, cursor, tz, left_offset)
            if start_utc < transition < end_utc:
                transitions.append(transition)
            left_offset = cursor_offset
        left = cursor
        if cursor == end_utc:
            break
        cursor = min(cursor + step, end_utc)
    return transitions


def _bisect_timezone_transition(
    left: datetime,
    right: datetime,
    tz: ZoneInfo,
    left_offset: timedelta | None,
) -> datetime:
    while right - left > timedelta(seconds=1):
        midpoint = left + (right - left) / 2
        if midpoint.astimezone(tz).utcoffset() == left_offset:
            left = midpoint
        else:
            right = midpoint
    return right.replace(microsecond=0)


def bucket_time_expression(
    raw: Any,
    *,
    time_meta: Any,
    grain: Grain,
    report_tz: ZoneInfo,
    datasource_read_tz: ZoneInfo,
    window: AbsoluteWindow | None,
    profile: EngineProfile = GENERIC_PROFILE,
) -> Any:
    """Return a report-local bucket expression for a timestamp-like time field."""
    if time_meta.data_type in {"datetime", "timestamp"}:
        if window is None:
            raise WindowInvalidError(
                message="bucket_time_expression requires a window for timestamp bucketing.",
                details={"data_type": time_meta.data_type},
            )
        return _local_bucket_expr(
            raw,
            time_meta=time_meta,
            grain=grain,
            report_tz=report_tz,
            datasource_read_tz=datasource_read_tz,
            window=window,
        )
    if (
        time_meta.data_type in {"string", "integer"}
        and time_meta.format is not None
        and time_meta.format.startswith("%")
        and getattr(time_meta, "parse_kind", None) == "strptime"
    ):
        if window is None:
            raise WindowInvalidError(
                message="bucket_time_expression requires a window for strptime bucketing.",
                details={"data_type": time_meta.data_type, "format": time_meta.format},
            )
        parsed = _parse_string_column(raw, time_meta, profile=profile)
        classification = _classify_strptime_format(time_meta.format)
        grain_matches_classification = (
            (grain.is_day and classification == "day")
            or (grain.unit == "hour" and grain.count == 1 and classification == "hour")
            or (grain.unit == "minute" and grain.count == 1 and classification == "minute")
        )
        context = effective_time_context(
            time_meta,
            report_tz=report_tz,
            datasource_read_tz=datasource_read_tz,
            field_expr=raw,
        )
        if grain_matches_classification and (
            classification == "day"
            or context.effective_column_tz is None
            or context.effective_column_tz == report_tz
        ):
            return parsed
        column_tz = context.effective_column_tz or datasource_read_tz
        local_parsed = _timestamp_expr_in_report_timezone(
            parsed,
            column_tz=column_tz,
            report_tz=report_tz,
            window=window,
        )
        return bucket_start_expr(local_parsed, grain)
    raise WindowInvalidError(
        message="bucket_time_expression requires a datetime, timestamp, or strptime time field.",
        details={"data_type": time_meta.data_type},
    )


def _local_bucket_expr(
    raw: Any,
    *,
    time_meta: Any,
    grain: Grain,
    report_tz: ZoneInfo,
    datasource_read_tz: ZoneInfo,
    window: AbsoluteWindow,
) -> Any:
    """Compute a bucket-start expression that aligns instant or declared-naive
    timestamp fields to report-local calendar boundaries.

    For naive timestamp columns with a declared timezone, the shift converts
    from declared-local to report-local.  For naive timestamp columns with no
    declaration, the column is already in datasource-read-local space so the
    shift converts from read-local to report-local.
    """
    data_type = time_meta.data_type
    if data_type in {"datetime", "timestamp"}:
        ts_expr = raw
        context = effective_time_context(
            time_meta,
            report_tz=report_tz,
            datasource_read_tz=datasource_read_tz,
            field_expr=raw,
        )
        column_tz = context.effective_column_tz or datasource_read_tz
        if context.declared_tz is None and context.actual_field_tz is None:
            _logger.warning(
                "Time dimension %r has no declared timezone for naive %r column; "
                "assuming datasource read timezone %s. Add timezone= to @ms.time_dimension "
                "to avoid silent misalignment.",
                getattr(time_meta, "semantic_id", "?"),
                data_type,
                getattr(datasource_read_tz, "key", str(datasource_read_tz)),
            )
    else:
        raise WindowInvalidError(
            message=f"_local_bucket_expr only supports datetime/timestamp, "
            f"got data_type={data_type!r}",
        )

    local_expr = _timestamp_expr_in_report_timezone(
        ts_expr,
        column_tz=column_tz,
        report_tz=report_tz,
        window=window,
    )
    return bucket_start_expr(local_expr, grain)


def _prefix_date_expr(
    table: ibis.Table, prefix_field_ir: Any, *, profile: EngineProfile = GENERIC_PROFILE
) -> Any:
    """Compute a date ibis expression from a day-level required_prefix field."""
    time_meta = prefix_field_ir.time_meta
    raw = prefix_field_ir.fn(table)
    if time_meta.data_type == "date":
        return raw
    if time_meta.data_type in {"string", "integer"}:
        return _parse_string_column(raw, time_meta, profile=profile)
    if time_meta.data_type in {"datetime", "timestamp"}:
        return raw.cast("date")
    raise WindowInvalidError(
        message=f"required_prefix field '{prefix_field_ir.name}' has unsupported "
        f"data_type {time_meta.data_type!r} for day-level bucketing",
    )


def combine_prefix_hour_to_timestamp(
    table: ibis.Table,
    *,
    hour_field_ir: Any,
    dataset_ir: Any,
    profile: EngineProfile = GENERIC_PROFILE,
) -> Any:
    """Combine a day-level prefix field and an hour-only column into a timestamp.

    Returns an ibis timestamp expression representing the start of each hour
    sample point.
    """
    prefix_field_ir = _resolve_required_prefix_time_field(dataset_ir, hour_field_ir)
    if prefix_field_ir is None or prefix_field_ir.time_meta is None:
        raise WindowInvalidError(
            message=f"hour_prefix time field '{hour_field_ir.name}' requires a "
            f"day-level prefix for timestamp construction",
        )
    prefix_date = _prefix_date_expr(table, prefix_field_ir, profile=profile)
    raw = hour_field_ir.fn(table)
    time_meta = hour_field_ir.time_meta
    hour_int = raw.cast("int") if time_meta.data_type == "string" else raw
    return prefix_date.cast("timestamp") + (hour_int * 3600).as_interval("s")


def _apply_hour_only_bucket(
    table: ibis.Table,
    *,
    raw: Any,
    field_ir: Any,
    window: AbsoluteWindow,
    report_tz: ZoneInfo,
    datasource_read_tz: ZoneInfo,
    dataset_ir: Any,
    profile: EngineProfile = GENERIC_PROFILE,
) -> ibis.Table:
    """Bucket for hour-only string/integer time fields that use required_prefix."""
    time_meta = field_ir.time_meta
    grain = window.grain
    assert grain is not None
    field_grain = Grain(count=1, unit=time_meta.granularity)

    prefix_field_ir = _resolve_required_prefix_time_field(dataset_ir, field_ir)
    if prefix_field_ir is None or prefix_field_ir.time_meta is None:
        raise WindowInvalidError(
            message=f"hour-only time field '{field_ir.name}' requires a day-level "
            f"required_prefix for bucket computation",
        )

    if grain < field_grain:
        raise WindowInvalidError(
            message=f"requested grain {grain.to_token()!r} is finer than time field "
            f"'{field_ir.name}' granularity '{time_meta.granularity}'",
        )

    prefix_raw = prefix_field_ir.fn(table)

    # Grain coarser than field: use prefix value directly (no parse, no truncate)
    if grain > field_grain:
        if _is_day_partition_meta(prefix_field_ir.time_meta) and grain.is_day:
            return table.mutate(bucket_start=prefix_raw.name("bucket_start"))
        prefix_date = _prefix_date_expr(table, prefix_field_ir, profile=profile)
        return table.mutate(bucket_start=bucket_start_expr(prefix_date, grain))

    # Grain matches field: concatenate prefix + hour into sortable string.
    # lpad(2, "0") unconditionally handles both "1" and "01" source columns.
    hour_str = raw if time_meta.data_type == "string" else raw.cast("string")
    hour_str = hour_str.lpad(2, "0")
    prefix_fmt = prefix_field_ir.time_meta.format
    if prefix_fmt == "%Y-%m-%d":
        bucket = (prefix_raw + ibis.literal("-") + hour_str).name("bucket_start")
    elif prefix_fmt == "%Y%m%d":
        bucket = (prefix_raw + hour_str).name("bucket_start")
    else:
        # Fallback for unusual prefix formats
        prefix_date = _prefix_date_expr(table, prefix_field_ir, profile=profile)
        hour_int = raw.cast("int") if time_meta.data_type == "string" else raw
        date_ts = prefix_date.cast("timestamp")
        bucket = (date_ts + (hour_int * 3600).as_interval("s")).name("bucket_start")
    return table.mutate(bucket_start=bucket)


def ensure_bucket_start_timestamp(
    series: pd.Series,
    *,
    time_meta: Any,
    dataset_ir: Any,
    grain: Grain | None,
    report_tz: ZoneInfo | None = None,
    time_context: EffectiveTimeContext | None = None,
    backend_datetime_decode_policy: BackendDatetimeDecodePolicy = "local_naive_label",
) -> pd.Series:
    """Normalize bucket_start values after SQL execution.

    Handles two post-execution normalizations:

    1. String-to-timestamp conversion for hour-only partition fields
       (produced by ``_apply_hour_only_bucket``).
    2. Timezone normalization: tz-aware bucket_start values (e.g. from
       ClickHouse ``Nullable(DateTime)`` columns) are converted to
       report-local naive timestamps so that downstream consumers see
       business-timezone dates, not UTC.
    """
    if grain is None:
        return series

    if time_context is not None:
        report_tz = report_tz or time_context.report_tz
        backend_datetime_decode_policy = time_context.backend_datetime_decode_policy
        bucket_output_kind = time_context.bucket_output_kind
    else:
        bucket_output_kind = _bucket_output_kind(time_meta)

    # Timezone normalization: convert tz-aware timestamps to report-local naive.
    if report_tz is not None and isinstance(series.dtype, pd.DatetimeTZDtype):
        converted: pd.Series = series.dt.tz_convert(report_tz).dt.tz_localize(None)
        return converted

    if (
        report_tz is not None
        and backend_datetime_decode_policy == "utc_naive_instant"
        and bucket_output_kind == "report_local_timestamp"
        and pd.api.types.is_datetime64_any_dtype(series)
    ):
        converted = series.dt.tz_localize(UTC_ZONE).dt.tz_convert(report_tz).dt.tz_localize(None)
        return converted

    if not pd.api.types.is_string_dtype(series):
        return series

    if time_meta is None or not _is_hour_only_partition_meta(time_meta):
        return series

    required_prefix = getattr(time_meta, "required_prefix", None)
    if not required_prefix:
        return series

    # Look up the prefix field directly by name/semantic_id.
    prefix_field_ir = None
    for field in dataset_ir.fields.values():
        if getattr(field, "is_time", False) and (
            field.name == required_prefix or field.semantic_id == required_prefix
        ):
            prefix_field_ir = field
            break
    if prefix_field_ir is None or prefix_field_ir.time_meta is None:
        return series

    prefix_fmt = prefix_field_ir.time_meta.format

    # Map (prefix_fmt, grain) -> strptime format for the concatenated bucket_start.
    # _apply_hour_only_bucket only produces string bucket_start for count=1
    # hour grain or day grain; multi-hour grains skip string concatenation
    # and use the timestamp fallback path instead.
    if grain.unit == "hour" and grain.count == 1:
        fmt_map = {
            "%Y-%m-%d": "%Y-%m-%d-%H",
            "%Y%m%d": "%Y%m%d%H",
        }
    elif grain.is_day:
        fmt_map = {
            "%Y-%m-%d": "%Y-%m-%d",
            "%Y%m%d": "%Y%m%d",
        }
    else:
        return series

    if prefix_fmt is None:
        return series

    strptime_fmt = fmt_map.get(prefix_fmt)
    if strptime_fmt is None:
        return series

    return pd.to_datetime(series, format=strptime_fmt, errors="coerce")


def apply_time_series_bucket(
    table: ibis.Table,
    *,
    field_ir: Any,
    window: AbsoluteWindow,
    report_tz: ZoneInfo,
    datasource_read_tz: ZoneInfo,
    dataset_ir: Any | None = None,
    profile: EngineProfile = GENERIC_PROFILE,
) -> ibis.Table:
    if field_ir.time_meta is None:
        raise WindowInvalidError(message=f"field '{field_ir.name}' has no time metadata")
    raw = field_ir.fn(table)
    _validate_time_field_timezone(raw, field_ir.time_meta)
    _validate_time_field_dtype(raw, field_ir.time_meta)
    time_meta = field_ir.time_meta
    data_type = time_meta.data_type
    fmt = time_meta.format
    if window.grain is None:
        return table

    # Strptime format: parse the column into a temporal type
    # (excludes hour-only fields that rely on required_prefix)
    if (
        data_type in {"string", "integer"}
        and fmt is not None
        and fmt.startswith("%")
        and not _is_hour_only_partition_meta(time_meta)
    ):
        parsed = _parse_string_column(raw, time_meta, profile=profile)
        classification = _classify_strptime_format(fmt)
        grain_matches_classification = (
            (window.grain.is_day and classification == "day")
            or (
                window.grain.unit == "hour" and window.grain.count == 1 and classification == "hour"
            )
            or (
                window.grain.unit == "minute"
                and window.grain.count == 1
                and classification == "minute"
            )
        )
        if grain_matches_classification:
            context = effective_time_context(
                time_meta,
                report_tz=report_tz,
                datasource_read_tz=datasource_read_tz,
                field_expr=raw,
            )
            if (
                classification == "day"
                or context.effective_column_tz is None
                or context.effective_column_tz == report_tz
            ):
                bucket = parsed.name("bucket_start")
            else:
                column_tz = context.effective_column_tz or datasource_read_tz
                local_parsed = _timestamp_expr_in_report_timezone(
                    parsed,
                    column_tz=column_tz,
                    report_tz=report_tz,
                    window=window,
                )
                bucket = bucket_start_expr(local_parsed, window.grain)
        else:
            context = effective_time_context(
                time_meta,
                report_tz=report_tz,
                datasource_read_tz=datasource_read_tz,
                field_expr=raw,
            )
            column_tz = context.effective_column_tz or datasource_read_tz
            local_parsed = _timestamp_expr_in_report_timezone(
                parsed,
                column_tz=column_tz,
                report_tz=report_tz,
                window=window,
            )
            bucket = bucket_start_expr(local_parsed, window.grain)
        return table.mutate(bucket_start=bucket)

    # Timestamp: bucket in report-local calendar
    if data_type in {"datetime", "timestamp"}:
        bucket = _local_bucket_expr(
            raw,
            time_meta=time_meta,
            grain=window.grain,
            report_tz=report_tz,
            datasource_read_tz=datasource_read_tz,
            window=window,
        )
        return table.mutate(bucket_start=bucket)

    # Hour-only string/integer with required_prefix
    if data_type in {"string", "integer"} and _is_hour_only_partition_meta(time_meta):
        return _apply_hour_only_bucket(
            table,
            raw=raw,
            field_ir=field_ir,
            window=window,
            report_tz=report_tz,
            datasource_read_tz=datasource_read_tz,
            dataset_ir=dataset_ir,
            profile=profile,
        )

    # Date type: simple truncate or cast
    if data_type == "date":
        bucket = bucket_start_expr(raw, window.grain)
        return table.mutate(bucket_start=bucket)

    # Unhandled type/format combination
    raise WindowInvalidError(
        message=f"cannot compute bucket for time field '{field_ir.name}' with "
        f"data_type={data_type!r}, format={fmt!r}",
    )
