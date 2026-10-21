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


def encoded_time_predicate(
    value: ir.Value,
    axis: TargetDimensionContract,
    authority: SourceTimeAuthority,
    *,
    start: date | datetime | None,
    end: date | datetime | None,
) -> ir.BooleanValue | None:
    """Invert a half-open range on canonical encoded event points without IO.

    Bounds are in the consumer's normalized coordinate space. Civil-date
    consumers pass dates; timestamp consumers pass normalized datetimes.
    ``None`` means this encoding/time authority has no qualified inverse; the
    caller keeps its original semantic predicate. No source values are probed.
    """
    parse = axis.parse
    physical = value.type()
    if not isinstance(parse, StrptimeParse) or parse.format not in _ORDERED_FORMATS:
        return None
    if not physical.is_string() and not (
        physical.is_integer() and parse.format.replace("%", "").isalpha()
    ):
        return None
    source_zone = None
    boundary_zone = None
    if authority.kind != "civil_date":
        if authority.read_timezone is None:
            return None
        source_zone = time_zone(authority.read_timezone)
        boundary_zone = time_zone(authority.boundary_timezone)
        if source_zone.utcoffset(None) is None or boundary_zone.utcoffset(None) is None:
            return None

    step = _ORDERED_FORMATS[parse.format]

    def encode(bound: date | datetime) -> str | None:
        point = bound if isinstance(bound, datetime) else datetime.combine(bound, time())
        if source_zone is not None and boundary_zone is not None:
            # Conversion failure is caught outside the inverse; it must not be
            # confused with the ordered ceiling sentinel above year 9999.
            point = (
                point.replace(tzinfo=boundary_zone) if point.tzinfo is None else point
            ).astimezone(source_zone)
        point = point.replace(tzinfo=None)
        midnight = point.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed = point - midnight
        ticks, remainder = divmod(
            elapsed.seconds * 1_000_000 + elapsed.microseconds, step * 1_000_000
        )
        try:
            point = midnight + timedelta(seconds=(ticks + bool(remainder)) * step)
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
        encoded = parse.format
        for token, text in tokens.items():
            encoded = encoded.replace(token, text)
        return encoded

    def comparison(bound: date | datetime, *, lower: bool) -> ir.BooleanValue:
        text = encode(bound)
        if text is None:
            return ibis.literal(False) if lower else value.notnull()
        if physical.is_integer():
            number = int(text)
            assert isinstance(physical, dt.Integer)
            minimum, maximum = physical.bounds
            if number <= minimum:
                return value.notnull() if lower else ibis.literal(False)
            if number > maximum:
                return ibis.literal(False) if lower else value.notnull()
            literal = ibis.literal(number, type=physical)
        else:
            literal = ibis.literal(text, type=physical)
        return value >= literal if lower else value < literal

    try:
        if start is None:
            return value.notnull() if end is None else comparison(end, lower=False)
        predicate = comparison(start, lower=True)
        return predicate if end is None else predicate & comparison(end, lower=False)
    except OverflowError:
        return None


def _parse_signature_zoned(text: str, fmt: str, zone: str) -> datetime:
    raise NotImplementedError("native zoned string parse")


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


def invalid_local_time(value: ir.TimestampValue, zone: str) -> ir.BooleanValue:
    """Reject naive civil values with no unique instant using native conversions.

    Neighboring civil days supply the offsets on both sides of a transition;
    candidate instants are checked by rendering them back to the original wall
    value. This retains non-hour transitions and never guesses a fold.
    """
    day = ibis.interval(seconds=86400)
    instant = localize(zone, value)
    previous = localize(zone, value - day) + day
    following = localize(zone, value + day) - day
    return (
        (render(zone, instant) != value)
        | ((previous != instant) & (render(zone, previous) == value))
        | ((following != instant) & (render(zone, following) == value))
    ).fill_null(False)


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


def source_time(
    value: ir.Value,
    axis: TargetDimensionContract,
    *,
    boundary_timezone: str,
    read_timezone: str | None,
    engine: str,
    read_source: Literal["engine", "system_fallback"] = "engine",
    prefix: ir.Value | None = None,
    frozen: SourceTimeAuthority | None = None,
    validity_checks: list[ir.BooleanValue] | None = None,
    alternative_instants: list[ir.TimestampValue] | None = None,
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
        else isinstance(physical, dt.Timestamp) and physical.timezone is not None
    )
    if declared is not None and physical_is_aware:
        raise DatasetCompilationError(
            expected="an aware source without parser timezone",
            received="timezone declared on an already-aware physical source",
            repair="Remove timezone from ms.timestamp() or ms.strptime(...); the aware physical source already owns its clock.",
            location="analysis.source_time",
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
        elif declared is not None:
            adopted, origin = declared, "declared"
        else:
            adopted, origin = kind.timezone, "physical"
        instant = value
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
        if validity_checks is not None and time_zone(adopted).utcoffset(None) is None:
            validity_checks.append(invalid_local_time(value, adopted))
        if alternative_instants is not None and time_zone(adopted).utcoffset(None) is None:
            day = ibis.interval(seconds=86400)
            for candidate in (
                localize(adopted, value - day) + day,
                localize(adopted, value + day) - day,
            ):
                alternative_instants.append(
                    timestamp(
                        ibis.ifelse(
                            render(adopted, candidate) == value,
                            candidate,
                            ibis.null().cast(candidate.type()),
                        )
                    )
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
        kind="instant" if origin == "physical" else "localizable",
        read_timezone=adopted,
        source=origin,
        boundary_timezone=boundary_timezone,
    )
    return (
        value if instant is None else render(boundary_timezone, instant),
        _retain_authority(authority, frozen),
    )
