"""Pure engine-agnostic expressions landing governed time in one explicit civil boundary zone."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._time_formats import ORDERED_TIME_FORMATS
from marivo._timezones import normalize_timezone, same_timezone
from marivo.analysis.compiler.errors import DatasetCompilationError, compilation_error
from marivo.analysis.core.time_authority import (
    CivilDateSourceTime,
    InstantSourceTime,
    SourceTimeAuthority,
    WallClockSourceTime,
)
from marivo.datasource._temporal_expressions import (
    localize,
    timestamp,
)
from marivo.semantic._physical_contracts import time_type_issue
from marivo.semantic._source_timezone import DeclaredSourceTimezone
from marivo.semantic.ir import (
    DateParse,
    HourPrefixParse,
    StrptimeParse,
    TargetDimensionContract,
    TargetEntityContract,
    TimestampParse,
    is_time_bearing_format,
)
from marivo.semantic.validator import Registry


def _clickhouse_date32_literal(value: str) -> date:
    """Return a ClickHouse Date32 literal without Date's 1970-2149 bounds."""
    raise NotImplementedError("native ClickHouse Date32 literal")


_clickhouse_date32 = ibis.udf.scalar.builtin(
    _clickhouse_date32_literal,
    name="toDate32",
    signature=((dt.string,), dt.date),
)


def ordered_time_literal(point: datetime, fmt: str, *, strict: bool = False) -> str | None:
    """Encode the first declared clock tick at or after a normalized boundary."""
    step = ORDERED_TIME_FORMATS[fmt]
    midnight = point.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = point - midnight
    ticks, remainder = divmod(elapsed.seconds * 1_000_000 + elapsed.microseconds, step * 1_000_000)
    try:
        point = midnight + timedelta(seconds=(ticks + (strict or bool(remainder))) * step)
    except OverflowError:
        return None
    tokens = {
        "%Y": f"{point.year:04d}",
        "%m": f"{point.month:02d}",
        "%d": f"{point.day:02d}",
        "%H": f"{point.hour:02d}",
        "%M": f"{point.minute:02d}",
        "%S": f"{point.second:02d}",
    }
    for token, text in tokens.items():
        fmt = fmt.replace(token, text)
    return fmt


TimeComparison = Literal[">=", "<", ">", "<=", "==", "!="]
TimePredicateUse = Literal["value", "filter"]


def _nullable_constant(value: ir.Value, truth: bool) -> ir.BooleanValue:
    result = ibis.ifelse(value.isnull(), ibis.null().cast("boolean"), truth)
    assert isinstance(result, ir.BooleanValue)
    return result


def _complement(value: ir.BooleanValue) -> ir.BooleanValue:

    node = value.op()
    if isinstance(node, (ops.GreaterEqual, ops.Greater)):
        operation = ops.Less if isinstance(node, ops.GreaterEqual) else ops.LessEqual
        result = operation(node.left, node.right).to_expr()
        assert isinstance(result, ir.BooleanValue)
        return result
    return ~value


def _raw_lineage(value: ir.Value) -> bool:
    """Admit only physical fields and transparent field projections."""

    node = value.op()
    while isinstance(node, ops.Field):
        if isinstance(node.rel, ops.Project):
            node = node.rel.values[node.name]
        elif isinstance(node.rel, (ops.Filter, ops.Reference)):
            node = ops.Field(node.rel.parent, node.name)
        else:
            return isinstance(node.rel, ops.PhysicalTable)
    return False


def _boundary_instant(bound: date | datetime, authority: SourceTimeAuthority) -> datetime:
    point = bound if isinstance(bound, datetime) else datetime.combine(bound, time())
    if point.tzinfo is None:
        point = point.replace(
            tzinfo=normalize_timezone(
                authority.timezone if authority.kind == "civil_date" else "UTC"
            ).tz
        )
    return point.astimezone(timezone.utc)


def _scalar_cutoff(
    raw: ir.Value, point: datetime, *, inclusive: bool, engine: str = "duckdb"
) -> ir.BooleanValue | None:
    """Compare a physical clock to one translated lower cutoff."""
    physical = raw.type()
    if physical.is_date():
        midnight = point.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            day = (midnight + timedelta(days=not inclusive or point != midnight)).date()
        except OverflowError:
            return _nullable_constant(raw, False)
        # Ibis represents both Date and Date32 as date. Only cutoffs outside
        # Date's range require the wider ClickHouse literal.
        literal = (
            _clickhouse_date32(day.isoformat())
            if engine == "clickhouse" and not date(1970, 1, 1) <= day <= date(2149, 6, 6)
            else ibis.literal(day, type=physical)
        )
        return raw >= literal
    if isinstance(physical, dt.Timestamp):
        scale = physical.scale
        if scale is not None and scale < 6:
            step = 10 ** (6 - scale)
            remainder = point.microsecond % step
            if remainder:
                try:
                    point += timedelta(microseconds=step - remainder)
                except OverflowError:
                    return _nullable_constant(raw, False)
                inclusive = True
        literal = ibis.literal(point, type=physical)
        return raw >= literal if inclusive else raw > literal
    return None


def _ordered_encoding(value: ir.Value, parse: StrptimeParse) -> bool:
    physical = value.type()
    return (
        parse.format in ORDERED_TIME_FORMATS
        and (physical.is_string() or physical.is_integer())
        and (not physical.is_integer() or parse.format.replace("%", "").isalnum())
    )


def _encoded_cutoff(
    raw: ir.Value, point: datetime, parse: StrptimeParse, *, inclusive: bool
) -> ir.BooleanValue | None:
    """Compare a canonical physical encoding to its first eligible source tick."""
    physical = raw.type()
    if not _ordered_encoding(raw, parse):
        return None
    text = ordered_time_literal(point, parse.format, strict=not inclusive)
    if text is None:
        return _nullable_constant(raw, False)
    if physical.is_integer():
        assert isinstance(physical, dt.Integer)
        value = int(text)
        if value > physical.bounds.upper:
            return _nullable_constant(raw, False)
        if value <= physical.bounds.lower:
            return _nullable_constant(raw, True)
        literal = ibis.literal(value, type=physical)
    else:
        literal = ibis.literal(text, type=physical)
    return raw >= literal


def _composite_cutoff(
    raw: ir.Value,
    prefix: ir.Value,
    point: datetime,
    *,
    inclusive: bool,
    prefix_axis: TargetDimensionContract,
    engine: str,
) -> ir.BooleanValue | None:
    physical = raw.type()
    if not (physical.is_integer() or physical.is_string()):
        return None
    if not _raw_lineage(prefix):
        return None

    def day_cutoff(point: datetime, *, inclusive: bool) -> ir.BooleanValue | None:
        if isinstance(prefix_axis.parse, StrptimeParse):
            if is_time_bearing_format(prefix_axis.parse.format):
                return None
            return _encoded_cutoff(prefix, point, prefix_axis.parse, inclusive=inclusive)
        return _scalar_cutoff(prefix, point, inclusive=inclusive, engine=engine)

    text = ordered_time_literal(point, "%Y%m%d%H", strict=not inclusive)
    if text is None:
        exhausted = ibis.ifelse(raw.isnull() | prefix.isnull(), ibis.null().cast("boolean"), False)
        assert isinstance(exhausted, ir.BooleanValue)
        return exhausted
    tick = datetime.strptime(text, "%Y%m%d%H")
    midnight = tick.replace(hour=0)
    at_day = day_cutoff(midnight, inclusive=True)
    after_day = day_cutoff(midnight, inclusive=False)
    if at_day is None or after_day is None:
        return None
    on_day = at_day & ~after_day
    hour = ibis.literal(f"{tick.hour:02d}" if physical.is_string() else tick.hour, type=physical)
    result = after_day | (on_day & (raw >= hour))
    result = ibis.ifelse(raw.isnull() | prefix.isnull(), ibis.null().cast("boolean"), result)
    assert isinstance(result, ir.BooleanValue)
    return result


def endpoint_rewrite_safe(point: datetime, zone: str, engine: str) -> bool:
    """Qualify one constant cutoff without scanning source values."""
    normalized = normalize_timezone(zone)
    if normalized.kind == "fixed_offset" or normalized.name == "UTC":
        return True
    from marivo.datasource.engines import profile_for_backend_type

    if profile_for_backend_type(engine) is None:
        return False
    # MySQL-family CONVERT_TZ can leave out-of-range clocks unconverted. Use
    # the common native range, including the endpoint qualification margin.
    if engine == "mysql" and not (1971 <= point.year <= 2037):
        return False
    try:
        offsets = {
            (point + timedelta(hours=hour)).astimezone(normalized.tz).utcoffset()
            for hour in range(-48, 49)
        }
    except (OverflowError, ValueError):
        return False
    return len(offsets) == 1


def source_time_comparison(
    value: ir.Value,
    axis: TargetDimensionContract,
    authority: SourceTimeAuthority,
    *,
    bound: date | datetime,
    comparison: TimeComparison,
    prefix: ir.Value | None = None,
    prefix_axis: TargetDimensionContract | None = None,
    engine: str = "duckdb",
) -> ir.BooleanValue | None:
    """Build exact membership over parsed values or native physical clocks.

    Local source clocks retain native predicates, while physical instants and civil dates
    keep their own domains. Constants retain the raw type and precision. A
    composite prefix is its original DATE or canonical date encoding, not its
    already parsed projection.
    """
    if authority.kind != "instant":
        instant_bound = _boundary_instant(bound, authority)
        if not endpoint_rewrite_safe(instant_bound, authority.timezone, engine):
            return None
    if not _raw_lineage(value):
        return None
    parse = axis.parse
    if isinstance(parse, StrptimeParse) and not _ordered_encoding(value, parse):
        text = value.cast("string")
        assert isinstance(text, ir.StringValue)
        value = parse_strptime(engine, text, parse)
        parse = None
    if isinstance(parse, HourPrefixParse):
        if prefix is None or prefix_axis is None:
            return None
    elif parse is not None and not isinstance(parse, (TimestampParse, DateParse, StrptimeParse)):
        return None

    def lower(strict: bool) -> ir.BooleanValue | None:
        if authority.kind == "civil_date":
            point = (
                _boundary_instant(bound, authority)
                .astimezone(normalize_timezone(authority.timezone).tz)
                .replace(tzinfo=None)
            )
            inclusive = not strict
        else:
            instant = _boundary_instant(bound, authority)
            if authority.kind == "wall_clock":
                point = instant.astimezone(normalize_timezone(authority.timezone).tz).replace(
                    tzinfo=None
                )
                inclusive = not strict
            else:
                point, inclusive = instant, not strict
        if isinstance(parse, HourPrefixParse):
            assert prefix is not None and prefix_axis is not None
            return _composite_cutoff(
                value, prefix, point, inclusive=inclusive, prefix_axis=prefix_axis, engine=engine
            )
        if isinstance(parse, StrptimeParse):
            return _encoded_cutoff(value, point, parse, inclusive=inclusive)
        return _scalar_cutoff(value, point, inclusive=inclusive, engine=engine)

    try:
        if comparison in (">=", "<"):
            result = lower(False)
            return None if result is None else result if comparison == ">=" else _complement(result)
        if comparison in (">", "<="):
            result = lower(True)
            return None if result is None else result if comparison == ">" else _complement(result)
        first, last = lower(False), lower(True)
        if first is None or last is None:
            return None
        equal = first & ~last
        return equal if comparison == "==" else ~equal
    except OverflowError:
        # A timezone conversion beyond Python's supported date domain has no
        # qualified constant-side form; retain the existing native expression.
        return None


def source_time_predicate(
    value: ir.Value,
    axis: TargetDimensionContract,
    authority: SourceTimeAuthority,
    *,
    start: date | datetime | None,
    end: date | datetime | None,
    prefix: ir.Value | None = None,
    prefix_axis: TargetDimensionContract | None = None,
    engine: str = "duckdb",
    use: TimePredicateUse = "value",
) -> ir.BooleanValue | None:
    """Build membership preserving truth values, or selected rows for a positive filter."""
    if use == "filter":
        filtered = _same_day_hour_filter(
            value,
            axis,
            authority,
            start=start,
            end=end,
            prefix=prefix,
            prefix_axis=prefix_axis,
            engine=engine,
        )
        if filtered is not None:
            return filtered
    if (
        start is None
        and end is None
        and isinstance(axis.parse, StrptimeParse)
        and (not _ordered_encoding(value, axis.parse) or not _raw_lineage(value))
    ):
        text = value.cast("string")
        assert isinstance(text, ir.StringValue)
        return _nullable_constant(parse_strptime(engine, text, axis.parse), True)
    if start is None and end is None:
        if (
            not _raw_lineage(value)
            or (prefix is not None and not _raw_lineage(prefix))
            or (
                isinstance(axis.parse, StrptimeParse)
                and axis.parse.format not in ORDERED_TIME_FORMATS
            )
        ):
            return None
        result = _nullable_constant(value, True)
        return result if prefix is None else result & _nullable_constant(prefix, True)
    first = (
        source_time_comparison(
            value,
            axis,
            authority,
            bound=start,
            comparison=">=",
            prefix=prefix,
            prefix_axis=prefix_axis,
            engine=engine,
        )
        if start is not None
        else None
    )
    last = (
        source_time_comparison(
            value,
            axis,
            authority,
            bound=end,
            comparison="<",
            prefix=prefix,
            prefix_axis=prefix_axis,
            engine=engine,
        )
        if end is not None
        else None
    )
    if (start is not None and first is None) or (end is not None and last is None):
        declaration = (
            None
            if authority.kind == "instant"
            else DeclaredSourceTimezone(
                authority.timezone,
                authority.origin,
                authority.inherited_from if authority.kind == "wall_clock" else None,
            )
        )
        parsed_prefix = prefix
        if (
            prefix is not None
            and prefix_axis is not None
            and isinstance(prefix_axis.parse, StrptimeParse)
        ):
            text = prefix.cast("string")
            assert isinstance(text, ir.StringValue)
            parsed_prefix = parse_strptime(engine, text, prefix_axis.parse)
        native, _ = source_time(
            value,
            axis,
            boundary_timezone="UTC",
            source_timezone=declaration,
            engine=engine,
            prefix=parsed_prefix,
            frozen=authority,
        )
        if authority.kind == "civil_date":
            native = localize(authority.timezone, timestamp(native))
        elif native.type().timezone is None:
            native = localize("UTC", timestamp(native))
        if start is not None and first is None:
            first = native >= ibis.literal(_boundary_instant(start, authority)).cast(native.type())
        if end is not None and last is None:
            last = native < ibis.literal(_boundary_instant(end, authority)).cast(native.type())
    return last if first is None else first if last is None else first & last


def _same_day_hour_filter(
    raw: ir.Value,
    axis: TargetDimensionContract,
    authority: SourceTimeAuthority,
    *,
    start: date | datetime | None,
    end: date | datetime | None,
    prefix: ir.Value | None,
    prefix_axis: TargetDimensionContract | None,
    engine: str,
) -> ir.BooleanValue | None:
    """Expose one source day's hour range without changing which rows pass WHERE.

    This is not a nullable Boolean replacement: a missing prefix with an hour
    outside the range can produce False instead of NULL. Declared hours are
    canonical 0..23 values, so midnight belongs only to its own source date.
    """
    if (
        not isinstance(axis.parse, HourPrefixParse)
        or authority.kind != "wall_clock"
        or start is None
        or end is None
        or prefix is None
        or prefix_axis is None
        or not _raw_lineage(raw)
        or not _raw_lineage(prefix)
        or not (raw.type().is_string() or raw.type().is_integer())
    ):
        return None
    ticks: list[datetime] = []
    for bound in (start, end):
        instant = _boundary_instant(bound, authority)
        if not endpoint_rewrite_safe(instant, authority.timezone, engine):
            return None
        point = instant.astimezone(normalize_timezone(authority.timezone).tz).replace(tzinfo=None)
        text = ordered_time_literal(point, "%Y%m%d%H")
        if text is None:
            return None
        ticks.append(datetime.strptime(text, "%Y%m%d%H"))
    first, last = ticks
    if first.date() != last.date():
        return None
    midnight = first.replace(hour=0)
    if isinstance(prefix_axis.parse, StrptimeParse):
        if is_time_bearing_format(prefix_axis.parse.format):
            return None
        day = _encoded_cutoff(prefix, midnight, prefix_axis.parse, inclusive=True)
    else:
        if not prefix.type().is_date():
            return None
        day = _scalar_cutoff(prefix, midnight, inclusive=True, engine=engine)
    # A representable, ordered date literal is necessary for an exact equality.
    if day is None or not isinstance(day.op(), ops.GreaterEqual):
        return None
    if first >= last:
        return ibis.literal(False)
    day_node = day.op()
    assert isinstance(day_node, ops.GreaterEqual)
    same_day = ops.Equals(day_node.left, day_node.right).to_expr()
    if engine == "mysql" and prefix.type().is_string():
        # Ibis casts string equality operands to BINARY on MySQL. Ordered
        # date bounds retain the original partition column instead.
        assert isinstance(prefix_axis.parse, StrptimeParse)
        next_day = _encoded_cutoff(prefix, midnight, prefix_axis.parse, inclusive=False)
        if next_day is None or not isinstance(next_day.op(), ops.GreaterEqual):
            return None
        same_day = day & _complement(next_day)
    lower = ibis.literal(
        f"{first.hour:02d}" if raw.type().is_string() else first.hour, type=raw.type()
    )
    upper = ibis.literal(
        f"{last.hour:02d}" if raw.type().is_string() else last.hour, type=raw.type()
    )
    result = same_day & (raw >= lower) & (raw < upper)
    assert isinstance(result, ir.BooleanValue)
    return result


def parse_strptime(engine: str, text: ir.StringValue, parse: StrptimeParse) -> ir.Value:
    """Apply a semantic parser using the shared native expression implementation."""
    from marivo.datasource._temporal_expressions import (
        TemporalExpressionError,
    )
    from marivo.datasource._temporal_expressions import (
        parse_strptime as native_strptime,
    )

    try:
        return native_strptime(
            engine, text, parse.format, time_bearing=is_time_bearing_format(parse.format)
        )
    except TemporalExpressionError as error:
        raise compilation_error(
            error.expected,
            error.received,
            repair=error.repair or "Use a supported native temporal format.",
        ) from error


def entity_engine(registry: Registry, entity: TargetEntityContract) -> str:
    """Resolve the engine backend_type of one Entity's declared datasource.

    ``placement.source_binding`` uses the same authority: the datasource that
    owns the Entity decides which parser and format its source speaks.
    """
    domain = entity.datasource_ref.path
    datasource = registry.datasources.get(domain)
    if datasource is None:
        raise compilation_error(
            f"a registered datasource {domain[:120]!r} for the source Entity",
            "missing source datasource",
        )
    return datasource.backend_type


def _retain_authority(
    authority: SourceTimeAuthority, frozen: SourceTimeAuthority | None
) -> SourceTimeAuthority:
    if frozen is None:
        return authority
    if authority.model_copy(update={"physical_type": frozen.physical_type}) != frozen:
        raise compilation_error("the exact frozen source time authority", "time authority drift")
    return frozen


def _hour_clock(value: ir.Value, prefix: ir.DateValue, *, engine: str) -> ir.TimestampValue:
    """Compose a neutral source wall clock before applying its authority."""
    hour = value.cast("int64")
    if not isinstance(hour, ir.IntegerValue):
        raise compilation_error("an integer hour", "invalid hour representation")
    prefix_clock = timestamp(prefix)
    if engine == "clickhouse":
        # DATE-to-timestamp otherwise adopts the session timezone before the
        # composite clock can use its inherited source authority.
        prefix_clock = timestamp(
            prefix.cast(dt.Timestamp(timezone="UTC", scale=6)).cast(dt.Timestamp(scale=6))
        )
    result = prefix_clock + hour.as_interval("h")
    assert isinstance(result, ir.TimestampValue)
    return result


def source_time(
    value: ir.Value,
    axis: TargetDimensionContract,
    *,
    boundary_timezone: str,
    source_timezone: DeclaredSourceTimezone | None,
    engine: str,
    prefix: ir.Value | None = None,
    frozen: SourceTimeAuthority | None = None,
) -> tuple[ir.Value, SourceTimeAuthority]:
    """Interpret one field from captured declarations, using native source operations."""
    physical = value.type()
    parse = axis.parse
    if frozen is not None:
        if frozen.axis != axis.ref.path:
            raise compilation_error("the frozen event axis", "time authority drift")
        source_timezone = (
            None
            if frozen.kind == "instant"
            else DeclaredSourceTimezone(
                frozen.timezone,
                frozen.origin,
                frozen.inherited_from if frozen.kind == "wall_clock" else None,
            )
        )
    issue = time_type_issue(
        dt.dtype(frozen.physical_type) if frozen is not None else physical,
        path=axis.ref.path,
        parse=parse,
        engine=engine,
    )
    if issue is not None:
        raise DatasetCompilationError(
            expected=issue.expected,
            received=issue.received,
            repair=issue.action,
            location=f"analysis.source_time[{axis.ref.kind}:{axis.ref.path}]",
        )
    physical_is_aware = (
        frozen.kind == "instant"
        if frozen is not None
        else isinstance(physical, dt.Timestamp)
        and (physical.timezone is not None or engine == "clickhouse")
    ) and not isinstance(parse, (StrptimeParse, HourPrefixParse))
    if isinstance(parse, HourPrefixParse):
        if prefix is None:
            raise compilation_error("a bound civil-date hour prefix", "missing date prefix")
        if not isinstance(prefix, ir.DateValue):
            raise compilation_error("a civil-date hour prefix", "timestamp hour prefix")
        value = _hour_clock(value, prefix, engine=engine)
    elif isinstance(parse, StrptimeParse):
        text = value.cast("string")
        assert isinstance(text, ir.StringValue)
        value = parse_strptime(engine, text, parse)
    elif isinstance(parse, DateParse):
        if not isinstance(value, ir.DateValue):
            raise compilation_error("a native civil date", "unsupported source representation")
    elif parse is not None and not isinstance(parse, TimestampParse):
        raise compilation_error("a supported time parser", "unresolved parser")
    if physical_is_aware:
        if engine == "clickhouse" and value.type().timezone is None:
            value = value.cast(dt.Timestamp(timezone="UTC", scale=value.type().scale))
        authority: SourceTimeAuthority = InstantSourceTime(
            axis=axis.ref.path, physical_type=str(physical)
        )
        instant = timestamp(value)
    else:
        if source_timezone is None:
            from marivo.semantic.errors import SourceTimezoneMissing

            raise SourceTimezoneMissing(axis=axis, physical_type=str(physical), backend=engine)
        if isinstance(value, ir.DateValue):
            authority = CivilDateSourceTime(
                axis=axis.ref.path,
                physical_type=str(physical),
                timezone=source_timezone.timezone,
                origin=source_timezone.origin,
            )
            return value, _retain_authority(authority, frozen)
        if not isinstance(value, ir.TimestampValue):
            raise compilation_error("a date or timestamp parser", "non-temporal source")
        authority = WallClockSourceTime(
            axis=axis.ref.path,
            physical_type=str(physical),
            timezone=source_timezone.timezone,
            origin=source_timezone.origin,
            inherited_from=source_timezone.inherited_from,
        )
        if value.type().timezone is not None:
            # A zoned parser carrier is neutral, never physical instant authority.
            value = value.cast(dt.Timestamp(scale=value.type().scale))
        if (
            same_timezone(source_timezone.timezone, boundary_timezone)
            and normalize_timezone(source_timezone.timezone).kind == "fixed_offset"
        ):
            return value, _retain_authority(authority, frozen)
        if value.type().scale is not None and value.type().scale > 6:
            raise compilation_error(
                "exact native timezone precision", "submicrosecond conversion unsupported"
            )
        instant = localize(source_timezone.timezone, timestamp(value))
    return instant, _retain_authority(authority, frozen)
