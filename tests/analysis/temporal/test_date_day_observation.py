"""Public DATE observations group identical facts before publishing UTC keys."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import sqlglot
import sqlglot.expressions as sge

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _datasource_model
from tests.analysis.temporal.test_calendar_native_keys import _maria
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value, frozen_coordinates
from tests.support.source_trace import SourceTrace


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("mysql", "mariadb"),
            ("trino", "iceberg"),
            ("clickhouse", "mergetree"),
        ]
    ),
)
@pytest.mark.parametrize("encoding", ["date", "compact", "dashed", "integer"])
def test_date_day_grouping_keeps_empty_null_and_category_domains(
    backend: str,
    profile: str,
    encoding: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    _maria(profile, monkeypatch)
    rows: list[dict[str, object]] = [
        {"id": 1, "point": date(2026, 10, 28), "amount": 10, "category": "A"},
        {"id": 2, "point": date(2026, 10, 28), "amount": 30, "category": "A"},
        {"id": 3, "point": date(2026, 10, 30), "amount": None, "category": "A"},
        {"id": 4, "point": date(2026, 10, 30), "amount": None, "category": "A"},
        {"id": 5, "point": date(2026, 10, 31), "amount": 100, "category": "A"},
        {"id": 6, "point": None, "amount": 999, "category": "A"},
        {"id": 7, "point": date(2026, 10, 28), "amount": 5, "category": "B"},
        {"id": 8, "point": date(2026, 10, 30), "amount": 20, "category": "B"},
    ]
    physical = (
        "DATE" if encoding == "date" else "BIGINT" if encoding == "integer" else "VARCHAR(10)"
    )
    clickhouse_type = (
        "Date" if encoding == "date" else "Int64" if encoding == "integer" else "String"
    )
    parser = (
        "ms.date(timezone='Asia/Shanghai')"
        if encoding == "date"
        else f"ms.strptime({'%Y-%m-%d' if encoding == 'dashed' else '%Y%m%d'!r}, timezone='Asia/Shanghai')"
    )
    for row in rows:
        point = row["point"]
        if isinstance(point, date) and encoding != "date":
            row["point"] = (
                int(point.strftime("%Y%m%d"))
                if encoding == "integer"
                else point.strftime("%Y-%m-%d" if encoding == "dashed" else "%Y%m%d")
            )
    data = SourceData(
        f"id BIGINT, point {physical}, amount BIGINT, category VARCHAR(10)",
        ",".join(
            f"({row['id']},"
            + (
                "NULL"
                if row["point"] is None
                else f"DATE '{row['point']}'"
                if encoding == "date"
                else str(row["point"])
                if encoding == "integer"
                else f"'{row['point']}'"
            )
            + f",{row['amount'] if row['amount'] is not None else 'NULL'},'{row['category']}')"
            for row in rows
        ),
        f"id Int64, point Nullable({clickhouse_type}), amount Nullable(Int64), category String",
        rows,
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Test', default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])\n"
                + f"point=ms.time_dimension_column(name='point', entity=facts, column='point', parse={parser}, is_default=True)\n"
                + "amount=ms.measure_column(name='amount', entity=facts, column='amount', additivity=ms.additive_all())\n"
                + "category=ms.dimension_column(name='category', entity=facts, column='category')\n",
            }
        )
        session = mv.session.get_or_create("late-date-keys", timezone="Asia/Shanghai")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        assert isinstance(amount, ms.Measure) and isinstance(category, ms.Dimension)
        days = session.time_scope(start="2026-10-28", end="2026-10-31").by(mv.grain("day"))
        metrics = {
            "sum": amount.sum(),
            "mean": amount.mean(),
            "count": amount.count(),
            "minimum": amount.min(),
        }
        if backend in ("duckdb", "postgres", "clickhouse"):
            metrics["quantile"] = amount.quantile(0)
        frame = session.observe(metrics, by=(days, category)).compute()
        result = frame.to_pandas().sort_values(["calendar:day", "category"]).reset_index(drop=True)
        assert result["calendar:day"].tolist() == [
            pd.Timestamp(day, tz="Asia/Shanghai")
            for day in ("2026-10-28", "2026-10-29", "2026-10-30")
            for _ in range(2)
        ]
        assert result.category.tolist() == ["A", "B"] * 3
        assert result["count"].tolist() == [2, 1, 0, 0, 0, 1]
        assert result["sum"].fillna(-1).tolist() == [40, 5, 0, 0, -1, 20]
        assert result["mean"].fillna(-1).tolist() == [20, 5, -1, -1, -1, 20]
        assert result["minimum"].fillna(-1).tolist() == [10, 5, -1, -1, -1, 20]
        if "quantile" in result:
            assert result["quantile"].fillna(-1).tolist() == [10, 5, -1, -1, -1, 20]
        assert (
            str(frozen_coordinates(frame)[0].rows["key.0"].dtype)
            == "timestamp[us, tz=UTC][pyarrow]"
        )
        grouped = [
            select
            for sql in source_trace.native_sql
            # SQLGlot cannot parse ClickHouse's curried quantile function.
            # Normalize only its spelling for this GROUP BY structure check.
            for select in sqlglot.parse_one(
                sql.replace("quantileExactInclusive(0.0)", "quantileExact"), read=backend
            ).find_all(sge.Select)
            if select.args.get("group") is not None
            and any(expression.find(sge.AggFunc) is not None for expression in select.expressions)
        ]
        assert grouped
        assert all(
            isinstance(select.expressions[0].unalias(), sge.Column)
            and select.expressions[0].unalias().name == "point"
            for select in grouped
        ), [select.sql() for select in grouped]
        saved = frame.persist()
        pd.testing.assert_frame_equal(saved.to_pandas(), frame.to_pandas())
        if backend in ("duckdb", "postgres", "clickhouse"):
            strict = session.observe(
                {"strict": amount.quantile(0, nulls="error")}, by=(days, category)
            )
            with pytest.raises(AnalysisError) as error:
                strict.compute()
            assert isinstance(error.value.__cause__, SemanticExpressionError)
            assert error.value.__cause__.expected == "non-NA source values"
        from marivo.analysis.compiler import source_time
        from marivo.analysis.compiler.calendar import reusable_date_day
        from marivo.analysis.dsl.temporal import exact_scope
        from marivo.analysis.materialization.native_calendar import output_calendar

        for start, end in (("2026-03-05", "2026-03-11"), ("2026-10-29", "2026-11-04")):
            gap = exact_scope(start, end, "America/Havana").by(mv.grain("day"))
            native, _, _ = output_calendar(gap._binding, case.session)
            # Simulate a different local timezone database qualifying a day
            # whose source-native midnight has actually shifted or repeated.
            with monkeypatch.context() as qualification:
                qualification.setattr(source_time, "endpoint_rewrite_safe", lambda *args: True)
                assert not reusable_date_day(native, "America/Havana", backend)
