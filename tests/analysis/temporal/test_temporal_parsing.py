"""Independent per-backend expectations for native strptime compilation.

Every format expectation below is written from the backend's own parser
documentation and checked against ``datetime.strptime`` as the value oracle,
never against the production emitter. Where a statement names the exact SQL
literal a backend receives, the literal is read from the parse projection's
own AST arguments rather than matched inside the whole statement, because
sqlglot keeps the compiler's Python-format alias in the ``AS`` clause and a
substring match there cannot tell a correct format from a wrong one.
"""

import re
from datetime import datetime

import ibis
import pytest
import sqlglot
from sqlglot import expressions as sge

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.source_time import (
    parse_strptime,
    translated_strptime_format,
)
from marivo.analysis.materialization.temporal_sql import sqlite_strptime
from marivo.datasource.engines import ENGINE_PROFILES
from marivo.semantic.ir import StrptimeParse

ENGINES = ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")

# The exact parser each engine must reach, taken from its own documentation.
NATIVE_PARSER = {
    "duckdb": "STRPTIME",
    "sqlite": "_MARIVO_STRPTIME",
    "postgres": "TO_TIMESTAMP",
    "mysql": "STR_TO_DATE",
    "trino": "DATE_PARSE",
    "clickhouse": "parseDateTimeOrNull",
}

# What sqlglot emits for the translated ``'%Y-%m-%d %H:%i:%s'``. MySQL and Trino
# rewrite a trailing ``%H:%i:%s`` to ``%T``, the documented MySQL spelling of
# ``%H:%M:%S``; the value is unchanged, so this is the format the parser reads.
MYSQL_FAMILY_FULL_FORMAT = "%Y-%m-%d %T"

# The microsecond binding the naive parse must carry: a bare TIMESTAMP becomes
# MySQL's DATETIME, which truncates the fraction the format may have parsed.
NAIVE_TIMESTAMP_CAST = {
    "duckdb": "TIMESTAMP(6)",
    "postgres": "TIMESTAMP(6)",
    "mysql": "DATETIME(6)",
    "trino": "TIMESTAMP(6)",
}


_STRING_LITERAL = re.compile(r"'((?:[^']|'')*)'")

# The projection alias ibis adds after the parse expression. It repeats the
# authored Python format, so any assertion over the whole statement can pass on
# the alias alone; only the ``AS`` immediately before ``FROM`` is removed.


def _sql(engine: str, fmt: str, *, timezone: str | None = None) -> str:
    table = ibis.table({"c": "string"}, name="t")
    parse = StrptimeParse(fmt, timezone=timezone) if timezone else StrptimeParse(fmt)
    expression = parse_strptime(engine, table.c.cast("string"), parse)
    return " ".join(ibis.to_sql(expression, dialect=engine).split())


def _parse_projection(engine: str, fmt: str, *, timezone: str | None = None) -> sge.Expression:
    """Return the parse projection, excluding the compiler's ``AS`` alias.

    The alias repeats the authored Python format, so any assertion that reads
    the whole statement can pass on the alias alone, whatever literal the
    parser actually receives.
    """
    ast = sqlglot.parse_one(_sql(engine, fmt, timezone=timezone), read=engine)
    select = ast.find(sge.Select)
    assert select is not None
    return select.expressions[0].unalias()


def _parser_arguments(engine: str, fmt: str, *, timezone: str | None = None) -> list[str]:
    """Read the string literals actually passed to *engine*'s parser function.

    The projection is rendered back through the engine's own dialect, so the
    scanned text is what the server receives: PostgreSQL keeps its
    ``HH24:MI:SS`` template literal and MySQL/Trino expose the ``%T`` sqlglot
    rewrote for them.
    """
    rendered = _parse_projection(engine, fmt, timezone=timezone).sql(dialect=engine)
    literals = [match.group(1).replace("''", "'") for match in _STRING_LITERAL.finditer(rendered)]
    assert literals, f"{engine} emitted no literal parser argument in {rendered!r}"
    return literals


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("fmt", ["%Y-%m-%d %H:%M:%S", "%Y%m%d", "%d/%m/%Y"])
def test_every_engine_reaches_its_own_parser(engine: str, fmt: str) -> None:
    """Each backend compiles to its documented native parse function."""
    sql = _sql(engine, fmt)
    assert NATIVE_PARSER[engine].lower() in sql.lower()


@pytest.mark.parametrize(
    "engine,expected",
    [
        ("duckdb", "%Y-%m-%d %H:%M:%S"),
        ("sqlite", "%Y-%m-%d %H:%M:%S"),
        ("postgres", "YYYY-MM-DD HH24:MI:SS"),
        ("mysql", MYSQL_FAMILY_FULL_FORMAT),
        ("trino", MYSQL_FAMILY_FULL_FORMAT),
        ("clickhouse", "%Y-%m-%d %H:%i:%s"),
    ],
)
def test_engine_final_formats_are_never_re_translated(engine: str, expected: str) -> None:
    """The parser receives the documented literal, not the compiler's alias.

    Both MySQL and Trino actually read ``'%Y-%m-%d %T'``: sqlglot re-maps the
    translated ``%H:%i:%s``, and ``%T`` is MySQL's documented ``%H:%M:%S``, so
    the minute is still minutes. PostgreSQL and ClickHouse keep the profile's
    exact translation, DuckDB and SQLite the authored Python format.
    """
    literals = _parser_arguments(engine, "%Y-%m-%d %H:%M:%S", timezone="UTC")
    assert literals[0] == expected


@pytest.mark.parametrize("engine", ENGINES)
def test_date_only_formats_stay_dates_and_timed_formats_stay_timestamps(engine: str) -> None:
    """The date-only rule is preserved: no time directive means a civil date."""
    table = ibis.table({"c": "string"}, name="t")
    text = table.c.cast("string")
    assert parse_strptime(engine, text, StrptimeParse("%Y%m%d")).type().is_date()
    assert not (
        parse_strptime(engine, text, StrptimeParse("%Y-%m-%d %H:%M:%S", timezone="UTC"))
        .type()
        .is_date()
    )


@pytest.mark.parametrize("engine", ["postgres", "mysql", "trino", "clickhouse"])
def test_translated_engines_never_receive_the_python_minute_token(engine: str) -> None:
    """Only DuckDB and SQLite read the authored Python format verbatim.

    On these four a surviving ``%M`` is a month name (MySQL family) or a bare
    PostgreSQL template pattern, so the minute directive must be gone from the
    emitted format.
    """
    literals = _parser_arguments(engine, "%Y-%m-%d %H:%M:%S", timezone="UTC")
    assert not any("%M" in literal for literal in literals)


def test_postgres_uses_hh24_for_the_24_hour_clock() -> None:
    """PostgreSQL's bare ``HH`` is the 12-hour clock, so ``%H`` must be ``HH24``."""
    assert translated_strptime_format("postgres", "%H") == "HH24"
    assert _parser_arguments("postgres", "%Y-%m-%d %H:%M:%S", timezone="UTC")[0] == (
        "YYYY-MM-DD HH24:MI:SS"
    )


def test_clickhouse_translates_minutes_and_keeps_microseconds_out() -> None:
    assert translated_strptime_format("clickhouse", "%M") == "%i"
    assert _parser_arguments("clickhouse", "%Y-%m-%d %H:%M:%S", timezone="UTC") == [
        "%Y-%m-%d %H:%i:%s",
        "UTC",
    ]


@pytest.mark.parametrize("engine", ["duckdb", "postgres", "mysql", "trino"])
def test_naive_parses_reach_the_parser_at_microsecond_scale(engine: str) -> None:
    """A bare TIMESTAMP would truncate the fraction MySQL's DATETIME keeps.

    The binding is load bearing for MySQL: ``DATETIME`` drops the fraction that
    ``DATETIME(6)`` preserves.
    """
    projection = _parse_projection(engine, "%Y-%m-%d %H:%M:%S", timezone="UTC")
    assert isinstance(projection, sge.Cast), "the naive parse must bind an explicit scale"
    assert projection.to.sql(dialect=engine) == NAIVE_TIMESTAMP_CAST[engine]


@pytest.mark.parametrize("engine", ["mysql", "trino"])
def test_mysql_family_actually_reads_the_collapsed_hour_format(engine: str) -> None:
    """sqlglot re-maps ``%H:%i:%s`` to ``%T``, MySQL's documented ``%H:%M:%S``.

    The assertion reads the parser argument, so the compiler's alias cannot
    satisfy it: a doubly translated format would show up here as a wrong value.
    """
    assert _parser_arguments(engine, "%Y-%m-%d %H:%M:%S", timezone="UTC")[0] == (
        MYSQL_FAMILY_FULL_FORMAT
    )


def test_trino_fractional_format_reaches_date_parse() -> None:
    """Fractional seconds follow Trino date_parse's native precision."""
    assert _parser_arguments("trino", "%Y-%m-%d %H:%M:%S.%f", timezone="UTC")[0] == (
        "%Y-%m-%d %T.%f"
    )


@pytest.mark.parametrize(
    "fmt,reason",
    [
        ("%Y-%m-%d %H:%M:%S.%f", "sub-second"),
        ("%Y-%m-%d %H:%M:%S", "expressible"),
    ],
)
def test_clickhouse_gates_on_format_expressibility(fmt: str, reason: str) -> None:
    """A format ClickHouse cannot express exactly is refused, never truncated.

    ``parseDateTime`` returns second-precision ``DateTime``, so a sub-second
    directive would silently drop the fraction.
    """
    table = ibis.table({"c": "string"}, name="t")
    parse = StrptimeParse(fmt, timezone="UTC")
    if reason == "sub-second":
        with pytest.raises(DatasetCompilationError) as failure:
            parse_strptime("clickhouse", table.c.cast("string"), parse)
        assert "sub-second" in (failure.value.received or "")
    else:
        assert parse_strptime("clickhouse", table.c.cast("string"), parse) is not None


@pytest.mark.parametrize("fmt", ["%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S%Z"])
def test_timezone_directives_never_reach_authoring_or_clickhouse(fmt: str) -> None:
    """This stage excludes offset/zone parsing, and authoring already refuses it.

    ``normalize_strptime`` probes the format through ``time.strptime``, which
    rejects both directives, so no ClickHouse-specific timezone gate is needed
    at compile time: an unauthorable format cannot reach the parser.
    """
    from marivo.semantic.time_format import normalize_strptime

    with pytest.raises(ValueError):
        normalize_strptime(fmt)


@pytest.mark.parametrize("engine", ["mysql", "trino", "clickhouse"])
def test_divergent_directives_are_structured_rejections(engine: str) -> None:
    """A directive the engine cannot express is a typed error, not a fallback."""
    with pytest.raises(DatasetCompilationError) as failure:
        translated_strptime_format(engine, "%Y-%W-%d")
    assert "untranslatable" in (failure.value.received or "")


def test_clickhouse_refuses_week_number_directives_it_cannot_execute() -> None:
    """ClickHouse rejects ``%U`` at execution with an opaque ``Code: 48`` error.

    A raw driver failure names neither the axis nor the format, so the
    unsupported directive is refused during compilation instead. The
    MySQL-family translator already rejects Python ``%W`` before translation,
    so only ``%U`` reaches this gate.
    """
    with pytest.raises(DatasetCompilationError) as failure:
        translated_strptime_format("clickhouse", "%Y-%U-%d")
    assert "%U" in (failure.value.received or "")


@pytest.mark.parametrize("directive", ["%a", "%A", "%w"])
def test_clickhouse_refuses_directives_it_silently_mis_parses(directive: str) -> None:
    """Weekday directives shift the instant instead of parsing it.

    ``parseDateTimeOrNull('2026-07-01 Wed', '%Y-%m-%d %a', 'UTC')`` answers
    2025-12-31 on ClickHouse 26.3.33.24: a well-formed cell, no error, and a
    non-NULL instant a whole week away. The weekday name is only *ignored* when
    it leads the format, which is why the same directive can look correct in
    one shape and wrong in another. Python ``%A`` reaches the same trap through
    the MySQL ``%W`` the translator emits for it. A directive that cannot
    produce the authored instant is not exactly expressible, so it is refused
    during compilation instead of being pinned as a working mapping.
    """
    fmt = f"%Y-%m-%d {directive}"
    with pytest.raises(DatasetCompilationError) as failure:
        translated_strptime_format("clickhouse", fmt)
    assert directive in (failure.value.received or "")


def test_clickhouse_keeps_the_calendar_directives_it_expresses_exactly() -> None:
    """Month names and the plain calendar and clock directives stay admitted.

    ``%b`` and ``%B`` were measured against ``datetime.strptime`` on well-formed
    cells and answer the authored instant, so refusing them would over-reject.
    """
    assert translated_strptime_format("clickhouse", "%b %d %Y") == "%b %d %Y"
    assert translated_strptime_format("clickhouse", "%B %d %Y") == "%M %d %Y"
    assert translated_strptime_format("clickhouse", "%Y-%m-%d %H:%M:%S") == "%Y-%m-%d %H:%i:%s"


def test_escaped_percent_is_not_mistaken_for_a_directive() -> None:
    """``%%`` is a literal percent, so ``%%f`` carries no sub-second directive."""
    assert translated_strptime_format("clickhouse", "%%f-%Y%m%d") == "%%f-%Y%m%d"
    assert translated_strptime_format("mysql", "%%f") == "%%f"
    with pytest.raises(DatasetCompilationError) as failure:
        translated_strptime_format("clickhouse", "%Y%m%d%f")
    assert "sub-second" in (failure.value.received or "")


def test_every_engine_profile_has_a_strptime_translator() -> None:
    """The hook is populated for all six engines, so none silently bypasses it."""
    for backend_type in ENGINE_PROFILES:
        assert callable(ENGINE_PROFILES[backend_type].translate_strptime_format)
        assert ENGINE_PROFILES[backend_type].translate_strptime_format("%Y%m%d")


def test_sqlite_scalar_parses_to_canonical_microsecond_text() -> None:
    """The SQLite scalar mirrors ``datetime.strptime`` on the exact input."""
    fmt = "%Y-%m-%d %H:%M:%S"
    text = "2026-07-01 15:59:00"
    assert sqlite_strptime(text, fmt) == datetime.strptime(text, fmt).isoformat(
        sep=" ", timespec="microseconds"
    )
    assert sqlite_strptime(None, fmt) is None


def test_sqlite_scalar_answers_null_for_an_unparseable_cell() -> None:
    """A malformed cell returns NULL without a separate source-data preflight.

    Raising would cross the driver boundary as an opaque
    ``user-defined function raised exception``.
    """
    assert sqlite_strptime("not-a-date", "%Y-%m-%d") is None
    assert sqlite_strptime("2026-02-30", "%Y-%m-%d") is None
