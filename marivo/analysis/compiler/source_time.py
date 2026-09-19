"""Pure engine-agnostic expressions landing governed time in one explicit civil boundary zone."""

import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo.analysis.compiler.errors import compilation_error
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.observation.temporal import SourceTimeAuthority, time_zone
from marivo.semantic.ir import (
    DateParse,
    DatetimeParse,
    HourPrefixParse,
    StrptimeParse,
    TargetDimensionContract,
    TargetEntityContract,
    TimestampParse,
    is_time_bearing_format,
)
from marivo.semantic.validator import Registry

# Backends whose string parser answers a malformed value with SQL NULL instead
# of an error. Marivo asserts the parse explicitly for exactly these engines so
# a malformed cell fails before publication.
#
# The remaining two engines are not equivalent to each other:
# - DuckDB raises on the same input, which the execution adapter already
#   converts to a structured failure, so no separate assertion is needed.
# - PostgreSQL ``TO_TIMESTAMP`` is *lenient*: a cell it cannot read in full can
#   become a wrong non-NULL instant instead of an error. ``to_timestamp(
#   '2026-07-01 15:59:00', 'YYYYMMDD')`` is 2026-01-07, a short cell is filled
#   with midnight, and trailing text is ignored. No post-parse NULL check can
#   detect that, so this is a known, documented limitation rather than a
#   guarded case; Marivo deliberately does not round-trip the source to compare
#   parsed values.
NULL_ON_MALFORMED_ENGINES: frozenset[str] = frozenset({"sqlite", "mysql", "clickhouse"})

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
# documented here because only this backend is used through a parser whose NULL
# result the compiler asserts; the assertion still holds, since a missing
# directive is a format property rather than a malformed cell.
_CLICKHOUSE_INEXACT_DIRECTIVES: frozenset[str] = frozenset({"%U", "%a", "%A", "%w"})

# A real ``%<letter>`` directive, as opposed to an escaped literal percent.
_STRPTIME_DIRECTIVE = re.compile(r"%[A-Za-z]")


def _parse_signature(text: str, fmt: str) -> datetime:
    raise NotImplementedError("native string parse")


def _parse_signature_zoned(text: str, fmt: str, zone: str) -> datetime:
    raise NotImplementedError("native zoned string parse")


# SQLite has no ibis ``StringToTimestamp`` rule. Its parser is a
# connection-local deterministic scalar registered by the SQLite execution
# adapter, and it consumes the authored Python format because
# ``datetime.strptime`` is exactly that language.
_sqlite_strptime: Callable[[ir.StringValue, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _parse_signature,
    name="_marivo_strptime",
    signature=((dt.string, dt.string), dt.Timestamp(scale=6)),
)
# ClickHouse has no ibis ``StringToTimestamp`` rule either. ``parseDateTimeOrNull``
# is its native MySQL-format parser; the explicit zone argument keeps the result
# a UTC-labelled instant regardless of session timezone, and the ``OrNull`` form
# lets the explicit validity assertion own malformed-value detection.
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
NATIVE_PARSE_OPERATIONS: tuple[type[ops.Node], ...] = (
    type(_sqlite_strptime(ibis.literal("x"), "%Y").op()),
    type(_clickhouse_strptime(ibis.literal("x"), "%Y", "UTC").op()),
)


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
    if engine == "sqlite":
        return _sqlite_strptime(text, fmt)
    if engine == "clickhouse":
        # The explicit zone argument keeps the parsed instant labelled UTC
        # whatever the session timezone is.
        return _clickhouse_strptime(text, fmt, "UTC")
    parsed: ir.Value = text.as_timestamp(fmt)
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
    hook is the one owner of who translates a format. DuckDB and SQLite consume
    the Python strptime format itself, so their translation is the identity and
    the emitted format is the authored one.

    Date-only formats stay date, exactly as before.
    """
    parsed = native_parse(engine, text, parse)
    if not is_time_bearing_format(parse.format):
        parsed = parsed.cast("date")
    return parsed


def malformed_strptime_rows(engine: str, text: ir.Value, parse: StrptimeParse) -> ir.Table | None:
    """Return the non-null text rows whose declared format does not parse.

    The check reads the value the parser actually returns, so a date-only
    format is asserted on its parser result as well: the civil-date cast would
    otherwise turn a malformed cell into a NULL coordinate that silently leaves
    the time axis.

    Only NULL-returning engines need this assertion: DuckDB raises on the same
    input, and PostgreSQL would return a wrong non-NULL instant that a NULL
    check cannot see, so an explicit check there would only add a second scan
    without changing the outcome.
    """
    if engine not in NULL_ON_MALFORMED_ENGINES:
        return None
    string = text.cast("string")
    if not isinstance(string, ir.StringValue):
        return None
    parsed = native_parse(engine, string, parse)
    if not isinstance(parsed, (ir.TimestampValue, ir.DateValue)):
        return None
    return string.as_table().filter(string.notnull() & parsed.isnull())


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


def boundary_instant(zone: str, value: ir.Value) -> ir.TimestampValue:
    """Use the first occurrence of a repeated civil boundary, with no machine tick."""
    wall = timestamp(value)
    native = localize(zone, wall)
    if time_zone(zone).utcoffset(None) is not None:
        return render("UTC", native)
    day = ibis.interval(seconds=86400)
    before = localize(zone, wall - day) + day
    after = localize(zone, wall + day) - day
    first = ibis.least(
        native,
        (render(zone, before) == wall).ifelse(before, native),
        (render(zone, after) == wall).ifelse(after, native),
    )
    return render("UTC", timestamp(first))


def source_time(
    value: ir.Value,
    axis: TargetDimensionContract,
    *,
    boundary_timezone: str,
    read_timezone: str | None,
    engine: str,
    read_source: Literal["engine", "system_fallback"] = "engine",
    prefix: ir.Value | None = None,
) -> tuple[ir.Value, SourceTimeAuthority, ir.TimestampValue | None]:
    """Parse and localize a column without executing it or consulting ambient state.

    The third element is the naive wall clock the axis contributes, before any
    civil-date cast, for a caller that must probe its own gap/fold legality.  It
    is ``None`` when the axis is already an exact instant or a civil date.
    """
    physical = value.type()
    parse = axis.parse
    declared = (
        parse.timezone
        if isinstance(parse, (DatetimeParse, TimestampParse, StrptimeParse))
        else None
    )
    if isinstance(parse, HourPrefixParse):
        if prefix is None:
            raise compilation_error("a bound civil-date hour prefix", "missing prefix axis")
        hour = value.cast("int64")
        if not isinstance(hour, ir.IntegerValue):
            raise compilation_error("an integer hour", "invalid hour representation")
        value = timestamp(prefix) + hour.as_interval("h")
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
    elif parse is not None and not isinstance(parse, (DatetimeParse, TimestampParse)):
        raise compilation_error("a bound supported time parser", "unresolved composite parser")
    if isinstance(value, ir.DateValue):
        if axis.logical_type != "date" or declared is not None:
            raise compilation_error(
                "a civil-date parser without timezone", "unsupported temporal source representation"
            )
        return (
            value,
            SourceTimeAuthority(
                axis=axis.ref.path,
                physical_type=str(physical),
                kind="civil_date",
                read_timezone=None,
                source="civil_date",
                boundary_timezone=boundary_timezone,
            ),
            None,
        )
    if not isinstance(value, ir.TimestampValue):
        raise compilation_error("a declared date or timestamp parser", "non-temporal source")
    kind = value.type()
    origin: Literal["physical", "declared", "engine", "system_fallback"]
    if kind.timezone is not None:
        if kind.scale is not None and kind.scale > 6:
            raise compilation_error(
                "exact native timezone conversion precision",
                "submicrosecond conversion unsupported",
            )
        if declared is not None and declared != kind.timezone:
            raise compilation_error("matching declared and physical timezones", "timezone conflict")
        adopted, origin = kind.timezone, "physical"
        instant = value
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
                )
            instant = localize(adopted, value)
    if origin not in ("physical", "declared", "engine", "system_fallback"):
        raise compilation_error("a registered read timezone source", "invalid time authority")
    authority = SourceTimeAuthority(
        axis=axis.ref.path,
        physical_type=str(physical),
        kind="instant" if kind.timezone is not None else "localizable",
        read_timezone=adopted,
        source=origin,
        boundary_timezone=boundary_timezone,
    )
    # A physical instant is already exact, and a converted value is an instant
    # rather than the wall clock a gap/fold guard must judge.  A naive value
    # keeps its own coordinate; a native precision the guard cannot round-trip
    # losslessly reports nothing rather than a lossy answer.
    naive = kind.timezone is None and not (kind.scale is not None and kind.scale > 6)
    return (
        value if instant is None else render(boundary_timezone, instant),
        authority,
        value if naive else None,
    )


def needs_reader_timezone(dataset: LogicalDataset) -> bool:
    """Use the exact dependency closure to avoid probing for already governed axes."""
    from marivo.analysis.compiler.normalize import logical_roots, required_source_dependencies
    from marivo.analysis.observation.contracts import MetricPayload, PopulationPayload

    dependencies = required_source_dependencies(dataset)
    for root in logical_roots(dataset):
        payload = root.payload
        axes: tuple[TargetDimensionContract | None, ...]
        if isinstance(payload, PopulationPayload):
            axes = (payload.reference_axis,)
        elif isinstance(payload, MetricPayload):
            axes = (
                payload.definition.reference_axis,
                payload.definition.time_axis,
                *payload.definition.dimensions,
            )
        else:
            continue
        for axis in axes:
            if axis is None or not axis.is_time_dimension or axis.logical_type == "date":
                continue
            physical = next(
                column.declared_type
                for entry in dependencies.entries
                if entry.entity.ref == axis.entity_ref
                for column in entry.columns
                if column.logical == axis.source_column
            )
            kind = dt.dtype(physical)
            if isinstance(kind, dt.Timestamp) and kind.timezone is not None:
                continue
            parser = axis.parse
            if (
                isinstance(parser, (DatetimeParse, TimestampParse, StrptimeParse))
                and parser.timezone
            ):
                continue
            if isinstance(parser, StrptimeParse) and (
                "%z" in parser.format or "%Z" in parser.format
            ):
                continue
            return True
    return False


def local_time_invalid(value: ir.TimestampValue, zone: str) -> ir.BooleanValue:
    """Reject gaps and repeated wall clocks instead of selecting an implicit fold."""
    instant = localize(zone, value)
    invalid = instant.isnull() | (render(zone, instant) != value)
    if time_zone(zone).utcoffset(None) is None:
        day = ibis.interval(seconds=86400)
        for candidate in (localize(zone, value - day) + day, localize(zone, value + day) - day):
            invalid = invalid | ((candidate != instant) & (render(zone, candidate) == value))
    return value.notnull() & invalid.fill_null(False)
