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
import marivo.semantic as ms
from marivo._definition.types import AxisBinding
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.datasource.source_cases import Case, SourceData, source_case, source_params

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
            + "day=ms.time_dimension_column(name='day',entity=facts,column='day',granularity='day',is_default=True)\n"
            + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n"
            + "big=ms.measure_column(name='big',entity=facts,column='big',additivity=ms.additive_all())\n"
            + "weight=ms.measure_column(name='weight',entity=facts,column='weight',additivity=ms.additive_all())\n"
            + "frozen=ms.finalize()\na=frozen.resolve(amount)\nb=frozen.resolve(big)\nw=frozen.resolve(weight)\n"
            + "@ms.metric()\ndef total():\n    return a.sum(empty='null')\n"
            + "@ms.metric()\ndef minimum():\n    return a.min()\n"
            + "@ms.metric()\ndef maximum():\n    return a.max()\n"
            + "@ms.metric()\ndef count():\n    return a.count()\n"
            + "@ms.metric()\ndef average():\n    return a.mean()\n"
            + "@ms.metric()\ndef weighted():\n    return a.weighted_mean(weight=w)\n"
            + "@ms.metric()\ndef distinct():\n    return a.count_distinct()\n"
            + "@ms.metric()\ndef quantile():\n    return a.quantile(q=0.5)\n"
            + "@ms.metric()\ndef empty():\n    return a.sum(empty='null').where(a>100)\n"
            + "@ms.metric()\ndef zero_error():\n    return a.weighted_mean(weight=w-w,zero_denominator='error')\n",
        }
    )


@pytest.mark.parametrize("backend,profile", source_params([("mysql", "innodb-table")]))
def test_mysql_direct_declared_utc_time_preserves_instants(
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
                + "happened=ms.time_dimension_column(name='happened',entity=facts,column='happened',granularity='hour',parse=ms.timestamp(timezone='UTC'),is_default=True)\n",
            }
        )
        table = case.session.bind(case.source, source_identity="utc-native-oracle").relation
        native = case.session._backend.execute(table.order_by("id"))
        assert isinstance(native, pd.DataFrame)
        session = mv.session.get_or_create("mysql_utc_read", report_timezone="America/New_York")
        members = session.members(ms.ref.entity("sales.facts"))
        artifact = session.read(
            members, {"happened": session.catalog.fields.get("sales.facts.happened")}
        ).persist()
        returned = artifact.to_pandas()[("value", "happened")]
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
            session.persisted_frame(artifact.artifact.id).to_pandas()
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
                + "happened=ms.time_dimension_column(name='happened',entity=facts,column='happened',granularity='hour',parse=ms.timestamp(timezone='UTC'),is_default=True)\n"
                + "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n",
            }
        )
        table = case.session.bind(case.source, source_identity="dst-native-oracle").relation
        native = case.session._backend.execute(
            table.filter(
                table.happened >= ibis.timestamp(points[0][1]),
                table.happened < ibis.timestamp(points[-1][1]),
            ).aggregate(total=table.amount.sum(), count=table.amount.count())
        )
        assert isinstance(native, pd.DataFrame)
        assert native.total.tolist() == [31]
        assert native["count"].tolist() == [3]
        session = mv.session.get_or_create("clickhouse_dst", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        happened = session.catalog.fields.get("sales.facts.happened")
        scope = session.time_scope(
            start="2026-11-01T01:00:00-04:00",
            end="2026-11-01T02:00:00-05:00",
            timezone="America/New_York",
        )
        artifact = session.observe(
            {"total": amount.sum(), "count": amount.count()}, during=scope
        ).persist()
        scalar = artifact.to_pandas()
        assert scalar[("value", "total")].tolist() == native.total.tolist()
        assert scalar[("value", "count")].tolist() == native["count"].tolist()
        fine = (
            session.observe({"total": amount.sum()}, by=(happened,), during=scope)
            .compute()
            .to_pandas()
        )
        assert fine[("coordinate", "axis.0")].tolist() == [point[1] for point in points[:-1]]
        assert fine[("value", "total")].tolist() == [1, 10, 20]
        assert artifact.to_pandas().equals(
            session.persisted_frame(artifact.artifact.id).to_pandas()
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
        session = mv.session.get_or_create("remote_numeric", report_timezone="UTC")
        category = session.catalog.fields.get("sales.facts.category")
        query = session.observe(
            {
                name: session.catalog.metrics.get("sales." + name)
                for name in ("total", "minimum", "maximum", "count")
            },
            by=(category,),
        )
        artifact = query.persist()
        frame = artifact.to_pandas()
        nonnull = frame.loc[frame[("coordinate", "axis.0")].notna()]
        assert nonnull[("value", "total")].tolist() == [Decimal("4.00")]
        assert nonnull[("value", "minimum")].tolist() == [Decimal("1.25")]
        assert nonnull[("value", "maximum")].tolist() == [Decimal("2.75")]
        assert nonnull[("value", "count")].tolist() == [2]
        assert len(frame) == 2
        assert frame.loc[frame[("coordinate", "axis.0")].isna(), ("value", "total")].isna().all()
        assert frame.equals(session.persisted_frame(artifact.artifact.id).to_pandas())
        members = session.members(ms.ref.entity("sales.facts"))
        direct_query = session.read(members, {"big": session.catalog.fields.get("sales.facts.big")})
        if profile == "distributed":
            from marivo.analysis.errors import AnalysisStageUnavailableError

            before = session.runs().items
            with pytest.raises(AnalysisStageUnavailableError, match="Distributed join route"):
                direct_query.compute()
            assert session.runs().items == before
            return
        direct = direct_query.compute()
        returned = direct.to_pandas()[("value", "big")]
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
        session = mv.session.get_or_create("remote_methods", report_timezone="UTC")
        query = session.observe(
            {
                name: session.catalog.metrics.get("sales." + name)
                for name in ("average", "weighted", "distinct")
            }
        )
        native_table = case.session.bind(
            case.source, source_identity="numeric-method-oracle"
        ).relation
        pair = native_table.amount.notnull() & native_table.weight.notnull()
        native = case.session._backend.execute(
            native_table.aggregate(
                weighted=(native_table.amount * native_table.weight).sum()
                / native_table.weight.sum(where=pair)
            )
        )
        assert isinstance(native, pd.DataFrame)
        from marivo.datasource.adapters import CompiledRead, SourceSession

        statements: list[str] = []
        original_execute = SourceSession.execute

        def record(owner: SourceSession, issued: CompiledRead) -> pd.DataFrame:
            statements.append(issued.sql)
            return original_execute(owner, issued)

        monkeypatch.setattr(SourceSession, "execute", record)
        frame = query.compute().to_pandas()
        assert float(frame[("value", "average")].iloc[0]) == pytest.approx(2.0)
        assert float(frame[("value", "weighted")].iloc[0]) == float(native.weighted.iloc[0])
        if backend == "clickhouse":
            assert "uniqExact(" in "\n".join(statements)
            assert "uniq(" not in "\n".join(statements)
        assert frame[("value", "distinct")].tolist() == [2]
        quantile = session.observe({"q": session.catalog.metrics.get("sales.quantile")})
        if backend not in ("duckdb", "postgres"):
            from marivo.analysis.errors import AnalysisStageUnavailableError

            before = session.runs().items
            with pytest.raises(AnalysisStageUnavailableError, match=backend + ": quantile"):
                quantile.compute()
            assert session.runs().items == before
        else:
            assert float(quantile.compute().to_pandas()[("value", "q")].iloc[0]) == 2.0


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
        session = mv.session.get_or_create("remote_dependencies", report_timezone="UTC")
        category = session.catalog.fields.get("sales.facts.category")
        tenant = session.catalog.fields.get("sales.facts.tenant")
        weight = session.catalog.fields.get("sales.facts.weight")
        amount = session.catalog.fields.get("sales.facts.amount")
        members = session.members(ms.ref.entity("sales.facts"))
        identity_axis = members.axes[0]
        assert isinstance(identity_axis, AxisBinding)
        chosen_values = session.values(
            by=(identity_axis, category),
            columns={"flag": None},
            rows=(((("a", 1), "x"), {"flag": True}), ((("b", 4), None), {"flag": True})),
        ).compute()
        entity = session.catalog.entities.get("sales.facts")
        chosen = session.population_from(
            chosen_values, on=((chosen_values.axes[0], entity), (chosen_values.axes[1], category))
        )
        selected = session.observe({"weight": weight.sum()}, where=mv.in_population(chosen))
        assert selected.compute().to_pandas()[("value", "weight")].tolist() == [1]
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
        assert [float(value) for value in drilled[("value", "total")]] == [1.25, 2.75]
        scope = session.time_scope(start="2026-07-01", end="2026-07-03").by(mv.grain("day"))
        window = session.catalog.metrics.get("sales.total").window(mv.all_history())
        cumulative = session.observe({"total": window}, during=scope).compute().to_pandas()
        assert [float(value) for value in cumulative[("value", "total")]] == [4.0, 4.0]
        empty = session.observe({"empty": session.catalog.metrics.get("sales.empty")}).compute()
        assert empty.to_pandas()[("value", "empty")].isna().all()
        from marivo.analysis.errors import AnalysisError
        from tests.analysis.materialization.publication_fixtures import publication_counts

        before = publication_counts(session)
        with pytest.raises(AnalysisError, match="denominator"):
            session.observe({"zero": session.catalog.metrics.get("sales.zero_error")}).compute()
        assert publication_counts(session) == before


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
        session = mv.session.get_or_create("same_source_population", report_timezone="UTC")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        members = session.members(ms.ref.entity("sales.facts"))
        attributes = session.read(members, {"category": category})
        predicate = (
            attributes["category"].is_null()
            if selection == "null"
            else attributes["category"] == (chr(0) if selection == "nul" else "e")
        )
        selection_values = attributes.where(predicate).compute()
        selected = session.population_from(
            selection_values,
            on=((selection_values.axes[0], session.catalog.entities.get("sales.facts")),),
        )
        consumer = session.observe(
            {"weight": weight.sum(empty="zero")},
            by=() if selection == "nul" else (category,),
            members=selected,
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
        assert result[("value", "weight")].tolist() == [expected]
        if selection != "nul":
            coordinate = result[("coordinate", "axis.0")]
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
                on=((empty_values.axes[0], session.catalog.entities.get("sales.facts")),),
            )
            for condition, expected in (
                (~mv.in_population(selected), 126),
                (mv.in_population(empty), 0),
            ):
                statements.clear()
                uploads.clear()
                result = (
                    session.observe({"weight": weight.sum(empty="zero")}, where=condition)
                    .compute()
                    .to_pandas()
                )
                assert result[("value", "weight")].tolist() == [expected]
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
        session = mv.session.get_or_create("source_char_selection", report_timezone="UTC")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        members = session.members(ms.ref.entity("sales.facts"))
        attributes = session.read(members, {"category": category})
        selected_values = attributes.where(attributes["category"] == "e").compute()
        selected = session.population_from(
            selected_values,
            on=((selected_values.axes[0], session.catalog.entities.get("sales.facts")),),
        )
        statements: list[str] = []
        execute = SourceSession.execute

        def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
            statements.append(read.sql)
            return execute(owner, read)

        monkeypatch.setattr(SourceSession, "execute", record)
        result = session.observe({"weight": weight.sum()}, members=selected).compute().to_pandas()
        assert result[("value", "weight")].tolist() == [1]
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
        session = mv.session.get_or_create("source_check_selection", report_timezone="UTC")
        category = session.catalog.fields.get("sales.facts.category")
        weight = session.catalog.fields.get("sales.facts.weight")
        members = session.members(ms.ref.entity("sales.facts"))
        attributes = session.read(members, {"category": category})
        selected_values = attributes.where(attributes["category"] == "absent")
        selected = session.population_from(
            selected_values,
            on=((selected_values.axes[0], session.catalog.entities.get("sales.facts")),),
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
            session.observe({"weight": weight.sum()}, members=selected).compute()
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
        session = mv.session.get_or_create("remote_cold", report_timezone="UTC")
        category = session.catalog.fields.get("sales.facts.category")
        saved = session.observe(
            {"total": session.catalog.metrics.get("sales.total")}, by=(category,)
        ).persist()
        saved.to_pandas().to_pickle(tmp_path / "saved-oracle.pkl")
        artifact_id = saved.artifact.id
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
    session = mv.session.resume('remote_cold', by='name')
    artifact = session.persisted_frame(sys.argv[1])
    actual = artifact.to_pandas()
    expected = pd.read_pickle('saved-oracle.pkl')
    assert actual.equals(expected)
    assert actual.attrs == expected.attrs
    artifact.show(n=1)
    assert artifact.artifact.findings().items == ()
    assert artifact.artifact.evidence_digest().items
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, artifact_id],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
