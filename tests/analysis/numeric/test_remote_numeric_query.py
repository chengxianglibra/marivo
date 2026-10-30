"""Public remote Query execution and native pandas numerical handoff."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

import ibis.expr.types as ir
import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import Case, SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value

CASES = [
    ("duckdb", "table"),
    ("postgres", "table"),
    ("mysql", "innodb-table"),
    ("trino", "iceberg"),
    ("clickhouse", "mergetree"),
]
SOURCE_CASES = [*CASES, ("trino", "non-iceberg"), ("clickhouse", "distributed")]
DATA = SourceData(
    "id BIGINT, tenant VARCHAR(10), category VARCHAR(10), amount DECIMAL(18,2), big BIGINT, weight BIGINT, day DATE",
    "(1,'a','x',1.25,9007199254740993,1,DATE '2026-07-01'),"
    "(2,'b','x',2.75,NULL,3,DATE '2026-07-01'),"
    "(3,'a',NULL,NULL,NULL,100,DATE '2026-07-02'),"
    "(4,'b',NULL,NULL,NULL,NULL,DATE '2026-07-02')",
    "id Int64, tenant String, category Nullable(String), amount Nullable(Decimal(18,2)), big Nullable(Int64), weight Nullable(Int64), day Date",
    [
        {
            "id": identity,
            "tenant": tenant,
            "category": category,
            "amount": amount,
            "big": big,
            "weight": weight,
            "day": date(2026, 7, day),
        }
        for identity, tenant, category, amount, big, weight, day in (
            (1, "a", "x", Decimal("1.25"), 9007199254740993, 1, 1),
            (2, "b", "x", Decimal("2.75"), None, 3, 1),
            (3, "a", None, None, None, 100, 2),
            (4, "b", None, None, None, None, 2),
        )
    ],
)


def _datasource_model(
    backend: str,
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    arguments = {
        **case.session.datasource.fields,
        **{key + "_env": value for key, value in case.session.datasource.env_refs.items()},
    }
    if "user" in arguments:
        monkeypatch.setenv("MARIVO_NUMERIC_QUERY_READER", str(arguments.pop("user")))
        arguments["user_env"] = "MARIVO_NUMERIC_QUERY_READER"
    arguments_text = ",".join(f"{key}={value!r}" for key, value in arguments.items())
    return "import marivo.datasource as md\n" + f"md.{backend}(name='warehouse',{arguments_text})\n"


def _project(
    backend: str,
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[dict[str, str]], SemanticProject],
) -> None:
    assert isinstance(case.source, TableSourceIR)
    factory(
        {
            "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Data',default=True)\n",
            "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['tenant','id'])\n"
            + "tenant=ms.dimension_column(name='tenant',entity=facts,column='tenant')\n"
            + "category=ms.dimension_column(name='category',entity=facts,column='category')\n"
            + "day=ms.time_dimension_column(name='day',entity=facts,column='day',is_default=True)\n"
            + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n"
            + "big=ms.measure_column(name='big',entity=facts,column='big',additivity=ms.additive_all())\n"
            + "weight=ms.measure_column(name='weight',entity=facts,column='weight',additivity=ms.additive_all())\n"
            + "a=amount\nb=big\nw=weight\n"
            + "@ms.metric()\ndef total():\n    return a.sum()\n"
            + "@ms.metric()\ndef minimum():\n    return a.min()\n"
            + "@ms.metric()\ndef maximum():\n    return a.max()\n"
            + "@ms.metric()\ndef count():\n    return a.count()\n"
            + "@ms.metric()\ndef average():\n    return a.mean()\n"
            + "@ms.metric()\ndef weighted():\n    return a.weighted_mean(weight=w)\n"
            + "@ms.metric()\ndef distinct():\n    return a.count_distinct()\n"
            + "@ms.metric()\ndef quantile():\n    return a.quantile(q=0.5)\n"
            + "@ms.metric()\ndef empty():\n    return a.sum().where(a>100)\n"
            + "@ms.metric()\ndef zero_weight():\n    return a.weighted_mean(weight=w-w)\n",
        }
    )


@pytest.mark.parametrize("backend,profile", source_params([("postgres", "table")]))
def test_postgres_sparse_calendar_keeps_window_contributions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    data = SourceData(
        DATA.columns,
        "(1,'a','selected',10,1,1,DATE '2020-12-22'),"
        "(2,'a','gap',100,2,1,DATE '2020-12-29'),"
        "(3,'a','selected',20,3,1,DATE '2021-01-05')",
        DATA.clickhouse_columns,
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("sparse_remote", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        calendar = (
            session.time_scope(start="2019-12-23", end="2020-01-06")
            .by(mv.grain("week"))
            .shift(mv.offset(years=1))
        )
        ordinary = (
            session.observe({"total": amount.sum()}, by=(calendar, category)).compute().to_pandas()
        )
        assert ordinary["calendar:week"].tolist() == ["2020-12-21", "2021-01-04"]
        assert ordinary["sales.facts.category"].tolist() == ["selected", "selected"]
        assert ordinary["total"].tolist() == [Decimal(10), Decimal(20)]
        windows = (
            session.observe(
                {"total": amount.sum().window(mv.trailing_periods(count=2))}, by=(calendar,)
            )
            .compute()
            .to_pandas()
        )
        assert windows["total"].tolist() == [Decimal(10), Decimal(120)]


@pytest.mark.parametrize("backend,profile", source_params([("mysql", "innodb-table")]))
def test_mysql_direct_physical_utc_time_preserves_instants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    data = SourceData(
        "id BIGINT, happened TIMESTAMP(6)",
        "(1,'2026-11-01 05:30:00.123456'),(2,'2026-11-01 06:30:00.654321'),(3,NULL)",
        "id Int64, happened Nullable(DateTime64(6, 'UTC'))",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(backend, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Data',default=True)\n",
                "sales/facts.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
                + "happened=ms.time_dimension_column(name='happened',entity=facts,column='happened',parse=ms.timestamp(),is_default=True)\n",
            }
        )
        table = case.session.bind(case.source, source_identity="utc-native-oracle").relation
        assert table.happened.type().timezone == "UTC"
        native = case.session._backend.execute(table.order_by("id"))
        assert isinstance(native, pd.DataFrame)
        session = mv.session.get_or_create("mysql_utc_read", timezone="America/New_York")
        members = session.members(session.catalog.get("entity:sales.facts"))
        artifact = session.read(
            members,
            {"happened": catalog_value(session.catalog, "sales.facts.happened", kind="field")},
        ).persist()
        returned = artifact.to_pandas()["happened"]
        assert returned.isna().tolist() == native.happened.isna().tolist()
        assert returned.dropna().tolist() == [
            datetime(2026, 11, 1, 5, 30, 0, 123456, tzinfo=timezone.utc),
            datetime(2026, 11, 1, 6, 30, 0, 654321, tzinfo=timezone.utc),
        ]
        assert (
            returned.dropna().tolist()
            == pd.to_datetime(native.happened.dropna(), utc=True).tolist()
        )
        assert artifact.to_pandas().equals(
            session.persisted_frame(artifact.artifact_id).to_pandas()
        )


@pytest.mark.parametrize("backend,profile", source_params([("clickhouse", "mergetree")]))
def test_clickhouse_dst_fold_preserves_instants_and_half_open_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    import ibis

    points = (
        (1, datetime(2026, 11, 1, 5, 0, tzinfo=timezone.utc), 1),
        (2, datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc), 10),
        (3, datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), 20),
        (4, datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc), 100),
    )
    data = SourceData(
        "id BIGINT, happened TIMESTAMP, amount BIGINT",
        "",
        "id Int64, happened DateTime64(6, 'UTC'), amount Int64",
        [
            {"id": identity, "happened": instant, "amount": amount}
            for identity, instant, amount in points
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
                + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key=['id'])\n"
                + "happened=ms.time_dimension_column(name='happened',entity=facts,column='happened',parse=ms.timestamp(),is_default=True)\n"
                + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n",
            }
        )
        table = case.session.bind(case.source, source_identity="dst-native-oracle").relation
        assert table.happened.type().timezone == "UTC"
        native = case.session._backend.execute(
            table.filter(
                table.happened >= ibis.timestamp(points[0][1]),
                table.happened < ibis.timestamp(points[-1][1]),
            ).aggregate(total=table.amount.sum(), count=table.amount.count())
        )
        assert isinstance(native, pd.DataFrame)
        assert native.total.tolist() == [31]
        assert native["count"].tolist() == [3]
        session = mv.session.get_or_create("clickhouse_dst", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        happened = catalog_value(session.catalog, "sales.facts.happened", kind="field")
        scope = session.time_scope(
            start="2026-11-01T01:00:00-04:00",
            end="2026-11-01T02:00:00-05:00",
            timezone="America/New_York",
        )
        artifact = session.observe(
            {"total": amount.sum(), "count": amount.count()}, by=(scope,)
        ).persist()
        scalar = artifact.to_pandas()
        assert scalar["total"].tolist() == native.total.tolist()
        assert scalar["count"].tolist() == native["count"].tolist()
        fine = session.observe({"total": amount.sum()}, by=(scope, happened)).compute().to_pandas()
        assert fine[fine.attrs["axes"][1]["name"].rsplit(".", 1)[-1]].tolist() == [
            point[1] for point in points[:-1]
        ]
        assert fine["total"].tolist() == [1, 10, 20]
        assert artifact.to_pandas().equals(
            session.persisted_frame(artifact.artifact_id).to_pandas()
        )


@pytest.mark.parametrize("backend,profile", source_params(SOURCE_CASES))
def test_public_native_numeric_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, DATA) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote_numeric", timezone="UTC")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        query = session.observe(
            {
                name: catalog_value(session.catalog, "sales." + name, kind="metric")
                for name in ("total", "minimum", "maximum", "count")
            },
            by=(category,),
        )
        artifact = query.persist()
        frame = artifact.to_pandas()
        nonnull = frame.loc[frame[frame.attrs["axes"][0]["name"].rsplit(".", 1)[-1]].notna()]
        assert nonnull["total"].tolist() == [Decimal("4.00")]
        assert nonnull["minimum"].tolist() == [Decimal("1.25")]
        assert nonnull["maximum"].tolist() == [Decimal("2.75")]
        assert nonnull["count"].tolist() == [2]
        assert len(frame) == 2
        assert (
            frame.loc[frame[frame.attrs["axes"][0]["name"].rsplit(".", 1)[-1]].isna(), "total"]
            .isna()
            .all()
        )
        assert frame.equals(session.persisted_frame(artifact.artifact_id).to_pandas())
        members = session.members(session.catalog.get("entity:sales.facts"))
        direct_query = session.read(
            members, {"big": catalog_value(session.catalog, "sales.facts.big", kind="field")}
        )
        direct = direct_query.compute()
        returned = direct.to_pandas()["big"]
        assert returned.isna().sum() == 3
        assert pd.notna(returned.iloc[0])
        if backend == "duckdb":
            assert returned.iloc[0] == 9007199254740993
            assert pd.api.types.is_integer_dtype(returned.dtype)


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_public_native_numeric_methods(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, DATA) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote_methods", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="field")
        query = session.observe(
            {
                name: catalog_value(session.catalog, "sales." + name, kind="metric")
                for name in ("average", "weighted", "distinct")
            }
            | (
                {"q": (amount / 1).quantile(0.5)}
                if backend == "clickhouse"
                else {"q": session.catalog.get("metric:sales.quantile")}
                if backend not in ("mysql", "trino")
                else {}
            )
            | {
                "row_zero": (amount / 0).mean(),
                "integer_zero": (weight / 0).mean(),
                "floating_zero": ((amount / 1) / 0.0).mean(),
                "empty_ratio": (amount / 0).mean().where(amount > 100),
                "row_ratio": (amount / 2).mean(),
                "metric_zero": amount.sum() / 0,
                "zero_over_zero": (amount.sum() - amount.sum()) / 0,
                "zero_weight": amount.weighted_mean(weight=weight - weight),
                "cancelled_weight": amount.weighted_mean(weight=weight - 2),
            }
        )
        from marivo.datasource.adapters import CompiledRead, SourceSession

        statements: list[str] = []
        original_execute = SourceSession.execute

        def record(owner: SourceSession, issued: CompiledRead) -> pd.DataFrame:
            statements.append(issued.sql)
            return original_execute(owner, issued)

        monkeypatch.setattr(SourceSession, "execute", record)
        frame = query.compute().to_pandas()
        assert float(frame["average"].iloc[0]) == pytest.approx(2.0)
        assert frame["row_ratio"].tolist() == [1.0]
        # Only the two non-NULL value/weight pairs contribute: (1.25 + 2.75*3)/4.
        assert frame["weighted"].tolist() == [2.375]
        if backend == "clickhouse":
            assert "uniqExact(" in "\n".join(statements)
            assert "uniq(" not in "\n".join(statements)
        assert frame["distinct"].tolist() == [2]
        for name in (
            "row_zero",
            "integer_zero",
            "floating_zero",
            "empty_ratio",
            "metric_zero",
            "zero_over_zero",
            "zero_weight",
            "cancelled_weight",
        ):
            assert frame[name].isna().tolist() == [True]
        if backend not in ("mysql", "trino"):
            assert float(frame["q"].iloc[0]) == 2.0


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_public_native_quantile_algorithms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    from dataclasses import replace

    from marivo.analysis.errors import AnalysisStageUnavailableError
    from marivo.datasource.adapters import CompiledRead, SourceSession

    data = replace(
        DATA,
        values=DATA.values.replace("1.25", "0.00").replace("2.75", "10.00"),
        rows=[
            dict(
                row,
                amount=Decimal("0")
                if row["id"] == 1
                else Decimal("10")
                if row["id"] == 2
                else None,
            )
            for row in DATA.rows
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native_quantiles", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        statements: list[str] = []
        sizes: list[int] = []
        original_execute = SourceSession.execute

        def record(owner: SourceSession, issued: CompiledRead) -> pd.DataFrame:
            result = original_execute(owner, issued)
            statements.append(issued.sql.lower())
            sizes.append(len(result))
            return result

        monkeypatch.setattr(SourceSession, "execute", record)
        exact_amount = amount / 1 if backend == "clickhouse" else amount
        if backend in ("mysql", "trino", "clickhouse"):
            for metric in (
                amount.quantile(0.95),
                amount.median(),
                amount.quantile(0.95).per(category).mean(),
            ):
                before = len(statements)
                with pytest.raises(AnalysisStageUnavailableError, match=backend + ": "):
                    session.observe({"q": metric}).compute()
                assert len(statements) == before
        if backend not in ("mysql", "trino"):
            result = (
                session.observe(
                    {
                        "q0": exact_amount.quantile(0),
                        "q95": exact_amount.quantile(0.95),
                        "q1": exact_amount.quantile(1),
                        "median": exact_amount.median(),
                        "floating": (amount / 1).quantile(0.95),
                        "integer": catalog_value(
                            session.catalog, "sales.facts.weight", kind="field"
                        ).quantile(0.5),
                        "empty": exact_amount.quantile(0.95).where(amount > 100),
                        "null": exact_amount.quantile(0.95).where(amount.isnull()),
                    }
                )
                .compute()
                .to_pandas()
            )
            for name, expected in (
                ("q0", 0),
                ("q95", 9.5),
                ("q1", 10),
                ("median", 5),
                ("floating", 9.5),
                ("integer", 3),
            ):
                assert float(result[name].iloc[0]) == expected
            if backend != "clickhouse":
                assert isinstance(result["q95"].iloc[0], Decimal)
            assert result["empty"].isna().all()
            assert result["null"].isna().all()
            assert sizes and max(sizes) == 1
            grouped = (
                session.observe({"q": exact_amount.quantile(0.95)}, by=(category,))
                .compute()
                .to_pandas()
            )
            assert sorted(float(v) for v in grouped["q"].dropna()) == [9.5]
            folded = (
                session.observe({"q": exact_amount.quantile(0.95).per(category).mean()})
                .compute()
                .to_pandas()
            )
            assert float(folded["q"].iloc[0]) == 9.5
            samples = mv.coordinates(by=(category,), keys=(("x",), ("missing",)))
            sampled = session.observe({"q": exact_amount.quantile(0.95).per(samples).mean()})
            assert float(sampled.compute().to_pandas()["q"].iloc[0]) == 9.5
        approximate = session.observe(
            {
                "q": amount.approx_quantile(0.95),
                "median": amount.approx_median(),
                "empty": amount.approx_quantile(0.95).where(amount > 100),
                "null": amount.approx_quantile(0.95).where(amount.isnull()),
            }
        )
        if backend in ("postgres", "mysql"):
            before = len(statements)
            with pytest.raises(AnalysisStageUnavailableError, match=backend + ": approx_quantile"):
                approximate.compute()
            assert len(statements) == before
        else:
            import ibis

            before = len(statements)
            frame = approximate.compute().to_pandas()
            assert 0 <= float(frame["q"].iloc[0]) <= 10
            native_table = case.session.bind(
                case.source, source_identity="quantile-oracle"
            ).relation
            input_sql = ibis.to_sql(native_table.select("amount"), dialect=backend)
            native_median = {
                "duckdb": "approx_quantile(CAST(amount AS DOUBLE), 0.5)",
                "trino": "approx_percentile(CAST(amount AS DOUBLE), 0.5)",
                "clickhouse": "quantileTDigest(0.5)(CAST(amount AS Nullable(Float64)))",
            }[backend]
            oracle = case.session._backend.sql(
                f"SELECT {native_median} AS v FROM ({input_sql}) AS sampled",
                schema={"v": "float64"},
            ).execute()
            assert isinstance(oracle, pd.DataFrame)
            assert float(frame["median"].iloc[0]) == float(oracle.v.iloc[0])
            assert isinstance(frame["q"].iloc[0], Decimal)
            assert frame["empty"].isna().all()
            assert frame["null"].isna().all()
            sql = "\n".join(statements[before:])
            assert {
                "duckdb": "approx_quantile",
                "trino": "approx_percentile",
                "clickhouse": "quantiletdigest",
            }[backend] in sql


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_native_quantile_edge_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    samples = [
        ("single", 7),
        ("ties", 7),
        ("ties", 7),
        ("negative", -10),
        ("negative", 0),
        ("negative", 10),
        ("large", 9007199254740993),
        ("large", 9007199254740995),
        ("null", None),
    ]
    rows: list[dict[str, object]] = [
        {
            "id": i,
            "tenant": "a",
            "category": group,
            "amount": Decimal(value) if value is not None else None,
            "big": value,
            "weight": 1,
            "day": date(2026, 7, 1),
        }
        for i, (group, value) in enumerate(samples)
    ]
    values = ",".join(
        f"({i},'a','{group}',{value if value is not None else 'NULL'},{value if value is not None else 'NULL'},1,DATE '2026-07-01')"
        for i, (group, value) in enumerate(samples)
    )
    data = SourceData(DATA.columns, values, DATA.clickhouse_columns, rows)
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("quantile_edges", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        big = catalog_value(session.catalog, "sales.facts.big", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        if backend in ("mysql", "trino"):
            from marivo.analysis.errors import AnalysisStageUnavailableError
            from tests.support.source_trace import capture_source

            trace = capture_source(monkeypatch)
            with pytest.raises(AnalysisStageUnavailableError, match=backend + ": quantile"):
                session.observe({"q": amount.quantile(0.95)}, by=(category,)).compute()
            assert trace.returned_rows == []
            return
        exact_amount = amount / 1 if backend == "clickhouse" else amount
        frame = (
            session.observe(
                {
                    "q": exact_amount.quantile(0.95),
                    "tiny": exact_amount.quantile(5e-324),
                    "integer": big.quantile(0.5),
                },
                by=(category,),
            )
            .compute()
            .to_pandas()
        )
        frame = frame.set_index(frame.columns[0])
        for group, expected in (
            ("single", "7.00"),
            ("ties", "7.00"),
            ("negative", "9.00"),
        ):
            assert float(frame.loc[group, "q"]) == (
                8.99 if backend == "duckdb" and group == "negative" else float(expected)
            )
        assert frame.loc["negative", "tiny"] == Decimal("-10.00")
        native_table = case.session.bind(case.source, source_identity="edge-oracle").relation
        source_sql = case.session._backend.compile(native_table)
        native_q, native_integer = {
            "duckdb": ("quantile_cont(amount, 0.95)", "quantile_cont(big, 0.5)"),
            "postgres": (
                "percentile_cont(0.95) WITHIN GROUP (ORDER BY amount)",
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY big)",
            ),
            "clickhouse": (
                "quantileExactInclusive(0.95)(toFloat64(amount))",
                "quantileExactInclusive(0.5)(big)",
            ),
        }[backend]
        if backend in ("duckdb", "postgres"):
            native_q = f"CAST({native_q} AS DECIMAL(18,2))"
        native_sql = f"SELECT {native_q} AS q, {native_integer} AS middle FROM ({source_sql}) AS native_values WHERE category = 'large'"
        if backend == "clickhouse":
            oracle_row = case.session._backend.raw_sql(native_sql).result_rows[0]
        else:
            with case.session._backend.raw_sql(native_sql) as cursor:
                oracle_row = cursor.fetchone()
        assert oracle_row is not None
        assert float(frame.loc["large", "q"]) == float(oracle_row[0])
        assert frame.loc["large", "integer"] == oracle_row[1]
        assert frame.loc["null"].isna().all()


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_native_time_dependencies_and_drill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, DATA) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote_dependencies", timezone="UTC")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="field")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="field")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        members = session.members(session.catalog.get("entity:sales.facts"))
        identity_axis = members.axes[0]
        assert identity_axis.kind == "identity"
        chosen_values = session.values(
            by=(identity_axis, category),
            columns={"flag": None},
            rows=(((("a", 1), "x"), {"flag": True}), ((("b", 4), None), {"flag": True})),
        ).compute()
        entity = session.catalog.get("entity:sales.facts")
        chosen = session.population_from(
            chosen_values, on=((chosen_values.axes[0], entity), (chosen_values.axes[1], category))
        )
        selected = session.observe({"weight": weight.sum()}, where=mv.in_population(chosen))
        assert selected.compute().to_pandas()["weight"].tolist() == [1]
        fine = session.observe({"total": amount.sum()}, by=(category,))
        positive = fine.where(fine["total"] > 0).compute()
        categories = session.population_from(positive, on=((positive.axes[0], category),))
        drilled = (
            session.observe(
                {"total": amount.sum()}, by=(category, tenant), where=mv.in_population(categories)
            )
            .compute()
            .to_pandas()
        )
        assert [float(value) for value in drilled["total"]] == [1.25, 2.75]
        scope = session.time_scope(start="2026-07-01", end="2026-07-03").by(mv.grain("day"))
        window = session.catalog.get("metric:sales.total").window(mv.all_history())
        cumulative = session.observe({"total": window}, by=(scope,)).compute().to_pandas()
        assert [float(value) for value in cumulative["total"]] == [4.0, 4.0]
        empty = session.observe({"empty": session.catalog.get("metric:sales.empty")}).compute()
        assert empty.to_pandas()["empty"].tolist() == [Decimal("0.00")]
        from tests.analysis.materialization.publication_fixtures import publication_counts

        before = publication_counts(session)
        result = session.observe(
            {"zero": session.catalog.get("metric:sales.zero_weight")}
        ).compute()
        assert result.to_pandas()["zero"].isna().all()
        assert publication_counts(session) == before


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_mixed_predicate_keeps_null_rows_and_complete_identity_membership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, DATA) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote_mixed_predicate", timezone="UTC")
        entity = session.catalog.get("entity:sales.facts")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="field")
        captured = session.values(
            by=(mv.identity(entity),),
            columns={"flag": None},
            rows=(((("a", 3),), {"flag": True}),),
        ).compute()
        population = session.population_from(captured, on=((captured.axes[0], entity),))
        field = category == "x"
        member = mv.in_population(population)
        # Independent source rows: x selects identities a/1 and b/2, while
        # membership adds a/3 despite its null category. b/4 stays unknown.
        for condition, total, identities in (
            (field | member, 104, {("a", 1), ("b", 2), ("a", 3)}),
            (member & field, 0, set()),
            (~(field | member), 0, set()),
        ):
            observed = session.observe({"weight": weight.sum()}, where=condition)
            assert observed.compute().to_pandas()["weight"].tolist() == [total]
            scoped = session.observe({"weight": weight.sum().where(condition)})
            assert scoped.compute().to_pandas()["weight"].tolist() == [total]
            selected = session.frame(session.members(entity, where=condition)).compute().to_pandas()
            assert set(selected.iloc[:, 0].tolist()) == identities


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("selection", ("string", "null", "nul"))
def test_same_source_read_filter_population_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
    selection: Literal["string", "null", "nul"],
) -> None:
    from marivo.datasource.adapters import CompiledRead, SourceSession

    big = 9007199254740993
    columns = (
        DATA.columns.replace(
            "category VARCHAR(10)",
            "category VARCHAR(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
        )
        if backend == "mysql"
        else DATA.columns
    )
    data = SourceData(
        columns,
        f"({big},'a','e',1.25,1,1,DATE '2026-07-01'),"
        f"({big},'b',NULL,2.75,2,3,DATE '2026-07-01'),"
        f"({big + 1},'a',NULL,NULL,3,100,DATE '2026-07-02'),"
        f"({big + 1},'b','E',NULL,4,5,DATE '2026-07-02'),"
        f"({big + 2},'a','e ',NULL,5,7,DATE '2026-07-02'),"
        f"({big + 2},'b','é',NULL,6,11,DATE '2026-07-02')",
        DATA.clickhouse_columns,
        [
            {
                "id": identity,
                "tenant": tenant,
                "category": category,
                "amount": amount,
                "big": value,
                "weight": weight,
                "day": date(2026, 7, day),
            }
            for identity, tenant, category, amount, value, weight, day in (
                (big, "a", "e", Decimal("1.25"), 1, 1, 1),
                (big, "b", None, Decimal("2.75"), 2, 3, 1),
                (big + 1, "a", None, None, 3, 100, 2),
                (big + 1, "b", "E", None, 4, 5, 2),
                (big + 2, "a", "e ", None, 5, 7, 2),
                (big + 2, "b", "é", None, 6, 11, 2),
            )
        ],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        if backend == "duckdb":
            assert case.environment["read_only"] is True
        elif backend == "postgres":
            assert case.environment["read_only_privileges_verified"] is True
        else:
            denied = case.environment["denied"]
            assert isinstance(denied, (list, tuple))
            assert len(denied) >= 2
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("same_source_population", timezone="UTC")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="field")
        members = session.members(session.catalog.get("entity:sales.facts"))
        attributes = session.read(members, {"category": category})
        predicate = (
            attributes["category"].is_null()
            if selection == "null"
            else attributes["category"] == (chr(0) if selection == "nul" else "e")
        )
        selection_values = attributes.where(predicate).compute()
        selected = session.population_from(
            selection_values,
            on=((selection_values.axes[0], session.catalog.get("entity:sales.facts")),),
        )
        consumer = session.observe(
            {"weight": weight.sum()},
            by=() if selection == "nul" else (category,),
            where=mv.in_population(selected),
        )
        statements: list[str] = []
        uploads: list[int] = []
        execute = SourceSession.execute
        bind_keys = SourceSession._bind_key_relation

        def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
            statements.append(read.sql)
            return execute(owner, read)

        def record_upload(owner: SourceSession, table: pa.Table) -> ir.Table:
            uploads.append(table.num_rows)
            return bind_keys(owner, table)

        monkeypatch.setattr(SourceSession, "execute", record)
        monkeypatch.setattr(SourceSession, "_bind_key_relation", record_upload)

        def assert_source_read() -> None:
            assert statements
            assert all(
                statement.lstrip().startswith(("SELECT", "WITH")) for statement in statements
            )

        result = consumer.compute().to_pandas()
        expected = {"string": 1, "null": 103, "nul": 0}[selection]
        assert result["weight"].tolist() == [expected]
        if selection != "nul":
            coordinate = result[result.attrs["axes"][0]["name"].rsplit(".", 1)[-1]]
            assert coordinate.isna().all() if selection == "null" else coordinate.tolist() == ["e"]
        else:
            assert all(chr(0) not in statement for statement in statements)
        if selection != "nul":
            assert_source_read()
        else:
            # A NUL literal keeps the original pandas selection semantics.
            assert_source_read()
        if selection == "string":
            empty_values = attributes.where(attributes["category"] == "absent").compute()
            empty = session.population_from(
                empty_values,
                on=((empty_values.axes[0], session.catalog.get("entity:sales.facts")),),
            )
            for condition, expected in (
                (~mv.in_population(selected), 126),
                (mv.in_population(empty), 0),
            ):
                statements.clear()
                uploads.clear()
                result = (
                    session.observe({"weight": weight.sum()}, where=condition).compute().to_pandas()
                )
                assert result["weight"].tolist() == [expected]
                assert_source_read()


@pytest.mark.parametrize("backend,profile", source_params([("mysql", "innodb-table")]))
def test_mysql_source_selection_preserves_fetched_char_and_charset_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    from marivo.datasource.adapters import CompiledRead, SourceSession

    data = SourceData(
        DATA.columns.replace(
            "category VARCHAR(10)",
            "category CHAR(10) CHARACTER SET latin1 COLLATE latin1_general_ci",
        ),
        "(1,'a','e ',NULL,1,1,DATE '2026-07-01'),"
        "(2,'b','é',NULL,2,2,DATE '2026-07-01'),"
        "(3,'a','E',NULL,3,4,DATE '2026-07-01'),"
        "(4,'b',NULL,NULL,4,8,DATE '2026-07-01')",
        DATA.clickhouse_columns,
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        denied = case.environment["denied"]
        assert isinstance(denied, (list, tuple))
        assert len(denied) >= 2
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("source_char_selection", timezone="UTC")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="field")
        members = session.members(session.catalog.get("entity:sales.facts"))
        attributes = session.read(members, {"category": category})
        selected_values = attributes.where(attributes["category"] == "e").compute()
        selected = session.population_from(
            selected_values,
            on=((selected_values.axes[0], session.catalog.get("entity:sales.facts")),),
        )
        statements: list[str] = []
        execute = SourceSession.execute

        def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
            statements.append(read.sql)
            return execute(owner, read)

        monkeypatch.setattr(SourceSession, "execute", record)
        result = (
            session.observe({"weight": weight.sum()}, where=mv.in_population(selected))
            .compute()
            .to_pandas()
        )
        assert result["weight"].tolist() == [1]
        assert statements
        assert statements[0].lstrip().startswith(("SELECT", "WITH"))


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_empty_source_selection_retains_original_identity_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    from marivo.analysis.errors import AnalysisError
    from marivo.datasource.adapters import CompiledRead, SourceSession

    data = SourceData(
        DATA.columns,
        "(1,'a','x',1.25,1,1,DATE '2026-07-01'),(NULL,'b',NULL,2.75,2,3,DATE '2026-07-01')",
        DATA.clickhouse_columns.replace("id Int64", "id Nullable(Int64)"),
        [dict(DATA.rows[0]), {**DATA.rows[1], "id": None, "category": None}],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        if backend == "duckdb":
            assert case.environment["read_only"] is True
        elif backend == "postgres":
            assert case.environment["read_only_privileges_verified"] is True
        else:
            denied = case.environment["denied"]
            assert isinstance(denied, (list, tuple))
            assert len(denied) >= 2
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("source_check_selection", timezone="UTC")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="field")
        members = session.members(session.catalog.get("entity:sales.facts"))
        attributes = session.read(members, {"category": category})
        selected_values = attributes.where(attributes["category"] == "absent")
        selected = session.population_from(
            selected_values,
            on=((selected_values.axes[0], session.catalog.get("entity:sales.facts")),),
        )
        statements: list[str] = []
        execute = SourceSession.execute

        def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
            statements.append(read.sql)
            return execute(owner, read)

        def no_key_upload(*args: object, **kwargs: object) -> None:
            raise AssertionError("the source must check its original identities before selection")

        monkeypatch.setattr(SourceSession, "execute", record)
        monkeypatch.setattr(SourceSession, "_bind_key_relation", no_key_upload)
        with pytest.raises(AnalysisError, match="non-null complete identity keys"):
            session.observe({"weight": weight.sum()}, where=mv.in_population(selected)).compute()
        assert statements
        assert statements[0].lstrip().startswith(("SELECT", "WITH"))
        # The invalid original read must fail before any key upload or new source aggregate.


@pytest.mark.parametrize("backend,profile", source_params([("postgres", "table")]))
def test_remote_saved_values_cold_read_without_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    import subprocess
    import sys

    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, DATA) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote_cold", timezone="UTC")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        saved = session.observe(
            {"total": session.catalog.get("metric:sales.total")}, by=(category,)
        ).persist()
        saved.to_pandas().to_pickle(tmp_path / "saved-oracle.pkl")
        artifact_id = saved.artifact_id
    # The real source table is dropped before the independent reader starts.
    (tmp_path / "models").rename(tmp_path / "retired-models")
    code = """
import sys
from unittest.mock import patch
import pandas as pd
import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
from marivo.analysis.session.core import Session

def forbidden(*args, **kwargs):
    raise AssertionError('cold Artifact reading must not open a source or Catalog')

with patch.object(SourceSession, '__init__', forbidden), patch.object(Session, 'catalog', property(forbidden)):
    session = mv.session.resume(sys.argv[2])
    artifact = session.persisted_frame(sys.argv[1])
    actual = artifact.to_pandas()
    expected = pd.read_pickle('saved-oracle.pkl')
    assert actual.equals(expected)
    assert actual.attrs == expected.attrs
    artifact.show(n=1)
    assert artifact.findings().items == ()
    assert artifact.evidence(limit=100).items
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, artifact_id, session.id],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_public_native_distinct_algorithms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> None:
    from marivo.analysis.errors import AnalysisStageUnavailableError
    from tests.support.source_trace import capture_source

    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, DATA) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native_distinct", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        trace = capture_source(monkeypatch)
        frame = session.observe({"value": amount.count_distinct()}).compute().to_pandas()
        assert frame["value"].tolist() == [2]
        if backend == "clickhouse":
            assert any("uniqExact(" in sql for sql in trace.native_sql)
            assert all("uniq(" not in sql for sql in trace.native_sql)
        before = len(trace.returned_rows)
        approximate = amount.approx_nunique()
        if backend in ("postgres", "mysql"):
            # A permitted sibling and outer formula must not cause a partial read.
            query = session.observe({"value": approximate + amount.count_distinct()})
            with pytest.raises(AnalysisStageUnavailableError, match=backend + ": approx_nunique"):
                query.compute()
            assert len(trace.returned_rows) == before
        else:
            frame = session.observe({"value": approximate}).compute().to_pandas()
            assert frame["value"].tolist() == [2]
            expected = {
                "duckdb": "approx_count_distinct",
                "trino": "approx_distinct",
                "clickhouse": "uniqhll12",
            }[backend]
            assert any(expected in sql.lower() for sql in trace.native_sql)
