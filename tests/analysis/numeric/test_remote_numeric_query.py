"""Public remote Query execution and native pandas numerical handoff."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
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
        ).execute()
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
        assert artifact.to_pandas().equals(session.artifact(artifact.id).to_pandas())


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
        ).execute()
        scalar = artifact.to_pandas()
        assert scalar[("value", "total")].tolist() == native.total.tolist()
        assert scalar[("value", "count")].tolist() == native["count"].tolist()
        fine = (
            session.observe({"total": amount.sum()}, by=(happened,), during=scope)
            .execute()
            .to_pandas()
        )
        assert fine[("coordinate", "axis.0")].tolist() == [point[1] for point in points[:-1]]
        assert fine[("value", "total")].tolist() == [1, 10, 20]
        assert artifact.to_pandas().equals(session.artifact(artifact.id).to_pandas())


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
        artifact = query.execute()
        frame = artifact.to_pandas()
        nonnull = frame.loc[frame[("coordinate", "axis.0")].notna()]
        assert nonnull[("value", "total")].tolist() == [Decimal("4.00")]
        assert nonnull[("value", "minimum")].tolist() == [Decimal("1.25")]
        assert nonnull[("value", "maximum")].tolist() == [Decimal("2.75")]
        assert nonnull[("value", "count")].tolist() == [2]
        assert len(frame) == 2
        assert frame.loc[frame[("coordinate", "axis.0")].isna(), ("value", "total")].isna().all()
        assert frame.equals(session.artifact(artifact.id).to_pandas())
        members = session.members(ms.ref.entity("sales.facts"))
        direct_query = session.read(members, {"big": session.catalog.fields.get("sales.facts.big")})
        if profile == "distributed":
            from marivo.analysis.errors import AnalysisStageUnavailableError

            before = session.runs().items
            with pytest.raises(AnalysisStageUnavailableError, match="Distributed join route"):
                direct_query.execute()
            assert session.runs().items == before
            return
        direct = direct_query.execute()
        returned = direct.to_pandas()[("value", "big")]
        assert returned.isna().sum() == 3
        assert pd.notna(returned.iloc[0])


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
        frame = query.execute().to_pandas()
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
                quantile.execute()
            assert session.runs().items == before
        else:
            assert float(quantile.execute().to_pandas()[("value", "q")].iloc[0]) == 2.0


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
        chosen = session.values(
            by=(members.axes[0], category),
            columns={"flag": None},
            rows=(((("a", 1), "x"), {"flag": True}), ((("b", 4), None), {"flag": True})),
        ).population()
        selected = session.observe({"weight": weight.sum()}, where=mv.in_population(chosen))
        assert selected.execute().to_pandas()[("value", "weight")].tolist() == [1]
        fine = session.observe({"total": amount.sum()}, by=(category,))
        drilled = fine.where(fine["total"] > 0).drill(add=(tenant,)).execute().to_pandas()
        assert [float(value) for value in drilled[("value", "total")]] == [1.25, 2.75]
        scope = session.time_scope(start="2026-07-01", end="2026-07-03").by(mv.grain("day"))
        window = session.catalog.metrics.get("sales.total").window(mv.all_history())
        cumulative = session.observe({"total": window}, during=scope).execute().to_pandas()
        assert [float(value) for value in cumulative[("value", "total")]] == [4.0, 4.0]
        empty = session.observe({"empty": session.catalog.metrics.get("sales.empty")}).execute()
        assert empty.to_pandas()[("value", "empty")].isna().all()
        from marivo.analysis.errors import AnalysisError
        from tests.analysis.materialization.publication_fixtures import publication_counts

        before = publication_counts(session)
        with pytest.raises(AnalysisError, match="denominator"):
            session.observe({"zero": session.catalog.metrics.get("sales.zero_error")}).execute()
        assert publication_counts(session) == before


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
        ).execute()
        saved.to_pandas().to_pickle(tmp_path / "saved-oracle.pkl")
        artifact_id = saved.id
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
    artifact = session.artifact(sys.argv[1])
    actual = artifact.to_pandas()
    expected = pd.read_pickle('saved-oracle.pkl')
    assert actual.equals(expected)
    assert actual.attrs == expected.attrs
    artifact.show(n=1)
    assert artifact.findings().items == ()
    assert artifact.evidence_digest().items
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, artifact_id],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
