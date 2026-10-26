"""Native definition-owned C10 distribution consumers."""

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Literal, NoReturn

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.datasource.adapters import CompiledRead, SourceSession, provider_for
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.graph.distribution_fixtures import Carrier, distribution_data
from tests.analysis.numeric.pandas_coordinates import ordinary_columns
from tests.datasource.source_cases import Case, datasource, source_case, source_params
from tests.shared_fixtures import run_ids
from tests.support.source_trace import SourceTrace


@pytest.mark.parametrize(
    "backend,kind,carrier",
    source_params(
        [
            (backend, kind, "int64")
            for backend in ("duckdb", "postgres", "mysql", "trino", "clickhouse")
            for kind in ("distinct", "identity", "quantile", "approx_distinct", "approx_quantile")
        ]
        + [("duckdb", "distinct", "timestamp")]
        + [("duckdb", "approx_distinct", "string")]
        + [(backend, "distinct", "decimal") for backend in ("postgres", "mysql", "trino")]
        + [
            (backend, "approx_distinct", "decimal")
            for backend in ("duckdb", "postgres", "mysql", "trino", "clickhouse")
        ]
        + [("postgres", "quantile", "decimal")]
        + [("postgres", "distinct_nonfinite", "decimal")]
        + [
            (backend, kind, carrier)
            for backend in ("postgres", "mysql", "trino", "clickhouse")
            for kind in ("distinct", "approx_distinct")
            for carrier in ("float64", "string", "boolean", "date", "timestamp")
            if not (backend == "clickhouse" and kind == "distinct")
        ]
        + [
            (backend, kind, "float64")
            for backend in ("postgres", "trino", "clickhouse")
            for kind in ("quantile", "approx_quantile")
            if not (backend in ("trino", "clickhouse") and kind == "quantile")
        ]
    ),
)
def test_native_distribution(
    backend: str,
    kind: Literal[
        "distinct",
        "distinct_nonfinite",
        "identity",
        "quantile",
        "approx_distinct",
        "approx_quantile",
    ],
    carrier: Carrier,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    source_trace: SourceTrace,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARIVO_PROJECT_ROOT", str(tmp_path))
    profile = (
        "iceberg" if backend == "trino" else "mergetree" if backend == "clickhouse" else "table"
    )
    datasets = [
        distribution_data(backend, [(1, "a", 0), (2, "b", 0), (3, "c", 0)], carrier),
        distribution_data(
            backend,
            [
                (9007199254740992, "a", 2),
                (9007199254740993, "a", 6),
                (9007199254740994, "a", 6),
                (9007199254740995, "a", None),
                (9007199254740993, "b", 6),
            ],
            carrier,
        ),
    ]
    if kind == "distinct_nonfinite":
        datasets[1] = replace(
            datasets[1], values=datasets[1].values.replace("9007199254740993.000001", "'NaN'")
        )
    with ExitStack() as stack:
        if backend == "duckdb":
            path = tmp_path / "source.duckdb"
            admin = ibis.duckdb.connect(path)
            for name, data in zip(("subjects", "facts"), datasets, strict=True):
                admin.raw_sql(f"CREATE TABLE {name} ({data.columns})")
                admin.raw_sql(f"INSERT INTO {name} VALUES {data.values}")
            admin.disconnect()
            connection = stack.enter_context(
                provider_for(backend).open(
                    datasource(backend, {"path": str(path), "read_only": True})
                )
            )
            subject, facts = [
                Case(connection, TableSourceIR(name), {"backend": backend, "read_only": True})
                for name in ("subjects", "facts")
            ]
        else:
            subject, facts = [
                stack.enter_context(source_case(backend, profile, tmp_path, monkeypatch, data))
                for data in datasets
            ]
        args = {
            **subject.session.datasource.fields,
            **{key + "_env": value for key, value in subject.session.datasource.env_refs.items()},
        }
        if "user" in args:
            monkeypatch.setenv("MARIVO_R93_READER", str(args.pop("user")))
            args["user_env"] = "MARIVO_R93_READER"
        timestamp = (
            "ms.timestamp()"
            if backend in ("mysql", "clickhouse")
            else "ms.timestamp(timezone='UTC')"
        )
        models = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        for name, case in (("subjects", subject), ("facts", facts)):
            assert isinstance(case.source, TableSourceIR)
            keys = ["owner"] if name == "subjects" else ["id", "owner"]
            models += f"{name}=ms.entity(name={name!r},datasource=ms.ref.datasource('warehouse'),source=md.table({case.source.table!r},database={case.source.database!r}),primary_key={keys!r})\n"
            models += (
                f"{name}_owner=ms.dimension_column(name='owner',entity={name},column='owner')\n"
            )
            models += f"{name}_time=ms.time_dimension_column(name='time',entity={name},column='happened',parse={timestamp},is_default=True)\n"
        models += "identity=ms.measure_column(name='identity',entity=facts,column='id',additivity=ms.additive_all())\n"
        models += (
            "amount=ms.measure_column(name='amount',entity=facts,column='amount',additivity=ms.additive_all())\n"
            if carrier in {"int64", "float64", "decimal"}
            else "amount=ms.time_dimension_column(name='amount',entity=facts,column='amount',parse=ms.timestamp(timezone='UTC'))\n"
            if carrier == "timestamp"
            else "amount=ms.dimension_column(name='amount',entity=facts,column='amount')\n"
        )
        models += "facts_subject=ms.relationship(name='facts_subject',from_entity=facts,to_entity=subjects,keys=[ms.join_on(facts_owner,subjects_owner)])\n"
        semantic_project_factory(
            {
                "datasources/warehouse.py": "import marivo.datasource as md\n"
                + f"md.{backend}(name='warehouse',"
                + ",".join(f"{key}={value!r}" for key, value in args.items())
                + ")\n",
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='R9',default=True)\n",
                "sales/models.py": models,
            }
        )
        if backend != "duckdb":

            def forbid_staging(*args: object, **kwargs: object) -> NoReturn:
                pytest.fail("C10 cannot create or drop backend tables")

            monkeypatch.setattr(type(subject.session._backend), "create_table", forbid_staging)
            monkeypatch.setattr(type(subject.session._backend), "drop_table", forbid_staging)
        session = mv.session.get_or_create("r93-distribution", timezone="UTC")
        members = session.members(session.catalog.entities.get("sales.subjects"))
        field = session.catalog.fields.get(
            "sales.facts.identity" if kind == "identity" else "sales.facts.amount"
        )
        time = session.catalog.fields.get("sales.facts.time")
        metric = (
            field.count_distinct(time=time)
            if kind in ("distinct", "distinct_nonfinite", "identity")
            else field.approx_nunique(time=time)
            if kind == "approx_distinct"
            else field.quantile(0.25, time=time)
            if kind == "quantile"
            else field.approx_quantile(0.25, time=time)
        )
        population = mv.product(session.time_scope(start="2026-08-01", end="2026-08-02"), members)
        assert isinstance(population, mv.Population)
        observed = session.observe({"value": metric}, by=population)
        submissions: list[dict[str, str]] = []
        source_execute = SourceSession.execute

        def traced_execute(self: SourceSession, read: CompiledRead) -> pd.DataFrame:
            frame = source_execute(self, read)
            submissions.append(
                {"sql": read.sql, "purpose": read.purpose, "source_identity": read.source_identity}
            )
            return frame

        monkeypatch.setattr(SourceSession, "execute", traced_execute)
        native_before = len(source_trace.native_sql)
        before_runs = run_ids(session)
        refuses = (
            (backend == "mysql" and carrier == "boolean")
            or (kind in ("distinct", "identity") and backend == "clickhouse")
            or (kind == "approx_distinct" and backend == "mysql")
            or (kind == "approx_quantile" and backend in ("postgres", "mysql"))
        )
        if refuses:
            before = set((tmp_path / ".marivo").rglob("*.parquet"))
            with pytest.raises(AnalysisError) as caught:
                observed.persist()
            failure = caught.value
            assert failure.expected and failure.received and failure.repair
            assert not submissions
            assert len(source_trace.native_sql) == native_before
            assert set((tmp_path / ".marivo").rglob("*.parquet")) == before
            assert run_ids(session) == before_runs
            return
        fixed = observed.persist()
        source_trace.record(fixed)
        exported = fixed.to_pandas()
        frame = exported.set_index(ordinary_columns(exported)[0])
        values = frame["value"]
        assert frame.index.tolist() == ["a", "b", "c"]
        expected = (
            {"a": 4, "b": 1, "c": 0}
            if kind == "identity"
            else {"a": 2, "b": 1, "c": 0}
            if kind in ("distinct", "distinct_nonfinite", "approx_distinct")
            else {"a": Decimal("9007199254740993.000002"), "b": Decimal("9007199254740993.000002")}
            if carrier == "decimal"
            else {"a": 4, "b": 6}
        )
        for owner, value in expected.items():
            if kind == "approx_quantile" and owner == "a":
                assert 2 <= values.loc[owner] <= 6
            else:
                assert values.loc[owner] == value
        if kind in ("quantile", "approx_quantile"):
            assert pd.isna(values.loc["c"])
        source_trace.save(
            f"c10-source-{backend}-{kind}",
            {**subject.environment, "profile": profile},
            {"columns": datasets[1].columns, "values": datasets[1].values},
            {
                "kind": kind,
                "input_identity_types": ["int64", "string"],
                "values": [4, 1, 0]
                if kind == "identity"
                else [2, 1, 0]
                if kind in ("distinct", "distinct_nonfinite", "approx_distinct")
                else [str(expected["a"]), str(expected["b"]), None]
                if kind == "quantile" and carrier == "decimal"
                else [4, 6, None]
                if kind == "quantile"
                else ["bounded_2_through_6", 6, None],
                "empty_cell": "defined"
                if kind in ("distinct", "distinct_nonfinite", "identity", "approx_distinct")
                else "null",
            },
            fixed,
            (subject.session, facts.session),
        )

        assert submissions
        if kind == "quantile":
            assert any(
                "0.25" in read["sql"] or "__quantile_lower" in read["sql"] for read in submissions
            )
