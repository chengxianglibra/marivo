"""DATE scope predicates consume business-day starts without changing raw fields."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.data_types import ScalarDataType
from marivo.analysis.errors import AnalysisError
from marivo.semantic.reader import SemanticProject
from tests.support.source_trace import SourceTrace


def _project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[dict[str, str]], SemanticProject],
    source_timezone: str,
    *,
    primary_key: str = "id",
) -> None:
    database = tmp_path / "date_scopes.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE facts(id BIGINT, day DATE, clock TIMESTAMPTZ, amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO facts VALUES "
            "(1, DATE '2026-07-01', TIMESTAMPTZ '2026-06-30 15:00:00Z', 10), "
            "(2, DATE '2026-07-02', TIMESTAMPTZ '2026-07-01 15:00:00Z', 20), "
            "(3, NULL, NULL, 30)"
        )
    factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database)!r}, "
                f"wall_clock_timezone={source_timezone!r})\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Test', default=True)\n"
            ),
            "sales/models.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), "
                f"source=md.table('facts'), primary_key=[{primary_key!r}])\n"
                "day=ms.time_dimension_column(name='day', entity=facts, column='day')\n"
                "clock=ms.time_dimension_column(name='clock', entity=facts, column='clock')\n"
                "amount=ms.measure_column(name='amount', entity=facts, column='amount', "
                "additivity=ms.additive_all())\n"
            ),
        }
    )
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize(
    "source_timezone,scope_timezone,expected",
    [("Asia/Tokyo", "UTC", [2]), ("UTC", "Asia/Tokyo", [1])],
)
def test_date_within_uses_source_business_day_start_and_explicit_scope_timezone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
    source_timezone: str,
    scope_timezone: str,
    expected: list[int],
) -> None:
    _project(tmp_path, monkeypatch, semantic_project_factory, source_timezone)
    session = mv.session.get_or_create("date-within", timezone="America/New_York")
    day = session.catalog.get("time_dimension:sales.facts.day")
    assert isinstance(day, ms.TimeDimension)
    scope = session.time_scope(start="2026-07-01", end="2026-07-02", timezone=scope_timezone)
    members = session.members(session.catalog.get("entity:sales.facts"), where=day.within(scope))
    result = session.frame(members).compute()

    # Tokyo July 1 starts June 30 at 15:00Z; Tokyo July 2 starts July 1 at
    # 15:00Z. A UTC source day starts at 09:00 on the same date in Tokyo.
    assert result.to_pandas().iloc[:, 0].tolist() == expected
    facts = [fact for fact in result.schema.source_times if fact.axis == "sales.facts.day"]
    assert len(facts) == 1
    assert facts[0].kind == "civil_date"
    assert facts[0].timezone == source_timezone
    assert facts[0].consumption_timezone == scope_timezone
    assert len(source_trace.native_sql) == 1


def test_date_within_preserves_date_identity_coordinates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    _project(tmp_path, monkeypatch, semantic_project_factory, "Asia/Tokyo", primary_key="day")
    with duckdb.connect(str(tmp_path / "date_scopes.duckdb")) as connection:
        connection.execute("DELETE FROM facts WHERE day IS NULL")
    session = mv.session.get_or_create("date-identity-within", timezone="UTC")
    day = session.catalog.get("time_dimension:sales.facts.day")
    assert isinstance(day, ms.TimeDimension)
    scope = session.time_scope(start="2026-07-01", end="2026-07-02")
    selected = session.members(session.catalog.get("entity:sales.facts"), where=day.within(scope))
    result = session.frame(selected).compute()
    assert result.to_pandas().iloc[:, 0].tolist() == [date(2026, 7, 2)]
    assert result.schema.axes[0].keys[0].dtype == ScalarDataType("date")


def test_date_within_composition_preserves_raw_projection_and_exact_equality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    _project(tmp_path, monkeypatch, semantic_project_factory, "Asia/Tokyo")
    session = mv.session.get_or_create("composed-date-within", timezone="UTC")
    entity = session.catalog.get("entity:sales.facts")
    day = session.catalog.get("time_dimension:sales.facts.day")
    amount = session.catalog.get("measure:sales.facts.amount")
    assert isinstance(day, ms.TimeDimension) and isinstance(amount, ms.Measure)
    scope = session.time_scope(start="2026-07-01", end="2026-07-02")
    within = day.within(scope)
    second_start = session.instant("2026-07-02", timezone="Asia/Tokyo")
    selected = session.members(entity, where=within & (day == second_start))
    result = session.read(selected, {"day": day}).compute()
    data = result.to_pandas()
    assert data.iloc[:, 0].tolist() == [2]
    assert data["day"].tolist() == [date(2026, 7, 2)]
    assert result.schema.values["day"].dtype == ScalarDataType("date")

    equality = session.members(entity, where=day == date(2026, 7, 1))
    # A Python date now means report midnight (UTC), not a source DATE label.
    # Neither Tokyo day starts at UTC midnight; an explicit source anchor matches.
    assert session.frame(equality).compute().to_pandas().iloc[:, 0].tolist() == []
    first_start = session.instant("2026-07-01", timezone="Asia/Tokyo")
    source_equality = session.members(entity, where=day == first_start)
    assert session.frame(source_equality).compute().to_pandas().iloc[:, 0].tolist() == [1]
    either = session.members(entity, where=within | (day == first_start))
    assert session.frame(either).compute().to_pandas().iloc[:, 0].tolist() == [1, 2]
    outside = session.members(entity, where=~within)
    assert session.frame(outside).compute().to_pandas().iloc[:, 0].tolist() == [1]
    observed = session.observe({"total": amount.sum()}, where=within).compute()
    assert observed.to_pandas()["total"].tolist() == [20]


def test_date_within_requires_whole_days_in_scope_timezone_but_timestamp_does_not(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    _project(tmp_path, monkeypatch, semantic_project_factory, "Asia/Tokyo")
    session = mv.session.get_or_create("date-scope-grain", timezone="UTC")
    entity = session.catalog.get("entity:sales.facts")
    day = session.catalog.get("time_dimension:sales.facts.day")
    clock = session.catalog.get("time_dimension:sales.facts.clock")
    assert isinstance(day, ms.TimeDimension) and isinstance(clock, ms.TimeDimension)
    scope = session.time_scope(start="2026-07-01T14:00Z", end="2026-07-01T16:00Z")
    selected = session.members(entity, where=day.within(scope))
    with pytest.raises(AnalysisError, match="complete-day scope") as caught:
        session.frame(selected).compute()
    assert "source timezone Asia/Tokyo" in str(caught.value.received)
    assert "scope timezone UTC" in str(caught.value.received)
    absolute = session.members(entity, where=clock.within(scope))
    assert session.frame(absolute).compute().to_pandas().iloc[:, 0].tolist() == [2]


def test_date_within_uses_backend_native_start_at_a_midnight_fold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    _project(tmp_path, monkeypatch, semantic_project_factory, "America/Havana")
    with duckdb.connect(str(tmp_path / "date_scopes.duckdb")) as connection:
        connection.execute("DELETE FROM facts")
        connection.execute(
            "INSERT INTO facts(id, day, amount) VALUES "
            "(1, DATE '2025-11-01', 10), (2, DATE '2025-11-02', 20), "
            "(3, DATE '2025-11-03', 30), (4, NULL, 40)"
        )
        oracle = connection.execute(
            "SELECT id FROM facts "
            "WHERE timezone('America/Havana', CAST(day AS TIMESTAMP)) "
            ">= TIMESTAMPTZ '2025-11-02 04:30:00Z' "
            "AND timezone('America/Havana', CAST(day AS TIMESTAMP)) "
            "< TIMESTAMPTZ '2025-11-03 04:30:00Z' ORDER BY id"
        ).fetchall()
    # DuckDB chooses the later Havana midnight (05:00Z). Choosing the earlier
    # Python fold branch (04:00Z) would incorrectly exclude November 2.
    assert oracle == [(2,)]
    session = mv.session.get_or_create("native-date-start", timezone="UTC")
    day = session.catalog.get("time_dimension:sales.facts.day")
    assert isinstance(day, ms.TimeDimension)
    scope = session.time_scope(start="2025-11-02", end="2025-11-03", timezone="-04:30")
    selected = session.members(session.catalog.get("entity:sales.facts"), where=day.within(scope))
    assert session.frame(selected).compute().to_pandas().iloc[:, 0].tolist() == [2]


def test_date_within_through_versioned_relationship_retains_scope_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    database = tmp_path / "profile_scopes.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE facts(id BIGINT, profile_id BIGINT, amount BIGINT)")
        connection.execute("INSERT INTO facts VALUES (1, 10, 1), (2, 20, 2)")
        connection.execute("CREATE TABLE profiles(id BIGINT, partition DATE, day DATE)")
        connection.execute(
            "INSERT INTO profiles VALUES "
            "(10, DATE '2026-07-01', DATE '2026-07-01'), "
            "(10, DATE '2026-07-02', DATE '2026-07-02'), "
            "(20, DATE '2026-07-01', DATE '2026-07-02'), "
            "(20, DATE '2026-07-02', DATE '2026-07-01')"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database)!r}, "
                "wall_clock_timezone='Asia/Tokyo')\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Test', default=True)\n"
            ),
            "sales/models.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "warehouse=ms.ref.datasource('warehouse')\n"
                "profiles=ms.entity(name='profiles', datasource=warehouse, "
                "source=md.table('profiles'), primary_key=['id'], "
                "versioning=ms.snapshot(partition_field=ms.ref.time_dimension('sales.profiles.partition'), "
                "timezone='Asia/Tokyo'))\n"
                "partition=ms.time_dimension_column(name='partition', entity=profiles, column='partition')\n"
                "day=ms.time_dimension_column(name='day', entity=profiles, column='day')\n"
                "profile_id=ms.dimension_column(name='id', entity=profiles, column='id')\n"
                "facts=ms.entity(name='facts', datasource=warehouse, source=md.table('facts'), "
                "primary_key=['id'])\n"
                "fact_profile_id=ms.dimension_column(name='profile_id', entity=facts, column='profile_id')\n"
                "profile=ms.relationship(name='profile', from_entity=facts, to_entity=profiles, "
                "keys=[ms.join_on(fact_profile_id, profile_id)])\n"
                "amount=ms.measure_column(name='amount', entity=facts, column='amount', "
                "additivity=ms.additive_all())\n"
            ),
        }
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("versioned-date-scope", timezone="UTC")
    day = session.catalog.get("time_dimension:sales.profiles.day")
    relationship = session.catalog.get("relationship:sales.profile")
    amount = session.catalog.get("measure:sales.facts.amount")
    assert isinstance(day, ms.TimeDimension) and isinstance(amount, ms.Measure)
    assert isinstance(relationship, ms.Relationship)
    historical_day = day.via((relationship,)).at(session.instant("2026-06-30T16:00Z"))
    scope = session.time_scope(start="2026-07-01", end="2026-07-02")
    selected = session.members(
        session.catalog.get("entity:sales.facts"), where=historical_day.within(scope)
    )
    result = session.read(selected, {"day": historical_day}).compute()
    data = result.to_pandas()
    assert data.iloc[:, 0].tolist() == [2]
    assert data["day"].tolist() == [date(2026, 7, 2)]
    observed = session.observe(
        {"total": amount.sum()}, where=historical_day.within(scope)
    ).compute()
    assert observed.to_pandas()["total"].tolist() == [2]
