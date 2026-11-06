"""DATE controls select target versions by their source business-day start."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic.reader import SemanticProject


def _dimension(catalog: ms.SemanticCatalog, path: str) -> ms.Dimension:
    result = catalog.get(path)
    assert result.kind == "dimension"
    return result


def _measure(catalog: ms.SemanticCatalog, path: str) -> ms.Measure:
    result = catalog.get(path)
    assert result.kind == "measure"
    return result


def _relationship(catalog: ms.SemanticCatalog, path: str) -> ms.Relationship:
    result = catalog.get(path)
    assert result.kind == "relationship"
    return result


def _time_dimension(catalog: ms.SemanticCatalog, path: str) -> ms.TimeDimension:
    result = catalog.get(path)
    assert result.kind == "time_dimension"
    return result


@pytest.mark.parametrize("zone", ("Asia/Tokyo", "UTC+08:00", "UTC-05:00"))
def test_date_control_uses_snapshot_boundary_timezone(
    zone: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    aware = zone == "Asia/Tokyo"
    version = "snapshot"
    database = tmp_path / "date_versions.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            f"CREATE TABLE profiles(id BIGINT, day {'TIMESTAMPTZ' if aware else 'TIMESTAMP'}, ending DATE, category VARCHAR)"
        )
        connection.execute(
            "INSERT INTO profiles VALUES "
            "(1, TIMESTAMPTZ '2026-06-30 15:00:00+00', DATE '2026-07-01', 'old'), "
            "(1, TIMESTAMPTZ '2026-07-01 15:00:00+00', NULL, 'new')"
            if aware
            else "INSERT INTO profiles VALUES "
            "(1, TIMESTAMP '2026-07-01 20:00:00', DATE '2026-07-01', 'old'), "
            "(1, TIMESTAMP '2026-07-02 20:00:00', NULL, 'new')"
        )
        connection.execute(
            "CREATE TABLE facts(id BIGINT, profile_id BIGINT, fact_day DATE, amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO facts VALUES (100, 1, DATE '2026-07-02', 10)"
            if zone == "UTC-05:00"
            else "INSERT INTO facts VALUES (100, 1, DATE '2026-07-01', 10)"
        )
        native = connection.execute(
            "SELECT category FROM profiles WHERE CAST(timezone('Asia/Tokyo', day) AS DATE) = DATE '2026-07-01'"
            if aware
            else "SELECT category FROM profiles WHERE CAST(day AS DATE) = DATE '2026-07-01'"
        ).fetchall()
        print("NATIVE_ORACLE", native)
        assert native == [("old",)]
    versioning = (
        "ms.snapshot(partition_field=ms.ref.time_dimension('sales.profiles.day'), "
        f"timezone={zone!r})"
        if version == "snapshot"
        else "ms.validity(valid_from=ms.ref.time_dimension('sales.profiles.day'), "
        "valid_to=ms.ref.time_dimension('sales.profiles.ending'), "
        "interval='closed_open', open_end=(None,))"
    )
    semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database)!r}, wall_clock_timezone='UTC')\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\n"
                "ms.domain(name='sales', owner='Analytics', default=True)\n"
            ),
            "sales/models.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "warehouse=ms.ref.datasource('warehouse')\n"
                "profiles=ms.entity(name='profiles', datasource=warehouse, "
                f"source=md.table('profiles'), primary_key=['id'], versioning={versioning})\n"
                "day=ms.time_dimension_column(name='day', entity=profiles, column='day', "
                f"parse=ms.timestamp({'' if aware else 'timezone=' + repr(zone)}))\n"
                "ending=ms.time_dimension_column(name='ending', entity=profiles, column='ending', "
                f"parse=ms.date(timezone={zone!r}))\n"
                "category=ms.dimension_column(name='category', entity=profiles, column='category')\n"
                "profile_id=ms.dimension_column(name='id', entity=profiles, column='id')\n"
                "facts=ms.entity(name='facts', datasource=warehouse, source=md.table('facts'), "
                "primary_key=['id'])\n"
                "fact_day=ms.time_dimension_column(name='fact_day', entity=facts, column='fact_day', "
                "parse=ms.date(timezone='UTC'))\n"
                "amount=ms.measure_column(name='amount', entity=facts, column='amount', "
                "additivity=ms.additive_all())\n"
                "fact_profile_id=ms.dimension_column(name='profile_id', entity=facts, column='profile_id')\n"
                "profile=ms.relationship(name='profile', from_entity=facts, to_entity=profiles, "
                "keys=[ms.join_on(fact_profile_id, profile_id)])\n"
            ),
        }
    )
    monkeypatch.chdir(tmp_path)
    ms.load(workspace_dir=tmp_path)
    session = mv.session.get_or_create(f"date-version-{version}", timezone="UTC")
    fact_day = _time_dimension(session.catalog, "time_dimension:sales.facts.fact_day")
    category = _dimension(session.catalog, "dimension:sales.profiles.category")
    relationship = _relationship(session.catalog, "relationship:sales.profile")
    historical_category = category.via((relationship,)).at(fact_day)
    period = session.time_scope(start="2026-07-01", end="2026-07-03")
    result = session.observe(
        {"value": _measure(session.catalog, "measure:sales.facts.amount").sum(time=fact_day)},
        by=(period, historical_category),
    ).compute()
    data = result.to_pandas(axis_names={result.axes[-1].token: "category"})

    # July 1 00:00 UTC belongs to Tokyo July 1, whose partition is June 30 15:00Z.
    print("ACTUAL", data.to_dict(orient="records"))
    assert data["category"].tolist() == ["old"]
    assert data["value"].tolist() == [10]
