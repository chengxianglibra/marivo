"""Native UTC Event instants with explicit wall-clock and precision authority."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.semantic.ir import DatetimeParse, TargetDimensionContract, TimestampParse
from marivo.semantic.validator import Registry

_UTC_ZONES = frozenset({"UTC", "Etc/UTC", "GMT", "Etc/GMT"})


def _timezone_signature(zone: str, value: datetime) -> datetime:
    # Ibis uses this signature to emit DuckDB timezone(); the Python body never executes.
    raise NotImplementedError("native timezone signature only")


_localize_utc: Callable[[str, ir.TimestampValue], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _timezone_signature,
    name="timezone",
    signature=((dt.string, dt.timestamp), dt.Timestamp(timezone="UTC")),
)


def _unsupported(received: str) -> DatasetCompilationError:
    return DatasetCompilationError(
        expected="a native Event date/timestamp with exact instant authority and at most microsecond precision",
        received=received,
        repair="Provide a native timestamp instant, or declare UTC (UTC, Etc/UTC, GMT, Etc/GMT) for a native naive date/timestamp. Parse strings and resolve non-UTC wall-clock ambiguity in the governed source, preserving precision at or below microseconds.",
        location="events.match.occurred_at",
        message="The source-required Event recipe does not support this temporal representation.",
    )


def event_instant(
    value: ir.Value, axis: TargetDimensionContract, registry: Registry
) -> ir.TimestampValue:
    """Normalize one admitted native occurrence axis to UTC without data access.

    A timestamp that already carries a zone represents an instant. Naive native
    timestamps and dates require explicit UTC metadata; dates mean UTC midnight.
    The builtin localization is independent of the DuckDB connection timezone.
    """
    declaration = registry.dimensions.get(axis.ref.path)
    if (
        declaration is None
        or not axis.is_time_dimension
        or axis.logical_type != "timestamp"
        or declaration.entity != axis.entity_ref.path
        or declaration.source_column != axis.source_column
    ):
        raise _unsupported("the occurrence axis lacks its exact governed timestamp binding")
    parse = declaration.parse
    if parse is not None and type(parse) not in (DatetimeParse, TimestampParse):
        raise _unsupported("a string, date-truncating, or custom temporal parser")
    declared_zone = parse.timezone if isinstance(parse, (DatetimeParse, TimestampParse)) else None
    if declared_zone != axis.timezone:
        raise _unsupported("the captured and current occurrence timezone declarations differ")
    if not isinstance(value, (ir.DateValue, ir.TimestampValue)):
        raise _unsupported("a non-native date/timestamp source column")
    physical = value.type()
    if isinstance(physical, dt.Timestamp):
        if physical.scale is not None and physical.scale > 6:
            raise _unsupported("source timestamp precision exceeds exact native Event microseconds")
        if physical.timezone is not None:
            normalized = value.cast(dt.Timestamp(timezone="UTC"))
            if not isinstance(normalized, ir.TimestampValue):
                raise _unsupported("the native timestamp cast did not retain temporal type")
            return normalized
    if declared_zone not in _UTC_ZONES:
        raise _unsupported("a naive occurrence column without explicit supported UTC authority")
    naive = value.cast(dt.timestamp)
    if not isinstance(naive, ir.TimestampValue):
        raise _unsupported("the native UTC wall-clock input did not retain temporal type")
    return _localize_utc("UTC", naive)
