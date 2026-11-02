"""DATE controls select target versions by their source business-day start."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.semantic.reader import SemanticProject


@pytest.mark.parametrize("version", ("validity",))
def test_aware_control_respects_native_date_midnight(
    version: Literal["snapshot", "validity"],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    database = tmp_path / "date_versions.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE profiles(id BIGINT, day DATE, ending DATE, category VARCHAR)"
        )
        connection.execute(
            "INSERT INTO profiles VALUES "
            "(1, DATE '2026-10-31', DATE '2026-11-01', 'old'), "
            "(1, DATE '2026-11-01', NULL, 'new')"
        )
        connection.execute(
            "CREATE TABLE facts(id BIGINT, profile_id BIGINT, fact_day TIMESTAMPTZ, amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO facts VALUES (100, 1, TIMESTAMPTZ '2026-11-01 04:30:00+00', 10)"
        )
        oracle = connection.execute(
            "SELECT category FROM profiles WHERE timezone('America/Havana', CAST(day AS TIMESTAMP)) <= TIMESTAMPTZ '2026-11-01 04:30:00+00' AND (ending IS NULL OR timezone('America/Havana', CAST(ending AS TIMESTAMP)) > TIMESTAMPTZ '2026-11-01 04:30:00+00')"
        ).fetchall()
        print("NATIVE_ORACLE", oracle)
        assert oracle == [("old",)]
    versioning = (
        "ms.snapshot(partition_field=ms.ref.time_dimension('sales.profiles.day'), "
        "timezone='America/Havana')"
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
                "parse=ms.date(timezone='America/Havana'))\n"
                "ending=ms.time_dimension_column(name='ending', entity=profiles, column='ending', "
                "parse=ms.date(timezone='America/Havana'))\n"
                "category=ms.dimension_column(name='category', entity=profiles, column='category')\n"
                "profile_id=ms.dimension_column(name='id', entity=profiles, column='id')\n"
                "facts=ms.entity(name='facts', datasource=warehouse, source=md.table('facts'), "
                "primary_key=['id'])\n"
                "fact_day=ms.time_dimension_column(name='fact_day', entity=facts, column='fact_day', "
                "parse=ms.timestamp())\n"
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
    fact_day = session.catalog.get("time_dimension:sales.facts.fact_day")
    category = session.catalog.get("dimension:sales.profiles.category")
    relationship = session.catalog.get("relationship:sales.profile")
    historical_category = category.via((relationship,)).at(fact_day)
    period = session.time_scope(start="2026-11-01", end="2026-11-02")
    result = session.observe(
        {"value": session.catalog.get("measure:sales.facts.amount").sum(time=fact_day)},
        by=(period, historical_category),
    ).compute()
    data = result.to_pandas(axis_names={result.axes[-1].token: "category"})

    # Native Havana Nov 1 midnight selects the later fold at 05:00Z, after the fact.
    fixed_category = category.via((relationship,)).at(session.instant("2026-11-01T04:30:00+00:00"))
    fixed = session.observe(
        {"value": session.catalog.get("measure:sales.facts.amount").sum(time=fact_day)},
        by=(period, fixed_category),
    ).compute()
    fixed_data = fixed.to_pandas(axis_names={fixed.axes[-1].token: "category"})
    print("FIXED_ANCHOR", fixed_data.to_dict(orient="records"))
    assert fixed_data["category"].tolist() == ["old"]
    print("ACTUAL", data.to_dict(orient="records"))
    assert data["category"].tolist() == ["old"]
    assert data["value"].tolist() == [10]
