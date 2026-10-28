"""Ordinary Metric preview over native SQL providers and bounded source scopes."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.errors import DatasourceAuthoringError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.execution_logs import execution_records

_DATA = SourceData(
    columns="id BIGINT, amount BIGINT, day DATE, precise DECIMAL(20,2), wide DECIMAL(20,2)",
    values="(1,2,DATE '2026-07-10',2.10,0),(2,NULL,DATE '2026-07-10',NULL,NULL),(3,8,DATE '2026-07-11',8.90,999999999999999999.00)",
    clickhouse_columns="id Int64, amount Nullable(Int64), day Date, precise Nullable(Decimal(20,2)), wide Nullable(Decimal(20,2))",
    rows=[
        {
            "id": 1,
            "amount": 2,
            "day": date(2026, 7, 10),
            "precise": Decimal("2.10"),
            "wide": Decimal("0"),
        },
        {"id": 2, "amount": None, "day": date(2026, 7, 10), "precise": None, "wide": None},
        {
            "id": 3,
            "amount": 8,
            "day": date(2026, 7, 11),
            "precise": Decimal("8.90"),
            "wide": Decimal("999999999999999999.00"),
        },
    ],
)


@contextmanager
def _partitioned_source(backend: str, source: TableSourceIR) -> Iterator[TableSourceIR]:
    """Own one native partitioned table while reusing the ready service fixture."""
    name = source.table + "_partitioned"
    if backend == "clickhouse":
        from tests.datasource.environment import clickhouse_analysis as ch

        identity = f"{source.database}.{name}"
        with ch.connection(admin=True) as admin:
            admin.command(
                f"CREATE TABLE {identity} ENGINE=MergeTree PARTITION BY day ORDER BY id "
                f"AS SELECT * FROM {source.database}.{source.table}"
            )
            try:
                yield TableSourceIR(name, database=source.database)
            finally:
                admin.command(f"DROP TABLE IF EXISTS {identity}")
    else:
        from tests.datasource.environment import trino_analysis as trino

        identity = f"iceberg.analysis.{name}"
        with trino.connection(admin=True) as admin:
            cursor = admin.cursor()
            try:
                cursor.execute(
                    f"CREATE TABLE {identity} WITH (partitioning=ARRAY['day']) "
                    f"AS SELECT * FROM iceberg.analysis.{source.table}"
                ).fetchall()
                yield TableSourceIR(name, database=source.database)
            finally:
                cursor.execute(f"DROP TABLE IF EXISTS {identity}").fetchall()
                cursor.close()


@pytest.mark.parametrize(
    "backend", source_params(("duckdb", "postgres", "mysql", "trino", "clickhouse"))
)
def test_native_metric_preview(
    backend: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    profile = {"trino": "iceberg", "clickhouse": "mergetree", "mysql": "innodb-table"}.get(
        backend, "table"
    )
    with source_case(backend, profile, tmp_path, monkeypatch, data=_DATA) as case:
        assert isinstance(case.source, TableSourceIR)
        arguments = {
            **case.session.datasource.fields,
            **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
        }
        if "user" in arguments:
            monkeypatch.setenv("MARIVO_METRIC_PREVIEW_READER", str(arguments.pop("user")))
            arguments["user_env"] = "MARIVO_METRIC_PREVIEW_READER"
        metrics = {
            "total": "amount.sum()",
            "average": "amount.mean()",
            "minimum": "amount.min()",
            "maximum": "amount.max()",
            "nonnull": "amount.count()",
            "distinct": "amount.count_distinct()",
            "rows": "facts.count()",
            "ratio": "amount.sum() / facts.count()",
            "middle": "amount.median()",
            "row_middle": "weight.median()",
            "quarter": "amount.quantile(0.25)",
            "weighted": "amount.weighted_mean(weight=weight)",
            "decimal_middle": "precise.median()",
            "decimal_quarter": "precise.quantile(0.25)",
            "tiny_quantile": "wide.quantile(1e-20)",
            "strict": "amount.sum(nulls='error')",
            "approximate": "amount.approx_median()",
        }
        definitions = {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.{backend}(name='warehouse',"
            + ",".join(f"{key}={value!r}" for key, value in arguments.items())
            + ")\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Data',default=True)\n",
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"facts = ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}))\n"
            + "amount = ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n"
            + "weight = ms.measure_column(name='weight',entity=facts,column='id',additivity=ms.additive_all())\n"
            + "precise = ms.measure_column(name='precise',entity=facts,column='precise',additivity=ms.additive_all())\n"
            + "wide = ms.measure_column(name='wide',entity=facts,column='wide',additivity=ms.additive_all())\n"
            + "\n".join(
                f"@ms.metric()\ndef {name}():\n    return {expression}\n"
                for name, expression in metrics.items()
            ),
        }
        project = semantic_project_factory(definitions)
        catalog = SemanticCatalog(project)
        names = [name for name in metrics if name not in {"strict", "approximate"}]
        results = catalog.preview_many(
            [catalog.get(f"metric:sales.{name}") for name in names],
            scope=md.unpruned(max_rows=10, timeout_seconds=30),
        ).results
        expected = [
            10,
            5,
            2,
            8,
            2,
            2,
            3,
            10 / 3,
            5,
            2,
            3.5,
            6.5,
            Decimal("5.50"),
            Decimal("3.80"),
            Decimal("0.01"),
        ]
        for result, value in zip(results, expected, strict=True):
            assert result.rows[0]["value"] == pytest.approx(value)
            assert result.coverage.entity_rows == (("sales.facts", 3),)
            assert result.coverage.entity_row_limits == (("sales.facts", 10),)
            assert result.coverage.scope_exhaustion == "exhaustive"
            assert result.execution_limits is not None
            assert result.execution_limits.backend == backend
            assert result.execution_limits.timeout_enforced == (backend != "duckdb")
            assert result.execution_limits.scan_rows is None
            assert result.execution_limits.scan_bytes is None
        if backend == "clickhouse":
            statements = [
                str(record["sql"]).lower()
                for record in execution_records(catalog.workspace_dir)
                if record.get("purpose") == "semantic.preview" and "sql" in record
            ]
            assert statements
            assert not any("uniq(" in sql for sql in statements)
            assert any("distinct" in sql for sql in statements)
        total = catalog.get("metric:sales.total")
        for start, end, expected_value in (
            ("2026-07-10", "2026-07-11", 2),
            ("2026-07-11", "2026-07-12", 8),
            ("2026-07-12", "2026-07-13", 0),
        ):
            result = catalog.preview(
                total,
                scope=md.time_range("day", start=start, end=end, max_rows=10, timeout_seconds=30),
            )
            assert result.rows == ({"value": expected_value},)
        with pytest.raises(SemanticExpressionError, match="non-NA"):
            catalog.preview(
                catalog.get("metric:sales.strict"),
                scope=md.unpruned(max_rows=10, timeout_seconds=30),
            )
        if backend in {"postgres", "mysql"}:
            with pytest.raises(SemanticExpressionError, match="declared quantile algorithm"):
                catalog.preview(
                    catalog.get("metric:sales.approximate"),
                    scope=md.unpruned(max_rows=10, timeout_seconds=30),
                )
        else:
            approximate = catalog.preview(
                catalog.get("metric:sales.approximate"),
                scope=md.unpruned(max_rows=10, timeout_seconds=30),
            )
            assert 2 <= approximate.rows[0]["value"] <= 8
        for name in ("middle", "row_middle"):
            empty_middle = catalog.preview(
                catalog.get(f"metric:sales.{name}"),
                scope=md.time_range(
                    "day", start="2026-07-12", end="2026-07-13", max_rows=10, timeout_seconds=30
                ),
            )
            assert empty_middle.rows == ({"value": None},)
            assert empty_middle.coverage.entity_rows == (("sales.facts", 0),)
        if backend in {"trino", "clickhouse"}:
            with _partitioned_source(backend, case.source) as partitioned:
                definitions["sales/models.py"] = definitions["sales/models.py"].replace(
                    case.source.table, partitioned.table
                )
                # SELECT-only fixture identities lack optional partition metadata privileges.
                # Their unavailable metadata retains the explicit broad-read route.
                restricted = SemanticCatalog(semantic_project_factory(definitions))
                assert restricted.preview(
                    restricted.get("metric:sales.total"),
                    scope=md.unpruned(max_rows=10, timeout_seconds=30),
                ).rows == ({"value": 10},)
                monkeypatch.setenv("MARIVO_METRIC_PREVIEW_METADATA_USER", "qualifier")
                definitions["datasources/warehouse.py"] = definitions[
                    "datasources/warehouse.py"
                ].replace("MARIVO_METRIC_PREVIEW_READER", "MARIVO_METRIC_PREVIEW_METADATA_USER")
                partitioned_catalog = SemanticCatalog(semantic_project_factory(definitions))
                total = partitioned_catalog.get("metric:sales.total")
                inspection = md.inspect(
                    ms.ref.datasource("warehouse"),
                    partitioned,
                    workspace_dir=partitioned_catalog.workspace_dir,
                )
                assert inspection.partitioning.value_source is not None, "\n".join(
                    inspection.warnings
                )
                with pytest.raises(DatasourceAuthoringError) as caught:
                    partitioned_catalog.preview(
                        total, scope=md.unpruned(max_rows=10, timeout_seconds=30)
                    )
                assert caught.value.effect_observed.query_executed is False
                for scope in (
                    md.partition({"day": "2026-07-10"}, max_rows=10, timeout_seconds=30),
                    md.time_range(
                        "day", start="2026-07-10", end="2026-07-11", max_rows=10, timeout_seconds=30
                    ),
                ):
                    result = partitioned_catalog.preview(total, scope=scope)
                    assert result.rows == ({"value": 2},)
                    assert result.coverage.entity_rows == (("sales.facts", 2),)

                original = f"md.table({partitioned.table!r},database={partitioned.database!r})"
                projected = (
                    original[:-1]
                    + ",columns={'id':'id','amount':'amount','day_alias':'day','precise':'precise','wide':'wide'})"
                )
                definitions["sales/models.py"] = definitions["sales/models.py"].replace(
                    original, projected
                )
                projected_catalog = SemanticCatalog(semantic_project_factory(definitions))
                result = projected_catalog.preview(
                    projected_catalog.get("metric:sales.total"),
                    scope=md.partition(
                        {"day_alias": "2026-07-10"}, max_rows=10, timeout_seconds=30
                    ),
                )
                assert result.rows == ({"value": 2},)
