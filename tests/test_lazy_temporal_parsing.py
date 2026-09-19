"""Independent per-backend expectations for native strptime compilation.

Every format expectation below is written from the backend's own parser
documentation and checked against ``datetime.strptime`` as the value oracle,
never against the production emitter. Where a statement names the exact SQL
literal a backend receives, the literal is read from the parse projection's
own AST arguments rather than matched inside the whole statement, because
sqlglot keeps the compiler's Python-format alias in the ``AS`` clause and a
substring match there cannot tell a correct format from a wrong one.
"""

import os
import re
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import ibis
import pytest
import sqlglot
from sqlglot import expressions as sge

from marivo.analysis import grain, time_scope
from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.source_time import (
    NULL_ON_MALFORMED_ENGINES,
    entity_engine,
    malformed_strptime_rows,
    parse_strptime,
    translated_strptime_format,
)
from marivo.analysis.materialization.temporal_sql import sqlite_strptime
from marivo.analysis.observation.metric import LogicalMetricDataset
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.datasource.engines import ENGINE_PROFILES
from marivo.refs import ref
from marivo.semantic.ir import StrptimeParse
from tests.lazy_execution_fixtures import ExecutionFixture, assert_compiled_validations
from tests.lazy_observation_fixtures import NoIoActionPort
from tests.lazy_temporal_fixtures import AXIS, temporal_fixture

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
_ALIAS_SUFFIX = re.compile(r" AS (`[^`]*`|\"[^\"]*\")\s+FROM ")


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


def _parse_expression(engine: str, fmt: str, *, timezone: str | None = None) -> str:
    """Return only the emitted parse expression, without SELECT, alias or FROM."""
    statement = _sql(engine, fmt, timezone=timezone)
    select = _ALIAS_SUFFIX.sub(" FROM ", statement).split("SELECT ", 1)[1]
    return select.split(" FROM ", 1)[0]


def _with_cell(sql: str, cell: str) -> str:
    """Inline one cell literal into compiled SQL, keeping the emitted format text.

    Cells are inlined rather than bound because MySQLdb's own parameter binding
    percent-formats the statement and would read the emitted ``%`` directives as
    placeholders.
    """
    return sql.replace("`t0`.`c`", f"'{cell}'")


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


def _sources_declared_for(fixture: ExecutionFixture, engine: str) -> LazySources:
    """Declare every datasource of *fixture* as *engine*, keeping the same physical rows."""
    registry = replace(
        fixture.registry,
        datasources={
            name: replace(datasource, backend_type=engine)
            for name, datasource in fixture.registry.datasources.items()
        },
    )
    registry.freeze()
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=fixture.sidecar,
        action_port=NoIoActionPort(),
        session_id="strptime",
        store_id="strptime",
    )


def _daily_dataset(sources: LazySources) -> LogicalMetricDataset:
    return (
        sources.observe(
            ref.metric("sales.revenue"),
            time_scope=time_scope(start="2026-07-01", end="2026-07-03"),
        )
        .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
        .aggregate()
    )


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


def test_clickhouse_weekday_format_is_refused_before_publication(tmp_path: Path) -> None:
    """A well-formed weekday cell is refused at compilation, never published.

    The source holds exactly the cells the format asks for and the datasource is
    declared as ClickHouse, so nothing about the data or the declaration is
    malformed. Compilation still stops before a single query is submitted: the
    wrong instant the server would answer for these cells is unreachable.
    """
    fmt = "%Y-%m-%d %a"
    with temporal_fixture(
        tmp_path,
        physical="VARCHAR",
        declared="string",
        parse=StrptimeParse(fmt),
        values=("2026-07-01 Wed", "2026-07-02 Thu"),
    ) as fixture:
        logical = _daily_dataset(_sources_declared_for(fixture, "clickhouse"))
        with pytest.raises(DatasetCompilationError) as failure:
            compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")
    received = failure.value.received or ""
    assert "%a" in received, received
    assert fmt in received, received


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


def test_only_null_returning_engines_get_the_explicit_assertion() -> None:
    """The malformed-value assertion is scoped to the engines that need it.

    PostgreSQL is deliberately outside the set: ``TO_TIMESTAMP`` can answer a
    malformed cell with a wrong non-NULL instant, which a NULL check cannot
    detect. DuckDB raises, and the execution adapter turns that into a
    structured failure.
    """
    table = ibis.table({"c": "string"}, name="t")
    parse = StrptimeParse("%Y-%m-%d %H:%M:%S", timezone="UTC")
    for engine in ENGINES:
        rows = malformed_strptime_rows(engine, table.c, parse)
        if engine in NULL_ON_MALFORMED_ENGINES:
            assert rows is not None, engine
        else:
            assert rows is None, engine


@pytest.mark.parametrize("engine", ["sqlite", "mysql", "clickhouse"])
@pytest.mark.parametrize("fmt", ["%Y%m%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"])
def test_date_only_parses_are_asserted_on_the_parser_result(engine: str, fmt: str) -> None:
    """A date-only format must not skip the malformed-value assertion.

    The civil-date cast turns a malformed cell into a NULL coordinate, which
    silently leaves the time axis; the assertion reads the parser result before
    that cast.
    """
    table = ibis.table({"c": "string"}, name="t")
    rows = malformed_strptime_rows(engine, table.c, StrptimeParse(fmt))
    assert rows is not None, engine
    sql = " ".join(ibis.to_sql(rows, dialect=engine).split())
    assert "IS NULL" in sql or "isNull" in sql
    parser = NATIVE_PARSER[engine]
    assert parser.lower() in sql.lower()
    # The guard reads the parser result; the civil-date cast would hide the
    # NULL it must report, so no DATE cast may appear in the predicate.
    casts = [
        node.to.sql(dialect=engine).upper()
        for node in sqlglot.parse_one(sql, read=engine).find_all(sge.Cast)
    ]
    assert "DATE" not in casts, sql


@pytest.mark.parametrize("engine", ["sqlite", "mysql", "clickhouse"])
def test_date_only_assertion_counts_only_malformed_cells(engine: str) -> None:
    """The guard names exactly the non-null cells the declared format rejects."""
    table = ibis.table({"c": "string"}, name="t")
    rows = malformed_strptime_rows(engine, table.c, StrptimeParse("%Y%m%d"))
    assert rows is not None
    assert rows.schema().names == ("c",)
    sql = " ".join(ibis.to_sql(rows, dialect=engine).split())
    assert "IS NOT NULL" in sql or "isNotNull" in sql


def test_date_only_guard_reports_the_malformed_sqlite_cell() -> None:
    """The widened guard is executable: it names the bad cell and spares the rest.

    SQLite is the engine whose parser is local, so the whole guard runs here
    without a service. ``datetime.strptime`` remains the oracle for which cells
    are malformed.
    """
    connection = ibis.sqlite.connect(":memory:")
    connection.raw_sql("CREATE TABLE t (c TEXT)")
    connection.raw_sql(
        "INSERT INTO t VALUES ('20260701'), ('bad'), (NULL), ('20261301'), ('20260702')"
    )
    connection.con.create_function("_marivo_strptime", 2, sqlite_strptime, deterministic=True)
    rows = malformed_strptime_rows("sqlite", connection.table("t").c, StrptimeParse("%Y%m%d"))
    assert rows is not None
    try:
        counted = rows.order_by("c").execute()
        assert counted["c"].tolist() == ["20261301", "bad"]
    finally:
        connection.disconnect()


@pytest.mark.parametrize("engine", ["duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse"])
@pytest.mark.parametrize(
    "fmt", ["%Y%m%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y"], ids=["date-only", "timed", "slashes"]
)
def test_compiled_dataset_carries_the_strptime_assertion(
    engine: str, fmt: str, tmp_path: Path
) -> None:
    """The compiler turns the malformed-value guard into one named validation.

    ``temporal_fixture`` compiles against DuckDB, so only the datasource
    declaration is swapped to *engine*: the assertion under test is the
    compiler's, which resolves the parser from that declaration.
    """
    with temporal_fixture(
        tmp_path,
        physical="VARCHAR",
        declared="string",
        parse=StrptimeParse(fmt, timezone="UTC") if "%H" in fmt else StrptimeParse(fmt),
        values=("20260701", "20260702"),
    ) as fixture:
        logical = _daily_dataset(_sources_declared_for(fixture, engine))
        compiled = compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")
        strptime = [
            validation for validation in compiled.validations if "strptime" in validation.name
        ]
        if engine in NULL_ON_MALFORMED_ENGINES:
            assert [validation.name for validation in strptime] == [
                f"temporal.strptime_format.{AXIS}"
            ]
        else:
            assert strptime == []


def test_sqlite_scalar_parses_to_canonical_microsecond_text() -> None:
    """The SQLite scalar mirrors ``datetime.strptime`` on the exact input."""
    fmt = "%Y-%m-%d %H:%M:%S"
    text = "2026-07-01 15:59:00"
    assert sqlite_strptime(text, fmt) == datetime.strptime(text, fmt).isoformat(
        sep=" ", timespec="microseconds"
    )
    assert sqlite_strptime(None, fmt) is None


def test_sqlite_scalar_answers_null_for_an_unparseable_cell() -> None:
    """A malformed cell returns NULL so the compiled assertion owns the report.

    Raising would cross the driver boundary as an opaque
    ``user-defined function raised exception`` instead of naming the axis.
    """
    assert sqlite_strptime("not-a-date", "%Y-%m-%d") is None
    assert sqlite_strptime("2026-02-30", "%Y-%m-%d") is None


@pytest.mark.parametrize(
    "engine,is_asserted",
    [
        ("sqlite", True),
        ("mysql", True),
        ("clickhouse", True),
        ("duckdb", False),
        ("postgres", False),
    ],
)
def test_malformed_input_split_is_asserted_not_assumed(engine: str, is_asserted: bool) -> None:
    """Only the NULL-returning engines get Marivo's assertion, for two reasons.

    ``datetime.strptime`` is the oracle: every cell it rejects must either be
    asserted by Marivo or reach the caller as some other explicit outcome.
    DuckDB and PostgreSQL are both outside the asserted set, but not for the
    same reason -- DuckDB raises on the same input, which the execution adapter
    converts to a structured failure, while PostgreSQL ``TO_TIMESTAMP`` is
    *lenient* and can answer a wrong non-NULL instant that no NULL check can
    see. The assertion is therefore scoped to the engines whose parser answers
    a malformed cell with NULL, and PostgreSQL's leniency is a documented
    limitation rather than a guarded case.
    """
    from datetime import datetime

    from marivo.analysis.materialization.temporal_sql import sqlite_strptime

    malformed = ["bad", "2026-13-01 00:00:00", ""]
    for text in malformed:
        with pytest.raises(ValueError):
            datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    if engine == "sqlite":
        assert all(sqlite_strptime(text, "%Y-%m-%d %H:%M:%S") is None for text in malformed)
    assert (engine in NULL_ON_MALFORMED_ENGINES) is is_asserted


@pytest.mark.parametrize("engine", ["sqlite", "mysql", "trino", "clickhouse"])
def test_entity_engine_resolves_from_the_declared_datasource(engine: str, tmp_path: Path) -> None:
    """The compiler learns the target engine from the owning datasource.

    This is the same authority ``placement.source_binding`` reads, so the
    parse never invents a backend of its own.
    """
    from marivo.semantic.validator import normalize_target_entity
    from tests.lazy_scalar_source_fixtures import registry_for

    registry, _sidecar = registry_for(tmp_path / "source.db", engine=engine)
    entity = normalize_target_entity(registry, "sales.orders")
    assert entity_engine(registry, entity) == engine


@pytest.mark.parametrize(
    "fmt,values,key",
    [
        ("%Y-%m-%d %H:%M:%S", ("2026-07-01 15:59:00", "2026-07-01 16:01:00"), "2026-07-01"),
        ("%Y%m%d", ("20260701", "20260702"), "2026-07-01"),
    ],
)
def test_duckdb_strptime_paths_still_execute(
    fmt: str, values: tuple[str, str], key: str, tmp_path: Path
) -> None:
    """The unconditioned DuckDB path keeps its pre-existing behavior."""
    parse = StrptimeParse(fmt, timezone="UTC") if "%H" in fmt else StrptimeParse(fmt)
    with temporal_fixture(
        tmp_path, physical="VARCHAR", declared="string", parse=parse, values=values
    ) as fixture:
        logical = (
            fixture.sources.observe(
                ref.metric("sales.revenue"),
                time_scope=time_scope(start="2026-07-01", end="2026-07-03"),
            )
            .with_time_axis(ref.time_dimension(AXIS), grain=grain("day"))
            .aggregate()
        )
        compiled = compile_dataset(logical, fixture.tables(logical), read_timezone="UTC")
        assert_compiled_validations(compiled.validations)
        rows = compiled.expression.to_pyarrow().to_pylist()
        assert {str(row["order_time"])[:10]: row["revenue"] for row in rows} == {
            key: 1.0,
            str(date.fromisoformat(key).replace(day=2)): 2.0,
        }


def _live_mysql_connection() -> object:
    from tests.multisource_environment import mysql_analysis as mysql

    if os.environ.get("MARIVO_MYSQL_ANALYSIS_TEST") != "1":
        pytest.skip("opt-in MySQL service")
    import MySQLdb

    return MySQLdb.connect(
        host=mysql.HOST,
        port=mysql.PORT,
        database=mysql.DATABASE,
        user=mysql.READER,
        password=mysql.password(),
        autocommit=True,
    )


@pytest.mark.runtime
def test_mysql_keeps_the_microseconds_the_parse_reads() -> None:
    """``DATETIME(6)`` preserves the fraction a bare ``DATETIME`` truncates.

    Both statements are MySQL's own execution of the emitted ``STR_TO_DATE``
    text, so the difference comes only from the binding Marivo emits. Cells are
    inlined because MySQLdb's parameter binding itself percent-formats the SQL
    and would read the format's ``%`` directives as placeholders.
    """
    cell = "2026-07-01 15:59:00.123456"
    unpinned = "2026-07-01 15:59:00"
    projection = _parse_expression("mysql", "%Y-%m-%d %H:%M:%S.%f", timezone="UTC")
    assert "DATETIME(6)" in projection, projection
    unbound = projection.replace("DATETIME(6)", "DATETIME")
    connection = _live_mysql_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT CAST({_with_cell(projection, cell)} AS CHAR)")
            parsed = cursor.fetchone()[0]
            cursor.execute(f"SELECT CAST({_with_cell(unbound, cell)} AS CHAR)")
            truncated = cursor.fetchone()[0]
            cursor.execute(f"SELECT CAST({_with_cell(unbound, unpinned)} AS CHAR)")
            unpinned_ok = cursor.fetchone()[0]
    finally:
        connection.close()
    assert str(parsed) == "2026-07-01 15:59:00.123456", (
        "the emitted DATETIME(6) binding must keep the parsed fraction"
    )
    assert str(truncated) == "2026-07-01 15:59:00", (
        "without the binding the same parse loses the fraction"
    )
    assert str(unpinned_ok) == unpinned, (
        "a text cell without the fraction is unaffected by the binding"
    )


@pytest.mark.runtime
def test_mysql_date_only_guard_counts_a_malformed_cell() -> None:
    """The compiled date-only assertion names the malformed cell and only it."""
    table = ibis.table({"c": "string"}, name="t")
    rows = malformed_strptime_rows("mysql", table.c, StrptimeParse("%Y%m%d"))
    assert rows is not None
    predicate = " ".join(ibis.to_sql(rows, dialect="mysql").split()).split(" WHERE ", 1)[1]
    connection = _live_mysql_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 WHERE " + _with_cell(predicate, "bad"))
            assert cursor.fetchone() is not None, "the malformed cell must be counted"
            cursor.execute("SELECT 1 WHERE " + _with_cell(predicate, "20261301"))
            assert cursor.fetchone() is not None, "an impossible civil date must be counted"
            cursor.execute("SELECT 1 WHERE " + _with_cell(predicate, "20260701"))
            assert cursor.fetchone() is None, "a well-formed cell must not be counted"
    finally:
        connection.close()


@pytest.mark.runtime
def test_postgres_lenient_to_timestamp_is_a_documented_limitation() -> None:
    """PostgreSQL answers a malformed cell with a wrong non-NULL instant.

    ``TO_TIMESTAMP`` fills missing fields and ignores trailing input instead of
    raising, so the compiler cannot make PostgreSQL safe by adding it to
    ``NULL_ON_MALFORMED_ENGINES``: there is no NULL for the guard to count. This
    pins the documented limitation so a later change cannot silently claim the
    engine is covered.
    """
    if os.environ.get("MARIVO_POSTGRES_ANALYSIS_TEST") != "1":
        pytest.skip("opt-in PostgreSQL service")
    from tests.multisource_environment import postgres_analysis as pg

    os.environ.setdefault("MARIVO_TEST_POSTGRES_PASSWORD", pg.password())
    with pg.connection(admin=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_timestamp(%s, %s)", ("2026-07-01 15:59:00", "YYYYMMDD"))
            wrong = cursor.fetchone()[0]
            cursor.execute(
                "SELECT to_timestamp(%s, %s)",
                ("2026-07-01 15:59:00 not-a-time", "YYYY-MM-DD HH24:MI:SS"),
            )
            trailing = cursor.fetchone()[0]
        connection.rollback()
    assert wrong is not None and str(wrong).startswith("2026-01-07"), (
        "the documented leniency changed; revisit NULL_ON_MALFORMED_ENGINES"
    )
    assert trailing is not None, "trailing garbage is ignored, not rejected"
    assert "postgres" not in NULL_ON_MALFORMED_ENGINES
