"""Pure engine-agnostic expressions landing governed time in one explicit civil boundary zone."""

import re
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import DatasetCompilationError, compilation_error
from marivo.analysis.core.time_authority import SourceTimeAuthority, time_zone
from marivo.semantic._physical_contracts import time_type_issue
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

# ClickHouse ``parseDateTime`` returns second-precision ``DateTime``, so a
# format carrying a sub-second directive would silently drop the fraction.
_CLICKHOUSE_TRUNCATING_DIRECTIVES: frozenset[str] = frozenset({"%f"})

# The Python directives ClickHouse ``parseDateTime`` cannot express exactly, so
# a format carrying one is refused during compilation rather than left to
# execution. All of them parse *successfully* at the driver boundary, which is
# why they cannot be left to fail on their own:
#
# - ``%U`` is answered with an opaque driver error (``Code: 48 .. format is not
#   supported``) instead of NULL, so nothing structured reaches the caller.
# - ``%a``, ``%A`` and ``%w`` are accepted and silently *shift* the result. Only
#   a leading weekday token is ignored, which coincides with Python; anywhere
#   else the parser moves the instant by whole weeks.
#   ``parseDateTimeOrNull('2026-07-01 Wed', '%Y-%m-%d %a', 'UTC')`` answers
#   2025-12-31 -- a wrong non-NULL instant, no error -- and Python ``%A``
#   reaches the same trap through the MySQL ``%W`` this emitter produces.
#
# The remaining refusal classes need no entry here. ``%f`` is caught by
# :data:`_CLICKHOUSE_TRUNCATING_DIRECTIVES` above, and the week-number and
# timezone letters -- ``%V``, ``%u``, ``%G``, ``%g``, ``%c``, ``%z``, plus
# Python ``%W`` itself -- never get this far, because the MySQL-family
# translator refuses them before this gate runs. ``%b``/``%B`` and the plain
# calendar and clock directives were measured correct and stay admitted.
#
# A format missing a directive is *not* refused: ClickHouse fills the gap from
# its own epoch (``%m`` alone answers year 2000, ``%Y`` alone month 1), exactly
# as DuckDB fills 1900 and MySQL fills 0000. Only ClickHouse's fill is
# documented here because its fill behavior differs from other engines.
_CLICKHOUSE_INEXACT_DIRECTIVES: frozenset[str] = frozenset({"%U", "%a", "%A", "%w"})

# A real ``%<letter>`` directive, as opposed to an escaped literal percent.
_STRPTIME_DIRECTIVE = re.compile(r"%[A-Za-z]")

# Only complete, fixed-width, most-significant-first calendar encodings are
# order preserving. The format declaration is the source contract, not a
# runtime certification of source rows or partition metadata.
_ORDERED_FORMATS: dict[str, int] = {
    "%Y%m%d": 86400,
    "%Y-%m-%d": 86400,
    "%Y%m%d%H": 3600,
    "%Y-%m-%d %H": 3600,
    "%Y%m%d%H%M": 60,
    "%Y-%m-%d %H:%M": 60,
    "%Y%m%d%H%M%S": 1,
    "%Y-%m-%d %H:%M:%S": 1,
}


def ordered_time_literal(point: datetime, fmt: str, *, strict: bool = False) -> str | None:
    """Encode the first declared clock tick at or after a normalized boundary."""
    step = _ORDERED_FORMATS[fmt]
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


def _nullable_constant(value: ir.Value, truth: bool) -> ir.BooleanValue:
    result = ibis.ifelse(value.isnull(), ibis.null().cast("boolean"), truth)
    assert isinstance(result, ir.BooleanValue)
    return result


def _complement(value: ir.BooleanValue) -> ir.BooleanValue:
    import ibis.expr.operations as ops

    node = value.op()
    if isinstance(node, (ops.GreaterEqual, ops.Greater)):
        operation = ops.Less if isinstance(node, ops.GreaterEqual) else ops.LessEqual
        result = operation(node.left, node.right).to_expr()
        assert isinstance(result, ir.BooleanValue)
        return result
    return ~value


def _raw_lineage(value: ir.Value) -> bool:
    """Admit only physical fields and transparent field projections."""
    import ibis.expr.operations as ops

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
        point = point.replace(tzinfo=time_zone(authority.boundary_timezone))
    return point.astimezone(timezone.utc)


def _scalar_cutoff(
    raw: ir.Value, parse: StrptimeParse | None, point: datetime, *, inclusive: bool
) -> ir.BooleanValue | None:
    """Compare a physical clock to one translated lower cutoff."""
    physical = raw.type()
    if parse is not None:
        if parse.format not in _ORDERED_FORMATS or not (
            physical.is_string()
            or (physical.is_integer() and parse.format.replace("%", "").isalpha())
        ):
            return None
        text = ordered_time_literal(point, parse.format, strict=not inclusive)
        if text is None:
            return _nullable_constant(raw, False)
        if physical.is_integer():
            number = int(text)
            assert isinstance(physical, dt.Integer)
            minimum, maximum = physical.bounds
            if number <= minimum:
                return _nullable_constant(raw, True)
            if number > maximum:
                return _nullable_constant(raw, False)
            literal = ibis.literal(number, type=physical)
        else:
            literal = ibis.literal(text, type=physical)
        return raw >= literal
    if physical.is_date():
        midnight = point.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            day = (midnight + timedelta(days=not inclusive or point != midnight)).date()
        except OverflowError:
            return _nullable_constant(raw, False)
        return raw >= ibis.literal(day, type=physical)
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


def _composite_cutoff(
    raw: ir.Value,
    prefix: ir.Value,
    prefix_axis: TargetDimensionContract,
    point: datetime,
    *,
    inclusive: bool,
) -> ir.BooleanValue | None:
    physical = raw.type()
    if not (physical.is_integer() or physical.is_string()):
        return None
    fmt = prefix_axis.parse
    if isinstance(fmt, StrptimeParse):
        if is_time_bearing_format(fmt.format):
            return None
    elif not prefix.type().is_date():
        return None
    text = ordered_time_literal(point, "%Y%m%d%H", strict=not inclusive)
    if text is None:
        exhausted = ibis.ifelse(raw.isnull() | prefix.isnull(), ibis.null().cast("boolean"), False)
        assert isinstance(exhausted, ir.BooleanValue)
        return exhausted
    tick = datetime.strptime(text, "%Y%m%d%H")
    midnight = tick.replace(hour=0)
    date_parse = fmt if isinstance(fmt, StrptimeParse) else None
    at_day = _scalar_cutoff(prefix, date_parse, midnight, inclusive=True)
    after_day = _scalar_cutoff(prefix, date_parse, midnight, inclusive=False)
    if at_day is None or after_day is None:
        return None
    on_day = at_day & ~after_day
    hour = ibis.literal(f"{tick.hour:02d}" if physical.is_string() else tick.hour, type=physical)
    result = after_day | (on_day & (raw >= hour))
    if tick.hour == 0 and tick.date() > date.min:
        previous = midnight - timedelta(days=1)
        at_previous = _scalar_cutoff(prefix, date_parse, previous, inclusive=True)
        assert at_previous is not None
        last = ibis.literal("24" if physical.is_string() else 24, type=physical)
        result |= (at_previous & ~at_day) & (raw == last)
    result = ibis.ifelse(raw.isnull() | prefix.isnull(), ibis.null().cast("boolean"), result)
    assert isinstance(result, ir.BooleanValue)
    return result


def source_time_comparison(
    value: ir.Value,
    axis: TargetDimensionContract,
    authority: SourceTimeAuthority,
    *,
    bound: date | datetime,
    comparison: TimeComparison,
    prefix: ir.Value | None = None,
    prefix_axis: TargetDimensionContract | None = None,
) -> ir.BooleanValue | None:
    """Build an exact raw predicate, or leave a legal native expression alone.

    Local source clocks use wall order, while physical instants and civil dates
    keep their own domains. Constants retain the raw type and precision. A
    composite prefix is its original DATE or canonical date encoding, not its
    already parsed projection.
    """
    if not _raw_lineage(value) or (prefix is not None and not _raw_lineage(prefix)):
        return None
    parse = axis.parse
    if isinstance(parse, HourPrefixParse):
        if prefix is None or prefix_axis is None:
            return None
    elif parse is not None and not isinstance(parse, (TimestampParse, DateParse, StrptimeParse)):
        return None
    if isinstance(parse, StrptimeParse) and parse.format not in _ORDERED_FORMATS:
        return None

    def lower(strict: bool) -> ir.BooleanValue | None:
        from marivo.analysis.compiler._wall_order import wall_order_bound

        if authority.kind == "civil_date":
            point = bound if isinstance(bound, datetime) else datetime.combine(bound, time())
            point = point.replace(tzinfo=None)
            inclusive = not strict
        else:
            instant = _boundary_instant(bound, authority)
            if authority.kind == "localizable":
                assert authority.read_timezone is not None
                point, inclusive = wall_order_bound(
                    instant, time_zone(authority.read_timezone), strict=strict
                )
            else:
                point, inclusive = instant, not strict
        if isinstance(parse, HourPrefixParse):
            assert prefix is not None and prefix_axis is not None
            return _composite_cutoff(value, prefix, prefix_axis, point, inclusive=inclusive)
        return _scalar_cutoff(
            value, parse if isinstance(parse, StrptimeParse) else None, point, inclusive=inclusive
        )

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
) -> ir.BooleanValue | None:
    """Build a half-open raw range with the same nullable comparison semantics."""
    kwargs = {"prefix": prefix, "prefix_axis": prefix_axis}
    if start is None and end is None:
        if (
            not _raw_lineage(value)
            or (prefix is not None and not _raw_lineage(prefix))
            or (isinstance(axis.parse, StrptimeParse) and axis.parse.format not in _ORDERED_FORMATS)
        ):
            return None
        result = _nullable_constant(value, True)
        return result if prefix is None else result & _nullable_constant(prefix, True)
    first = (
        source_time_comparison(value, axis, authority, bound=start, comparison=">=", **kwargs)
        if start is not None
        else None
    )
    last = (
        source_time_comparison(value, axis, authority, bound=end, comparison="<", **kwargs)
        if end is not None
        else None
    )
    if (start is not None and first is None) or (end is not None and last is None):
        return None
    return last if first is None else first if last is None else first & last


def encoded_time_predicate(
    value: ir.Value,
    axis: TargetDimensionContract,
    authority: SourceTimeAuthority,
    *,
    start: date | datetime | None,
    end: date | datetime | None,
) -> ir.BooleanValue | None:
    """Build the qualified canonical-encoding half-open predicate without IO."""
    if not isinstance(axis.parse, StrptimeParse):
        return None
    return source_time_predicate(value, axis, authority, start=start, end=end)


def _parse_signature_zoned(text: str, fmt: str, zone: str) -> datetime:
    raise NotImplementedError("native zoned string parse")


def _parse_signature_clock(text: str, fmt: str) -> datetime:
    raise NotImplementedError("native canonical DuckDB time parse")


# Preserve the declaration's canonical-encoding authority in the expression.
# An arbitrary authored Ibis StringToTimestamp does not carry that promise.
_duckdb_strptime: Callable[[ir.StringValue, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _parse_signature_clock,
    name="strptime",
    signature=((dt.string, dt.string), dt.Timestamp(timezone="UTC", scale=6)),
)


# ClickHouse has no ibis ``StringToTimestamp`` rule. ``parseDateTimeOrNull``
# is its native MySQL-format parser; the explicit zone argument keeps the result
# a UTC-labelled instant regardless of session timezone. Malformed values may
# become NULL and are not checked by an automatic source preflight.
_clickhouse_strptime: Callable[[ir.StringValue, str, str], ir.TimestampValue] = (
    ibis.udf.scalar.builtin(
        _parse_signature_zoned,
        name="parseDateTimeOrNull",
        signature=((dt.string, dt.string, dt.string), dt.Timestamp(timezone="UTC", scale=6)),
    )
)

# The neutral parse operations the presence guard must admit. They stay out of
# ``governed_temporal_operation``, whose rewrite expects the localize/render
# two-argument shape rather than the parse shape.


def translated_strptime_format(engine: str, fmt: str) -> str:
    """Translate one authored Python format into *engine*'s native parser format.

    ``EngineProfile.translate_strptime_format`` owns "who translates the
    format"; this function is the compiler's only route to it, so no backend can
    silently bypass translation. A format the engine cannot express exactly is a
    structured compilation failure rather than a server-default parse.
    """
    from marivo.datasource.engines import profile_for_backend_name

    profile = profile_for_backend_name(engine)
    if engine == "clickhouse":
        # ``%%`` is an escaped literal percent, not a directive, so the scan
        # removes those pairs before looking for any directive. ``%%f`` parses
        # a literal ``%`` followed by ``f`` and must stay admitted.
        directives = set(_STRPTIME_DIRECTIVE.findall(fmt.replace("%%", "")))
        truncating = _CLICKHOUSE_TRUNCATING_DIRECTIVES & directives
        if truncating:
            raise compilation_error(
                "a strptime format that ClickHouse parseDateTime can express exactly",
                f"ClickHouse would truncate the sub-second directive in format {fmt[:200]!r}",
            )
        inexact = _CLICKHOUSE_INEXACT_DIRECTIVES & directives
        if inexact:
            raise compilation_error(
                "a strptime format that ClickHouse parseDateTime can express exactly",
                f"ClickHouse cannot express directive {sorted(inexact)[0]} exactly "
                f"in format {fmt[:200]!r}: it either fails or shifts the parsed value",
            )
    try:
        return profile.translate_strptime_format(fmt)
    except ValueError as cause:
        raise compilation_error(
            f"a strptime format expressible on the {profile.name} backend",
            f"untranslatable strptime format for the {profile.name} backend",
        ) from cause


def _needs_naive_binding(fmt: str) -> bool:
    return "%z" not in fmt and "%Z" not in fmt


def native_parse(engine: str, text: ir.StringValue, parse: StrptimeParse) -> ir.Value:
    """Call *engine*'s native parser without the date-only civil cast.

    Keeping this step separate lets the malformed-value assertion read the
    parser result directly instead of the cast value.
    """
    fmt = translated_strptime_format(engine, parse.format)
    if engine == "clickhouse":
        # The explicit zone argument keeps the parsed instant labelled UTC
        # whatever the session timezone is.
        return _clickhouse_strptime(text, fmt, "UTC")
    parsed: ir.Value = _duckdb_strptime(text, fmt) if engine == "duckdb" else text.as_timestamp(fmt)
    if _needs_naive_binding(parse.format):
        # Ibis labels StringToTimestamp UTC although a parser without an
        # offset returns a naive timestamp. Bind its actual value domain at
        # microsecond scale: a bare TIMESTAMP becomes MySQL's DATETIME,
        # which silently drops the fraction the format may have parsed.
        parsed = parsed.cast(dt.Timestamp(scale=6))
    return parsed


def parse_strptime(engine: str, text: ir.StringValue, parse: StrptimeParse) -> ir.Value:
    """Parse string text with *engine*'s native parser and the authored format.

    Every engine goes through :func:`translated_strptime_format`, so the profile
    hook is the one owner of who translates a format. DuckDB consumes
    the Python strptime format itself, so their translation is the identity and
    the emitted format is the authored one.

    Date-only formats stay date, exactly as before.
    """
    parsed = native_parse(engine, text, parse)
    if not is_time_bearing_format(parse.format):
        parsed = parsed.cast("date")
    return parsed


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


def _timezone_signature(zone: str, value: datetime) -> datetime:
    raise NotImplementedError("native DuckDB timezone signature")


_localize_native: Callable[[str, ir.TimestampValue], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _timezone_signature,
    name="timezone",
    signature=((dt.string, dt.timestamp), dt.Timestamp(timezone="UTC", scale=6)),
)
_render_native: Callable[[str, ir.TimestampValue], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _timezone_signature,
    name="timezone",
    signature=((dt.string, dt.Timestamp(timezone="UTC")), dt.Timestamp(scale=6)),
)


def timestamp(value: ir.Value) -> ir.TimestampValue:
    result = value if isinstance(value, ir.TimestampValue) else value.cast("timestamp")
    if not isinstance(result, ir.TimestampValue):
        raise compilation_error("a temporal value", "invalid timestamp expression")
    return result


def localize(zone: str, value: ir.TimestampValue) -> ir.TimestampValue:
    resolved = time_zone(zone)
    if isinstance(resolved, timezone):
        offset = resolved.utcoffset(None)
        if offset is None:
            raise compilation_error("an exact fixed offset", "missing timezone offset")
        return _localize_native("UTC", value - ibis.interval(seconds=int(offset.total_seconds())))
    return _localize_native(zone, value)


def render(zone: str, value: ir.TimestampValue) -> ir.TimestampValue:
    resolved = time_zone(zone)
    if isinstance(resolved, timezone):
        offset = resolved.utcoffset(None)
        if offset is None:
            raise compilation_error("an exact fixed offset", "missing timezone offset")
        return _render_native("UTC", value) + ibis.interval(seconds=int(offset.total_seconds()))
    return _render_native(zone, value)


def _retain_authority(
    authority: SourceTimeAuthority, frozen: SourceTimeAuthority | None
) -> SourceTimeAuthority:
    if frozen is None:
        return authority
    # SourceBinding verifies the raw schema before carrier preparation.
    # Prepared consumers may adopt a different native carrier precision;
    # retain the raw physical fact rather than reconstructing its authority.
    if authority.model_copy(update={"physical_type": frozen.physical_type}) != frozen:
        raise compilation_error("the exact frozen source time authority", "time authority drift")
    return frozen


def _frozen_time_origin(
    authority: SourceTimeAuthority,
) -> Literal["physical", "declared", "engine", "system_fallback"]:
    if authority.source == "physical":
        return "physical"
    if authority.source == "declared":
        return "declared"
    if authority.source == "engine":
        return "engine"
    if authority.source == "system_fallback":
        return "system_fallback"
    raise compilation_error("a frozen timestamp source authority", "civil date authority changed")


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
    read_timezone: str | None,
    engine: str,
    read_source: Literal["engine", "system_fallback"] = "engine",
    prefix: ir.Value | None = None,
    prefix_timezone: str | None = None,
    frozen: SourceTimeAuthority | None = None,
) -> tuple[ir.Value, SourceTimeAuthority]:
    """Parse and localize a column without executing it or consulting ambient state."""
    if frozen is not None:
        if frozen.axis != axis.ref.path or frozen.boundary_timezone != boundary_timezone:
            raise compilation_error("the frozen event axis and boundary", "time authority drift")
        read_timezone = frozen.read_timezone
        if frozen.source in ("engine", "system_fallback"):
            read_source = frozen.source
    physical = value.type()
    parse = axis.parse
    declared = parse.timezone if isinstance(parse, (TimestampParse, StrptimeParse)) else None
    physical_is_aware = (
        frozen.source == "physical"
        if frozen is not None
        else isinstance(physical, dt.Timestamp)
        and (physical.timezone is not None or engine == "clickhouse")
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
    if isinstance(parse, HourPrefixParse):
        if prefix is None:
            raise compilation_error("a bound civil-date hour prefix", "missing prefix axis")
        if not isinstance(prefix, ir.DateValue):
            raise compilation_error("a civil-date hour prefix", "timestamp hour prefix")
        if frozen is None and prefix_timezone is not None:
            read_timezone = prefix_timezone
        value = _hour_clock(value, prefix, engine=engine)
    elif isinstance(parse, StrptimeParse):
        text = value.cast("string")
        if not isinstance(text, ir.StringValue):
            raise compilation_error("a string parser input", "invalid parser expression")
        value = parse_strptime(engine, text, parse)
    elif isinstance(parse, DateParse):
        if not isinstance(value, ir.DateValue):
            raise compilation_error(
                "a native civil date", "unsupported temporal source representation"
            )
    elif parse is not None and not isinstance(parse, (TimestampParse)):
        raise compilation_error("a bound supported time parser", "unresolved composite parser")
    if isinstance(value, ir.DateValue):
        if isinstance(parse, TimestampParse) or declared is not None:
            raise compilation_error(
                "a civil-date parser without timezone", "unsupported temporal source representation"
            )
        return (
            value,
            _retain_authority(
                SourceTimeAuthority(
                    axis=axis.ref.path,
                    physical_type=str(physical),
                    kind="civil_date",
                    read_timezone=None,
                    source="civil_date",
                    boundary_timezone=boundary_timezone,
                ),
                frozen,
            ),
        )
    if not isinstance(value, ir.TimestampValue):
        raise compilation_error("a declared date or timestamp parser", "non-temporal source")
    if physical_is_aware and engine == "clickhouse" and value.type().timezone is None:
        value = timestamp(value.cast(dt.Timestamp(timezone="UTC", scale=value.type().scale)))
    kind = value.type()
    if frozen is not None and frozen.source == "civil_date":
        raise compilation_error(
            "a frozen timestamp source authority", "civil date authority changed"
        )
    origin: Literal["physical", "declared", "engine", "system_fallback"]
    if kind.timezone is not None:
        if kind.scale is not None and kind.scale > 6:
            raise compilation_error(
                "exact native timezone conversion precision",
                "submicrosecond conversion unsupported",
            )
        if frozen is not None:
            adopted = frozen.read_timezone
            if adopted is None:
                raise compilation_error(
                    "a frozen timestamp read timezone", "missing read authority"
                )
            origin = _frozen_time_origin(frozen)
        elif isinstance(parse, StrptimeParse):
            adopted = declared or read_timezone
            origin = "declared" if declared is not None else read_source
            if adopted is None:
                raise compilation_error(
                    "execution-resolved source read timezone", "missing wall-clock authority"
                )
        elif declared is not None:
            adopted, origin = declared, "declared"
        else:
            adopted, origin = kind.timezone, "physical"
        instant = value
        if engine == "clickhouse" and isinstance(parse, StrptimeParse):
            # ClickHouse parses in explicit UTC to avoid session authority.
            # That UTC label is a neutral parser carrier, not source meaning.
            instant = localize(adopted, timestamp(value.cast(dt.Timestamp(scale=6))))
        if engine == "mysql" and adopted == "UTC" and boundary_timezone == "UTC":
            # The UTC-zoned value already represents UTC. Rendering UTC is
            # identity; these engines have no DuckDB/PostgreSQL timezone().
            value = timestamp(value.cast(dt.Timestamp(scale=6)))
            instant = None
        elif engine in ("clickhouse", "trino") and adopted == "UTC" and boundary_timezone == "UTC":
            # Preserve the UTC-zoned value so Ibis labels boundary literals
            # UTC. A naive cast would adopt a session zone or unsupported function.
            instant = None
    else:
        if frozen is not None:
            adopted = frozen.read_timezone
            if adopted is None:
                raise compilation_error(
                    "a frozen timestamp read timezone", "missing read authority"
                )
            origin = _frozen_time_origin(frozen)
        else:
            adopted = declared or read_timezone
            origin = "declared" if declared is not None else read_source
        if adopted is None:
            raise compilation_error(
                "execution-resolved source read timezone", "missing wall-clock authority"
            )
        if adopted == boundary_timezone and time_zone(adopted).utcoffset(None) is not None:
            instant = None
        else:
            if kind.scale is not None and kind.scale > 6:
                raise compilation_error(
                    "exact native timezone conversion precision",
                    "submicrosecond conversion unsupported",
                    repair="Expose an aware UTC timestamp retaining the original precision in the source or Entity output before using this TimeDimension. Keep the naive primary_key unchanged if wall-clock identity is intended; do not truncate its ticks to make timezone conversion pass.",
                )
            instant = localize(adopted, value)
    if origin not in ("physical", "declared", "engine", "system_fallback"):
        raise compilation_error("a registered read timezone source", "invalid time authority")
    authority = SourceTimeAuthority(
        axis=axis.ref.path,
        physical_type=str(physical),
        kind="instant" if origin == "physical" else "localizable",
        read_timezone=adopted,
        source=origin,
        boundary_timezone=boundary_timezone,
    )
    return (
        value if instant is None else render(boundary_timezone, instant),
        _retain_authority(authority, frozen),
    )
