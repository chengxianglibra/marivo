"""Report-independent reductions retain their scope across native time operations."""

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_get


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
def test_native_mixed_scope_calendar_and_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    data = SourceData(
        "id BIGINT NOT NULL, amount BIGINT, day DATE",
        "(1,100,DATE '2026-11-02'),(2,200,DATE '2026-11-03')",
        "id Int64, amount Nullable(Int64), day Date",
        [
            {"id": 1, "amount": 100, "day": date(2026, 11, 2)},
            {"id": 2, "amount": 200, "day": date(2026, 11, 3)},
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Data',default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'],default_time=ms.ref.time_dimension('sales.facts.day'))\n"
                "day=ms.time_dimension_column(name='day',entity=facts,column='day')\n"
                "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n",
            }
        )
        session = mv.session.get_or_create("independent-time", timezone="UTC")
        amount = catalog_get(session.catalog, "measure:sales.facts.amount", kind="measure")
        entity = catalog_get(session.catalog, "entity:sales.facts", kind="entity")
        timed, independent = amount.sum(), amount.sum(time=None)
        members = session.members(entity).count()
        scope = session.time_scope(start="2026-11-02", end="2026-11-05")
        axis = scope.by(mv.grain("day"))
        assert session.observe(
            {"all": independent}, where=session.time_scope(start="2030-01-01", end="2030-01-02")
        ).compute().to_pandas()["all"].tolist() == [300]
        rows = (
            session.observe({"ratio": timed / members, "all": independent}, by=(axis,))
            .compute()
            .to_pandas()
        )
        assert rows["ratio"].tolist() == [50, 100, 0]
        assert rows["all"].tolist() == [300, 300, 300]
        assert session.observe({"members": members}, by=(axis,)).compute().to_pandas()[
            "members"
        ].tolist() == [2, 2, 2]
        rules = [
            mv.all_history(),
            mv.since(start=scope.start),
            mv.to_date(grain=mv.grain("month")),
            mv.trailing_duration(duration=timedelta(days=2)),
            mv.trailing_periods(count=2),
        ]
        for index, rule in enumerate(rules):
            expected = [50, 150, 100] if index >= 3 else [50, 150, 150]
            result = session.observe(
                {"ratio": (timed / members).window(rule), "all": independent.window(rule)},
                by=(axis,),
            ).compute()
            assert result.to_pandas()["ratio"].tolist() == expected
            assert result.to_pandas()["all"].tolist() == [300, 300, 300]
            fact = result.schema.value_time_semantics["ratio"]
            assert [item.kind for item in fact.contributions] == ["clocked", "independent"]
