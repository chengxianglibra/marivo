"""Native pending field Top returns bounded rows on each supported provider."""

from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.core.frame_data import StoredFrameBacking
from marivo.analysis.materialization.errors import MaterializationError
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import capture_source

CASES = [
    ("duckdb", "table"),
    ("postgres", "table"),
    ("mysql", "innodb-table"),
    ("trino", "iceberg"),
    ("clickhouse", "mergetree"),
]


def _data() -> SourceData:
    rows: list[dict[str, object]] = [
        {
            "id": identity,
            "tenant": tenant,
            "category": "x",
            "amount": 1,
            "big": score,
            "weight": 1 if tenant == "a" else 0,
            "day": date(2026, 7, 1),
        }
        for tenant, identity, score in (
            ("b", 1, 2**53 + 1),
            ("a", 2, None),
            ("a", 1, 2**53 + 1),
            ("b", 2, 2**53),
        )
    ]
    return SourceData(
        "id BIGINT, tenant VARCHAR(10), category VARCHAR(10), amount BIGINT, big BIGINT, weight BIGINT, day DATE",
        ",".join(
            f"({row['id']},'{row['tenant']}','x',1,"
            + ("NULL" if row["big"] is None else str(row["big"]))
            + f",{row['weight']},DATE '2026-07-01')"
            for row in rows
        ),
        "id Int64, tenant String, category String, amount Int64, big Nullable(Int64), weight Int64, day Date",
        rows,
    )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_native_read_top_retains_order_bounded_transport_and_fixed_boundary(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data()) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native-read-top", timezone="UTC")
        members = session.members(session.catalog.entities.get("sales.facts"))
        score = session.catalog.fields.get("sales.facts.big")
        pending = session.read(members, {"score": score})
        raw = pending.compute().to_pandas()
        assert raw.iloc[:, 0].tolist() == [("a", 1), ("a", 2), ("b", 1), ("b", 2)]
        assert raw[("value", "score")].tolist() == [2**53 + 1, pd.NA, 2**53 + 1, 2**53]
        assert str(raw[("value", "score")].dtype) == "int64[pyarrow]"
        trace = capture_source(monkeypatch, measure_cost=True)
        for descending, expected in (
            (True, [("a", 1), ("b", 1), ("b", 2), ("a", 2)]),
            (False, [("b", 2), ("a", 1), ("b", 1), ("a", 2)]),
        ):
            result = pending.top(4, by=pending["score"], descending=descending).compute()
            rows = result.to_pandas()
            assert rows.iloc[:, 0].tolist() == expected
            assert pd.isna(rows[("value", "score")].iloc[-1])
            assert str(rows[("value", "score")].dtype) == "int64[pyarrow]"
        top = pending.top(1, by=pending["score"]).compute()
        assert top.to_pandas().iloc[0].tolist() == [("a", 1), 2**53 + 1]
        assert pending.top(0, by=pending["score"]).compute().to_pandas().empty
        assert trace.returned_rows == [4, 4, 1, 1]
        assert trace.uploaded_key_rows == []
        assert all("ORDER BY" in sql for sql in trace.native_sql)
        assert all(
            "IS NOT DISTINCT FROM" not in sql and "<=>" not in sql for sql in trace.native_sql
        )
        assert all("SELECT DISTINCT" not in sql for sql in trace.native_sql)
        trace.save(
            "frame-top-" + backend,
            case.environment,
            {"rows": 4, "top": [4, 4, 1, 0], "score": "nullable int64"},
            {
                "descending_keys": [["a", 1], ["b", 1], ["b", 2], ["a", 2]],
                "ascending_keys": [["b", 2], ["a", 1], ["b", 1], ["a", 2]],
                "returned_rows": [4, 4, 1, 1],
                "uploads": [],
            },
            top,
            (case.session,),
        )
        stored = top.persist()
        assert isinstance(stored._backing, StoredFrameBacking)
        reopened = session.persisted_frame(stored._backing.artifact_id)
        trace.returned_rows.clear()
        trace.native_sql.clear()
        for fixed in (top, stored, reopened):
            assert fixed.top(1, by=fixed["score"]).compute().to_pandas().iloc[0].tolist() == [
                ("a", 1),
                2**53 + 1,
            ]
            assert fixed.top(0, by=fixed["score"]).compute().to_pandas().empty
        assert trace.returned_rows == [] and trace.uploaded_key_rows == []
        if backend == "postgres":
            from tests.datasource.environment import postgres_analysis as postgres

            assert isinstance(case.source, TableSourceIR)
            with postgres.connection(admin=True) as admin:
                admin.execute(f"DELETE FROM public.{case.source.table}")
        elif backend == "trino":
            from tests.datasource.environment import trino_analysis as trino

            assert isinstance(case.source, TableSourceIR)
            with trino.connection(admin=True, catalog="iceberg") as admin:
                admin.cursor().execute(
                    f"DELETE FROM iceberg.analysis.{case.source.table}"
                ).fetchall()
        if backend in ("postgres", "trino"):
            for n in (0, 1):
                empty = pending.top(n, by=pending["score"]).compute().to_pandas()
                assert empty.empty
                assert str(empty[("value", "score")].dtype) == "int64[pyarrow]"
            assert trace.returned_rows == [1, 1] and trace.uploaded_key_rows == []


@pytest.mark.parametrize("backend,profile", source_params(CASES))
def test_filtered_native_read_top_reuses_its_exact_member_scope(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, _data()) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("filtered-native-read-top", timezone="UTC")
        entity = session.catalog.entities.get("sales.facts")
        weight = session.catalog.fields.get("sales.facts.weight")
        score = session.catalog.fields.get("sales.facts.big")
        trace = capture_source(monkeypatch)
        selected = session.read(session.members(entity, where=weight >= 1), {"score": score})
        rows = selected.top(4, by=selected["score"]).compute().to_pandas()
        assert rows.iloc[:, 0].tolist() == [("a", 1), ("a", 2)]
        assert rows[("value", "score")].tolist() == [2**53 + 1, pd.NA]
        assert selected.top(0, by=selected["score"]).compute().to_pandas().empty
        empty = session.read(session.members(entity, where=weight < 0), {"score": score})
        assert empty.top(1, by=empty["score"]).compute().to_pandas().empty
        assert trace.returned_rows == [2, 1, 1] and trace.uploaded_key_rows == []
        assert all("EXISTS" not in sql and "SELECT DISTINCT" not in sql for sql in trace.native_sql)


@pytest.mark.parametrize(
    "backend,profile",
    source_params([("postgres", "table"), ("mysql", "innodb-table"), ("trino", "iceberg")]),
)
@pytest.mark.parametrize("n", (0, 1))
@pytest.mark.parametrize("invalid", ("duplicate", "null_identity"))
def test_native_read_top_preserves_full_input_key_errors(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
    n: int,
    invalid: str,
) -> None:
    source = _data()
    extra = (
        "(2,'a','x',1,1,1,DATE '2026-07-01')"
        if invalid == "duplicate"
        else (
            "(NULL,'a','x',1,0,1,DATE '2026-07-01')"
            if backend in ("postgres", "trino")
            else "(0,NULL,'x',1,0,1,DATE '2026-07-01')"
        )
    )
    monkeypatch.chdir(tmp_path)
    with source_case(
        backend, profile, tmp_path, monkeypatch, replace(source, values=source.values + "," + extra)
    ) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native-read-top-integrity", timezone="UTC")
        members = session.members(
            session.catalog.entities.get("sales.facts"),
            where=session.catalog.fields.get("sales.facts.weight") >= 0,
        )
        pending = session.read(members, {"score": session.catalog.fields.get("sales.facts.big")})
        trace = capture_source(monkeypatch)
        with pytest.raises(MaterializationError) as failure:
            pending.top(n, by=pending["score"]).compute()
        assert failure.value.expected is not None
        if invalid == "duplicate":
            assert failure.value.stage == "stage_output"
            assert "unique stage key" in failure.value.expected
        else:
            assert "identity" in failure.value.expected
            assert "non-null" in failure.value.expected
        assert trace.returned_rows == [1] and trace.uploaded_key_rows == []


@pytest.mark.parametrize(
    "backend,profile", source_params([("postgres", "table"), ("trino", "iceberg")])
)
@pytest.mark.parametrize("n", (0, 1))
def test_native_read_top_preserves_nonfinite_values_outside_selection(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
    n: int,
) -> None:
    source = _data()
    real = "DOUBLE PRECISION" if backend == "postgres" else "DOUBLE"
    data = replace(
        source,
        columns=source.columns.replace("big BIGINT", f"big {real}"),
        values="(1,'a','x',1,1,1,DATE '2026-07-01'),"
        + f"(2,'a','x',1,CAST('Infinity' AS {real}),0,DATE '2026-07-01')",
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native-read-top-nonfinite", timezone="UTC")
        members = session.members(
            session.catalog.entities.get("sales.facts"),
            where=session.catalog.fields.get("sales.facts.weight") >= 0,
        )
        pending = session.read(
            members,
            {
                "score": session.catalog.fields.get("sales.facts.weight"),
                "invalid": session.catalog.fields.get("sales.facts.big"),
            },
        )
        trace = capture_source(monkeypatch)
        result = pending.top(n, by=pending["score"]).compute().to_pandas()
        assert len(result) == n
        if n:
            assert result["value", "score"].tolist() == [1]
            assert result["value", "invalid"].tolist() == [1.0]
        if backend == "postgres":
            # Float payloads keep PostgreSQL's existing complete-read boundary.
            assert trace.returned_rows and all(count == 2 for count in trace.returned_rows)
        else:
            assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("backend,profile", source_params([("mysql", "innodb-table")]))
def test_native_read_top_keeps_byte_identity_on_case_insensitive_columns(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    source = _data()
    data = replace(
        source,
        columns=source.columns.replace(
            "tenant VARCHAR(10)", "tenant VARCHAR(10) COLLATE utf8mb4_0900_ai_ci"
        ).replace("category VARCHAR(10)", "category VARCHAR(10) COLLATE utf8mb4_0900_ai_ci"),
        values="(1,'A',NULL,10,9007199254740993,1,DATE '2026-07-01'),"
        "(1,'a',NULL,20,9007199254740993,1,DATE '2026-07-01')",
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native-read-top-byte-identity", timezone="UTC")
        members = session.members(
            session.catalog.entities.get("sales.facts"),
            where=session.catalog.fields.get("sales.facts.weight") >= 0,
        )
        pending = session.read(members, {"score": session.catalog.fields.get("sales.facts.big")})
        trace = capture_source(monkeypatch)
        rows = pending.top(2, by=pending["score"]).compute().to_pandas()
        assert rows.iloc[:, 0].tolist() == [("A", 1), ("a", 1)]
        assert rows[("value", "score")].tolist() == [2**53 + 1, 2**53 + 1]
        top = pending.top(1, by=pending["score"]).compute().to_pandas()
        assert top.iloc[0].tolist() == [("A", 1), 2**53 + 1]
        assert trace.returned_rows == [2, 1] and trace.uploaded_key_rows == []
        requested = mv.coordinates(
            by=(mv.identity(session.catalog.entities.get("sales.facts")),),
            keys=((("a", 1),), (("missing", 99),)),
        )
        selected = (
            session.read(requested, {"score": session.catalog.fields.get("sales.facts.big")})
            .compute()
            .to_pandas()
        )
        assert selected.iloc[:, 0].tolist() == [("a", 1), ("missing", 99)]
        assert selected[("value", "score")].tolist() == [2**53 + 1, pd.NA]
        entity = session.catalog.entities.get("sales.facts")
        total = session.catalog.metrics.get("sales.total")
        for domain, expected in (
            (session.members(entity), 30),
            (mv.coordinates(by=(mv.identity(entity),), keys=((("A", 1),),)), 10),
            (mv.coordinates(by=(mv.identity(entity),), keys=((("a", 1),),)), 20),
        ):
            result = session.observe({"total": total}, where=mv.in_population(domain))
            assert result.compute().to_pandas()[("value", "total")].tolist() == [expected]
        category = session.catalog.fields.get("sales.facts.category")
        all_members = session.members(entity)
        mixed = all_members.classify(by=(all_members.axes[0], category))
        for domain, expected in (
            (mixed, 30),
            (mv.coordinates(by=(mv.identity(entity), category), keys=((("A", 1), None),)), 10),
            (mv.coordinates(by=(mv.identity(entity), category), keys=((("a", 1), None),)), 20),
        ):
            result = session.observe({"total": total}, where=mv.in_population(domain))
            assert result.compute().to_pandas()[("value", "total")].tolist() == [expected]
        from tests.datasource.environment import mysql_analysis as mysql

        assert isinstance(case.source, TableSourceIR)
        with mysql.connection(admin=True) as admin, admin.cursor() as cursor:
            cursor.execute(f"UPDATE {case.source.table} SET category='x'")
        native_categories = mv.coordinates(
            by=(mv.identity(entity), category),
            keys=((("A", 1), "x"), (("A", 1), "X"), (("a", 1), "x"), (("a", 1), "X")),
        )
        result = session.observe({"total": total}, where=mv.in_population(native_categories))
        assert result.compute().to_pandas()[("value", "total")].tolist() == [30]
        assert trace.uploaded_key_rows == []
