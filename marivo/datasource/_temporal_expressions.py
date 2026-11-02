"""Shared source-native temporal expressions for Semantic and Analysis."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir

from marivo._timezones import normalize_timezone


class TemporalExpressionError(ValueError):
    """An unsupported native temporal expression configuration."""

    def __init__(self, expected: str, received: str, repair: str | None = None) -> None:
        self.expected = expected
        self.received = received
        self.repair = repair
        super().__init__(f"Expected {expected}; received {received}")


def temporal_expression_error(
    expected: str, received: str, *, repair: str | None = None
) -> TemporalExpressionError:
    return TemporalExpressionError(expected, received, repair)


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
# while other engines retain their own native defaults. No library-level
# date completion is added.
_CLICKHOUSE_INEXACT_DIRECTIVES: frozenset[str] = frozenset({"%U", "%a", "%A", "%w"})

# A real ``%<letter>`` directive, as opposed to an escaped literal percent.
_STRPTIME_DIRECTIVE = re.compile(r"%[A-Za-z]")


def _parse_signature_zoned(text: str, fmt: str, zone: str) -> datetime:
    raise NotImplementedError("native zoned string parse")


def _parse_signature_clock(text: str, fmt: str) -> datetime:
    raise NotImplementedError("native canonical DuckDB time parse")


# Retain a compiler-owned parser, separate from arbitrary authored Ibis
# StringToTimestamp expressions.
_duckdb_strptime: Callable[[ir.StringValue, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _parse_signature_clock,
    name="try_strptime",
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
    if engine == "trino":
        unsupported = {"%U", "%w"} & set(_STRPTIME_DIRECTIVE.findall(fmt.replace("%%", "")))
        if unsupported:
            raise temporal_expression_error(
                "a format supported by Trino date_parse",
                f"unsupported directive {sorted(unsupported)[0]} in {fmt[:200]!r}",
                repair="Use calendar date directives such as %Y-%m-%d, or expose a native temporal column. Unsupported format configuration cannot be wrapped in TRY.",
            )
    if engine == "clickhouse":
        # ``%%`` is an escaped literal percent, not a directive, so the scan
        # removes those pairs before looking for any directive. ``%%f`` parses
        # a literal ``%`` followed by ``f`` and must stay admitted.
        directives = set(_STRPTIME_DIRECTIVE.findall(fmt.replace("%%", "")))
        truncating = _CLICKHOUSE_TRUNCATING_DIRECTIVES & directives
        if truncating:
            raise temporal_expression_error(
                "a strptime format that ClickHouse parseDateTime can express exactly",
                f"ClickHouse would truncate the sub-second directive in format {fmt[:200]!r}",
            )
        inexact = _CLICKHOUSE_INEXACT_DIRECTIVES & directives
        if inexact:
            raise temporal_expression_error(
                "a strptime format that ClickHouse parseDateTime can express exactly",
                f"ClickHouse cannot express directive {sorted(inexact)[0]} exactly "
                f"in format {fmt[:200]!r}: it either fails or shifts the parsed value",
            )
    try:
        return profile.translate_strptime_format(fmt)
    except ValueError as cause:
        raise temporal_expression_error(
            f"a strptime format expressible on the {profile.name} backend",
            f"untranslatable strptime format for the {profile.name} backend",
        ) from cause


def _needs_naive_binding(fmt: str) -> bool:
    return "%z" not in fmt and "%Z" not in fmt


_postgres_strptime: Callable[[ir.StringValue, str], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _parse_signature_clock,
    name="to_timestamp",
    signature=((dt.string, dt.string), dt.Timestamp(timezone="UTC", scale=6)),
)


def _try_signature(value: datetime) -> datetime:
    raise NotImplementedError("native Trino recoverable expression errors")


_trino_try: Callable[[ir.TimestampValue], ir.TimestampValue] = ibis.udf.scalar.builtin(
    _try_signature, name="try", signature=((dt.Timestamp(scale=6),), dt.Timestamp(scale=6))
)


def _mysql_guard_signature(value: datetime, span: timedelta) -> datetime:
    raise NotImplementedError("native MySQL valid calendar carrier")


_mysql_calendar_guard: Callable[[ir.TimestampValue, ir.IntervalValue], ir.TimestampValue] = (
    ibis.udf.scalar.builtin(
        _mysql_guard_signature,
        name="date_add",
        signature=((dt.Timestamp(scale=6), dt.Interval(unit="D")), dt.Timestamp(scale=6)),
    )
)

_GOVERNED_PARSE_TYPES = (
    type(_duckdb_strptime(ibis.literal("20000101"), "%Y%m%d").op()),
    type(_postgres_strptime(ibis.literal("20000101"), "YYYYMMDD").op()),
    type(_trino_try(ibis.timestamp("2000-01-01")).op()),
    type(_mysql_calendar_guard(ibis.timestamp("2000-01-01"), ibis.interval(days=0)).op()),
    type(_clickhouse_strptime(ibis.literal("20000101"), "%Y%m%d", "UTC").op()),
)


def governed_parse_operation(node: ops.Node) -> bool:
    """Recognize this compiler's parsers for neutral-clock membership."""
    return type(node) in _GOVERNED_PARSE_TYPES


def native_parse(engine: str, text: ir.StringValue, format: str) -> ir.Value:
    """Parse data using the owning engine's error and argument boundary."""
    fmt = translated_strptime_format(engine, format)
    if engine == "clickhouse":
        # The explicit zone argument keeps the parsed instant labelled UTC
        # whatever the session timezone is.
        return _clickhouse_strptime(text, fmt, "UTC")
    parsed: ir.Value = (
        _duckdb_strptime(text, fmt)
        if engine == "duckdb"
        else _postgres_strptime(text, fmt)
        if engine == "postgres"
        else text.as_timestamp(fmt)
    )
    if _needs_naive_binding(format):
        # Ibis labels StringToTimestamp UTC although a parser without an
        # offset returns a naive timestamp. Bind its actual value domain at
        # microsecond scale: a bare TIMESTAMP becomes MySQL's DATETIME,
        # which silently drops the fraction the format may have parsed.
        parsed = parsed.cast(dt.Timestamp(scale=6))
    if engine == "trino":
        assert isinstance(parsed, ir.TimestampValue)
        parsed = _trino_try(parsed)
    elif engine == "mysql":
        # SELECT STR_TO_DATE returns warnings/NULL for malformed fields, but
        # MariaDB can retain impossible calendar dates. Native date arithmetic
        # rejects those before they reach the temporal carrier/driver.
        assert isinstance(parsed, ir.TimestampValue)
        parsed = _mysql_calendar_guard(parsed, ibis.interval(days=0))
    return parsed


def parse_strptime(
    engine: str, text: ir.StringValue, format: str, *, time_bearing: bool
) -> ir.Value:
    """Parse string text with *engine*'s native parser and the authored format.

    Every engine goes through :func:`translated_strptime_format`, so the profile
    hook is the one owner of who translates a format. DuckDB consumes
    the Python strptime format itself, so their translation is the identity and
    the emitted format is the authored one.

    Date-only formats stay date, exactly as before.
    """
    parsed = native_parse(engine, text, format)
    if not time_bearing:
        parsed = parsed.cast("date")
    return parsed


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
        raise temporal_expression_error("a temporal value", "invalid timestamp expression")
    return result


def localize(zone: str, value: ir.TimestampValue) -> ir.TimestampValue:
    normalized = normalize_timezone(zone)
    resolved = normalized.tz
    if normalized.kind == "fixed_offset":
        offset = resolved.utcoffset(None)
        if offset is None:
            raise temporal_expression_error("an exact fixed offset", "missing timezone offset")
        return _localize_native("UTC", value - ibis.interval(seconds=int(offset.total_seconds())))
    return _localize_native(zone, value)


def render(zone: str, value: ir.TimestampValue) -> ir.TimestampValue:
    normalized = normalize_timezone(zone)
    resolved = normalized.tz
    if normalized.kind == "fixed_offset":
        offset = resolved.utcoffset(None)
        if offset is None:
            raise temporal_expression_error("an exact fixed offset", "missing timezone offset")
        return _render_native("UTC", value) + ibis.interval(seconds=int(offset.total_seconds()))
    return _render_native(zone, value)
