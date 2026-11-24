"""Native pending field Top returns bounded rows on each supported provider."""

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal
from math import inf
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.core.frame_data import StoredFrameBacking
from marivo.analysis.model.data_types import DecimalDataType, ScalarDataType
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.datasource.ir import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.terminal_fusion_fixtures import (
    TERMINAL_SOURCE_PROFILES,
    terminal_key,
    terminal_key_rows,
    terminal_rows,
)
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.environment import clickhouse_analysis as clickhouse
from tests.datasource.environment import mysql_analysis as mysql
from tests.datasource.environment import postgres_analysis as postgres
from tests.datasource.environment import trino_analysis as trino
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.formal.reference.equivalence import ObservedFailure, assert_equivalent, run_frame
from tests.formal.reference.evaluator import top as reference_top
from tests.formal.reference.model import Column, Result
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import capture_source


@pytest.mark.parametrize("backend,profile", source_params(TERMINAL_SOURCE_PROFILES))
def test_native_read_top_retains_order_bounded_transport_and_fixed_boundary(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    data = terminal_key_rows(backend, terminal_rows())
    a1, a2, b1, b2 = (terminal_key(backend, tenant, row) for tenant in ("a", "b") for row in (1, 2))
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native-read-top", timezone="UTC")
        members = session.members(catalog_value(session.catalog, "sales.facts", kind="entity"))
        score = catalog_value(session.catalog, "sales.facts.big", kind="measure")
        pending = session.read(members, {"score": score})
        raw = pending.compute().to_pandas()
        assert raw.iloc[:, 0].tolist() == [a1, a2, b1, b2]
        assert raw["score"].tolist() == [2**53 + 1, pd.NA, 2**53 + 1, 2**53]
        assert str(raw["score"].dtype) == "int64[pyarrow]"
        trace = capture_source(monkeypatch, measure_cost=True)
        for descending, expected in (
            (True, [a1, b1, b2, a2]),
            (False, [b2, a1, b1, a2]),
        ):
            result = pending.top(4, by=pending["score"], descending=descending)
            rows = result.to_pandas()
            assert rows.iloc[:, 0].tolist() == expected
            assert pd.isna(rows["score"].iloc[-1])
            assert str(rows["score"].dtype) == "int64[pyarrow]"
        top = pending.top(1, by=pending["score"]).compute()
        assert top.to_pandas().iloc[0].tolist() == [a1, 2**53 + 1]
        assert pending.top(0, by=pending["score"]).to_pandas().empty
        assert trace.returned_rows == [4, 4, 1, 1]
        assert all("ORDER BY" in sql for sql in trace.native_sql)
        trace.save(
            "frame-top-" + backend,
            case.environment,
            {"rows": 4, "top": [4, 4, 1, 0], "score": "nullable int64"},
            {
                "descending_keys": [a1, b1, b2, a2],
                "ascending_keys": [b2, a1, b1, a2],
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
                a1,
                2**53 + 1,
            ]
            assert fixed.top(0, by=fixed["score"]).compute().to_pandas().empty
        assert trace.returned_rows == []
        if backend == "postgres":
            assert isinstance(case.source, TableSourceIR)
            with postgres.connection(admin=True) as admin:
                admin.execute(f"DELETE FROM public.{case.source.table}")
        elif backend == "trino":
            assert isinstance(case.source, TableSourceIR)
            with trino.connection(admin=True, catalog="iceberg") as admin:
                admin.cursor().execute(
                    f"DELETE FROM iceberg.analysis.{case.source.table}"
                ).fetchall()
        if backend in ("postgres", "trino"):
            for n in (0, 1):
                empty = pending.top(n, by=pending["score"]).compute().to_pandas()
                assert empty.empty
                assert str(empty["score"].dtype) == "int64[pyarrow]"
            assert trace.returned_rows == [1, 1]


@pytest.mark.parametrize("backend,profile", source_params(TERMINAL_SOURCE_PROFILES))
def test_filtered_native_read_top_reuses_its_exact_member_scope(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    data = terminal_key_rows(backend, terminal_rows())
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("filtered-native-read-top", timezone="UTC")
        entity = catalog_value(session.catalog, "sales.facts", kind="entity")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="measure")
        score = catalog_value(session.catalog, "sales.facts.big", kind="measure")
        trace = capture_source(monkeypatch)
        selected = session.read(session.members(entity, where=weight >= 1), {"score": score})
        rows = selected.top(4, by=selected["score"]).compute().to_pandas()
        assert rows.iloc[:, 0].tolist() == [terminal_key(backend, "a", row) for row in (1, 2)]
        assert rows["score"].tolist() == [2**53 + 1, pd.NA]
        assert selected.top(0, by=selected["score"]).compute().to_pandas().empty
        empty = session.read(session.members(entity, where=weight < 0), {"score": score})
        assert empty.top(1, by=empty["score"]).compute().to_pandas().empty
        assert trace.returned_rows == [2, 1, 1]


@pytest.mark.parametrize(
    "backend,profile",
    source_params(
        [
            ("duckdb", "table"),
            ("postgres", "table"),
            ("mysql", "innodb-table"),
            ("trino", "iceberg"),
        ]
    ),
)
@pytest.mark.parametrize("n", (0, 1))
def test_native_read_top_preserves_full_input_nonnull_identity_errors(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
    n: int,
) -> None:
    source = terminal_rows()
    extra = (
        "(NULL,'a','x',1,0,1,DATE '2026-07-01')"
        if backend in ("postgres", "trino")
        else "(0,NULL,'x',1,0,1,DATE '2026-07-01')"
    )
    monkeypatch.chdir(tmp_path)
    with source_case(
        backend, profile, tmp_path, monkeypatch, replace(source, values=source.values + "," + extra)
    ) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native-read-top-integrity", timezone="UTC")
        members = session.members(
            catalog_value(session.catalog, "sales.facts", kind="entity"),
            where=catalog_value(session.catalog, "sales.facts.weight", kind="measure") >= 0,
        )
        pending = session.read(
            members, {"score": catalog_value(session.catalog, "sales.facts.big", kind="measure")}
        )
        trace = capture_source(monkeypatch)
        with pytest.raises(MaterializationError) as failure:
            pending.top(n, by=pending["score"]).compute()
        assert failure.value.expected is not None
        assert "identity" in failure.value.expected
        assert "non-null" in failure.value.expected
        assert trace.returned_rows == [1]


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
    source = terminal_rows()
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
            catalog_value(session.catalog, "sales.facts", kind="entity"),
            where=catalog_value(session.catalog, "sales.facts.weight", kind="measure") >= 0,
        )
        pending = session.read(
            members,
            {
                "score": catalog_value(session.catalog, "sales.facts.weight", kind="measure"),
                "invalid": catalog_value(session.catalog, "sales.facts.big", kind="measure"),
            },
        )
        trace = capture_source(monkeypatch)
        result = pending.top(n, by=pending["score"]).compute().to_pandas()
        assert len(result) == n
        if n:
            assert result["score"].tolist() == [1]
            assert result["invalid"].tolist() == [1.0]
        if backend == "postgres":
            # Float payloads keep PostgreSQL's existing complete-read boundary.
            assert trace.returned_rows and all(count == 2 for count in trace.returned_rows)
        else:
            assert trace.returned_rows == [1]


@pytest.mark.parametrize("backend,profile", source_params([("mysql", "innodb-table")]))
def test_native_read_top_keeps_byte_identity_on_case_insensitive_columns(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    source = terminal_rows()
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
            catalog_value(session.catalog, "sales.facts", kind="entity"),
            where=catalog_value(session.catalog, "sales.facts.weight", kind="measure") >= 0,
        )
        pending = session.read(
            members, {"score": catalog_value(session.catalog, "sales.facts.big", kind="measure")}
        )
        trace = capture_source(monkeypatch)
        rows = pending.top(2, by=pending["score"]).compute().to_pandas()
        assert rows.iloc[:, 0].tolist() == [("A", 1), ("a", 1)]
        assert rows["score"].tolist() == [2**53 + 1, 2**53 + 1]
        top = pending.top(1, by=pending["score"]).compute().to_pandas()
        assert top.iloc[0].tolist() == [("A", 1), 2**53 + 1]
        assert trace.returned_rows == [2, 1]
        requested = mv.coordinates(
            by=(mv.identity(catalog_value(session.catalog, "sales.facts", kind="entity")),),
            keys=((("a", 1),), (("missing", 99),)),
        )
        selected = (
            session.read(
                requested,
                {"score": catalog_value(session.catalog, "sales.facts.big", kind="measure")},
            )
            .compute()
            .to_pandas()
        )
        assert selected.iloc[:, 0].tolist() == [("a", 1), ("missing", 99)]
        assert selected["score"].tolist() == [2**53 + 1, pd.NA]
        entity = catalog_value(session.catalog, "sales.facts", kind="entity")
        total = catalog_value(session.catalog, "sales.total", kind="metric")
        for domain, expected in (
            (session.members(entity), 30),
            (mv.coordinates(by=(mv.identity(entity),), keys=((("A", 1),),)), 10),
            (mv.coordinates(by=(mv.identity(entity),), keys=((("a", 1),),)), 20),
        ):
            result = session.observe({"total": total}, where=mv.in_population(domain))
            assert result.compute().to_pandas()["total"].tolist() == [expected]
        category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
        all_members = session.members(entity)
        mixed = all_members.classify(by=(all_members.axes[0], category))
        assert isinstance(mixed, mv.Population)
        for domain, expected in (
            (mixed, 30),
            (mv.coordinates(by=(mv.identity(entity), category), keys=((("A", 1), None),)), 10),
            (mv.coordinates(by=(mv.identity(entity), category), keys=((("a", 1), None),)), 20),
        ):
            result = session.observe({"total": total}, where=mv.in_population(domain))
            assert result.compute().to_pandas()["total"].tolist() == [expected]

        assert isinstance(case.source, TableSourceIR)
        with mysql.connection(admin=True) as admin, admin.cursor() as cursor:
            cursor.execute(f"UPDATE {case.source.table} SET category='x'")
        native_categories = mv.coordinates(
            by=(mv.identity(entity), category),
            keys=((("A", 1), "x"), (("A", 1), "X"), (("a", 1), "x"), (("a", 1), "X")),
        )
        result = session.observe({"total": total}, where=mv.in_population(native_categories))
        assert result.compute().to_pandas()["total"].tolist() == [30]


def _extended_scores(
    backend: str,
) -> tuple[SourceData, list[tuple[str, int, float | None, Decimal | None]]]:
    rows = [
        ("a", 1, float("nan"), Decimal("9007199254740992.000000000002")),
        ("a", 2, None, None),
        ("b", 1, 100.0, Decimal("9007199254740992.000000000001")),
        ("a", 3, 100.0, Decimal("9007199254740992.000000000002")),
        ("a", 4, inf, Decimal("9007199254740992.000000000000")),
        ("a", 5, -inf, Decimal("-9007199254740992.000000000000")),
        ("b", 2, float("nan"), Decimal("-9007199254740992.000000000001")),
        ("b", 3, -0.0, Decimal("0.000000000000")),
        ("a", 6, 0.0, Decimal("0.000000000000")),
    ]
    if backend == "mysql":
        rows = [
            (key, identity, score if identity not in (4, 5) and score == score else 20.0, amount)
            for key, identity, score, amount in rows
        ]
    float_type = "DOUBLE PRECISION" if backend == "postgres" else "DOUBLE"
    values = []
    for tenant, identity, score, amount in rows:
        if score is None:
            floating = "NULL"
        elif score in (inf, -inf) or score != score:
            token = "Infinity" if score == inf else "-Infinity" if score == -inf else "NaN"
            floating = f"CAST('{token}' AS {float_type})"
        else:
            floating = str(score)
        decimal = "NULL" if amount is None else f"CAST('{amount:f}' AS DECIMAL(38,12))"
        values.append(f"({identity},'{tenant}','x',{decimal},{floating},1,DATE '2026-07-01')")
    data = SourceData(
        f"id BIGINT, tenant VARCHAR(10), category VARCHAR(10), amount DECIMAL(38,12), big {float_type}, weight BIGINT, day DATE",
        ",".join(values),
        "id Int64, tenant String, category String, amount Nullable(Decimal(38,12)), big Nullable(Float64), weight Int64, day Date",
        [
            {
                "id": identity,
                "tenant": tenant,
                "category": "x",
                "amount": amount,
                "big": score,
                "weight": 1,
                "day": date(2026, 7, 1),
            }
            for tenant, identity, score, amount in rows
        ],
    )
    return data, rows


@pytest.mark.parametrize("backend,profile", source_params(TERMINAL_SOURCE_PROFILES))
def test_native_float_and_decimal_read_top_matches_reference_and_bounds_transport(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    data, rows = _extended_scores(backend)
    data = terminal_key_rows(backend, data)
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("extended-read-top", timezone="UTC")
        members = session.members(catalog_value(session.catalog, "sales.facts", kind="entity"))
        keys = tuple(terminal_key(backend, tenant, identity) for tenant, identity, _, _ in rows)
        key_types = (
            ScalarDataType("int64" if backend == "clickhouse" else "string"),
            ScalarDataType("int64"),
        )
        trace = capture_source(monkeypatch)
        for name, index, dtype in (
            ("big", 2, ScalarDataType("float64")),
            ("amount", 3, DecimalDataType(38, 12)),
        ):
            source = session.read(
                members,
                {"score": catalog_value(session.catalog, f"sales.facts.{name}", kind="measure")},
            )
            reference = Result(
                keys,
                {},
                {
                    "score": Column.from_values(
                        dict(zip(keys, (row[index] for row in rows), strict=True))
                    )
                },
            )
            for descending, limit in ((False, 2), (True, 2), (True, len(keys) - 1)):
                result = source.top(limit, by=source["score"], descending=descending)
                expected = reference_top(reference, limit, "score", descending=descending)
                assert_equivalent(
                    expected,
                    run_frame(result, OptimizationPolicy.disabled()),
                    key_types=key_types,
                    value_types={"score": dtype},
                )
                before = len(trace.returned_rows)
                assert_equivalent(
                    expected,
                    run_frame(result, OptimizationPolicy()),
                    key_types=key_types,
                    value_types={"score": dtype},
                )
                if backend == "postgres" and name == "amount":
                    # Native NUMERIC permits NaN, which the complete Decimal
                    # conversion must see even when ranking would exclude it.
                    assert trace.returned_rows[before:] == [len(keys)]
                else:
                    assert trace.returned_rows[before:] == [limit], trace.native_sql[-1]
                    assert "__frame_top_present" in trace.native_sql[-1]
        assert not trace.uploaded_key_rows


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("postgres", "table")])
)
def test_native_read_top_accepts_precision_proven_integer_to_decimal_scores(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
) -> None:
    def exact_decimal_project(files: dict[str, str]) -> SemanticProject:
        model = files["sales/facts.py"]
        declaration = next(
            line for line in model.splitlines() if line.startswith("facts=ms.entity")
        )
        decorator = declaration.removeprefix("facts=").replace("name='facts',", "")
        return semantic_project_factory(
            {
                **files,
                "sales/facts.py": model.replace(
                    declaration,
                    f"@{decorator}\ndef facts(raw):\n"
                    "    return raw.mutate(big=raw.big.cast('decimal(38,12)'))",
                ),
            }
        )

    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, terminal_rows()) as case:
        _project(backend, case, monkeypatch, exact_decimal_project)
        session = mv.session.get_or_create("exact-decimal-cast-top", timezone="UTC")
        members = session.members(catalog_value(session.catalog, "sales.facts", kind="entity"))
        score = catalog_value(session.catalog, "sales.facts.big", kind="measure")
        source = session.read(members, {"score": score})
        top = source.top(1, by=source["score"])
        expected = Result(
            (("a", 1),), {}, {"score": Column.from_values({("a", 1): Decimal(2**53 + 1)})}
        )
        assert_equivalent(expected, run_frame(top, OptimizationPolicy.disabled()))
        trace = capture_source(monkeypatch)
        assert_equivalent(expected, run_frame(top, OptimizationPolicy()))
        assert trace.returned_rows == [1]
        assert "__frame_top_present" in trace.native_sql[0]


def test_native_aggregate_top_consumes_a_direct_decimal_extremum(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    data, rows = _extended_scores("duckdb")
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        _project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native-extremum-top", timezone="UTC")
        tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="dimension")
        measure = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        query = session.observe({"score": measure.min(time=None)}, by=(tenant,))
        requested = query.top(1, by=query["score"], descending=False)
        expected = Result(
            (("b",),),
            {},
            {
                "score": Column.from_values(
                    {
                        ("b",): min(
                            item[3] for item in rows if item[0] == "b" and item[3] is not None
                        )
                    }
                )
            },
        )
        assert_equivalent(expected, run_frame(requested, OptimizationPolicy.disabled()))
        trace = capture_source(monkeypatch)
        assert_equivalent(expected, run_frame(requested, OptimizationPolicy()))
        assert trace.returned_rows == [1]
        assert "__frame_top_present" in trace.native_sql[0]


@pytest.mark.runtime
def test_postgres_decimal_top_retains_excluded_native_conversion_failure(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = terminal_rows()
    data = replace(
        fixture,
        columns=fixture.columns.replace("amount BIGINT", "amount NUMERIC(18,2)"),
        values=fixture.values + ",(99,'bad','x','NaN',0,0,DATE '2026-07-01')",
    )
    monkeypatch.chdir(tmp_path)
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        _project("postgres", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("postgres-decimal-receipt", timezone="UTC")
        entity = catalog_value(session.catalog, "sales.facts", kind="entity")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        frame = session.read(session.members(entity), {"score": amount})
        query = frame.top(0, by=frame["score"])
        control = run_frame(query, OptimizationPolicy.disabled())
        assert isinstance(control, ObservedFailure), control
        assert control.stage == "source_conversion" and "NaN" in (control.received or "")
        trace = capture_source(monkeypatch)
        assert run_frame(query, OptimizationPolicy()) == control
        assert trace.native_sql and all(
            "__frame_top_present" not in sql for sql in trace.native_sql
        )


@pytest.fixture
def terminal_boundary_session(
    request: pytest.FixtureRequest,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[mv.Session]:
    monkeypatch.chdir(tmp_path)
    day = "infinity" if getattr(request, "param", False) else "2026-07-01"
    initial = terminal_rows()
    data = replace(
        initial,
        columns=initial.columns.replace("amount BIGINT", "amount DOUBLE"),
        values=f"(1,'a','x',10,7,1,DATE '{day}'),(2,'a','y',1,2,1,DATE '2026-07-02')",
    )
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        _project("duckdb", case, monkeypatch, semantic_project_factory)
        yield mv.session.get_or_create("terminal-boundary", timezone="UTC")


@pytest.mark.parametrize("terminal_boundary_session", (True,), indirect=True)
@pytest.mark.parametrize("terminal", ("float-top", "integer-top", "fix", "reduce", "over"))
def test_terminal_keeps_temporal_conversion_of_full_input(
    terminal_boundary_session: mv.Session, terminal: str
) -> None:
    session = terminal_boundary_session
    entity = catalog_value(session.catalog, "sales.facts", kind="entity")
    clock = catalog_value(session.catalog, "sales.facts.day", kind="time_dimension")
    field = "big" if terminal == "integer-top" else "amount"
    score = catalog_value(session.catalog, f"sales.facts.{field}", kind="measure")
    read = session.read(session.members(entity), {"clock": clock, "score": score})
    if terminal.endswith("top"):
        query = read.top(0, by=read["score"])
    elif terminal == "fix":
        query = read.fix(read.axes[0], ("a", 2))
    elif terminal == "reduce":
        query = read.reduce({"n": mv.row_count()})
    else:
        query = read.select({"n": read.over(mv.row_count())})
    original = run_frame(query, OptimizationPolicy.disabled())
    actual = run_frame(query, OptimizationPolicy.default())
    assert isinstance(original, ObservedFailure)
    assert actual == original
    assert original.stage == "source_conversion"
    assert "out of range" in (original.received or "")


@pytest.mark.parametrize("field", ("amount", "big"), ids=("floating-score", "integer-score"))
def test_native_top_leaves_oversized_limits_to_the_frame(
    terminal_boundary_session: mv.Session, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    session = terminal_boundary_session
    entity = catalog_value(session.catalog, "sales.facts", kind="entity")
    score = catalog_value(session.catalog, f"sales.facts.{field}", kind="measure")
    source = session.read(session.members(entity), {"score": score})
    query = source.top(2**63, by=source["score"], descending=True)
    trace = capture_source(monkeypatch)
    original = run_frame(query, OptimizationPolicy.disabled())
    trace.native_sql.clear()
    actual = run_frame(query, OptimizationPolicy.default())
    assert not isinstance(actual, ObservedFailure)
    assert actual == original
    assert len(actual.keys) == 2
    assert not any("__frame_top" in sql for sql in trace.native_sql)


@pytest.mark.runtime
def test_clickhouse_frame_terminals_use_completed_string_keys(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = terminal_rows()
    data = replace(
        source,
        rows=[],
        clickhouse_columns=source.clickhouse_columns.replace("amount Int64", "amount Float64"),
    )
    monkeypatch.chdir(tmp_path)
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        with clickhouse.connection(admin=True) as admin:
            admin.command(
                f"INSERT INTO qualification.{case.source.table} VALUES "
                "(1,unhex('FF'),'invalid',1,1,1,'2026-07-01'),"
                "(2,'z','valid',1,1,1,'2026-07-01')"
            )
        _project("clickhouse", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("clickhouse-completed-string-keys", timezone="UTC")
        entity = catalog_value(session.catalog, "sales.facts", kind="entity")
        pending = session.read(
            session.members(entity),
            {
                name: catalog_value(session.catalog, f"sales.facts.{name}", kind="measure")
                for name in ("big", "amount")
            },
        )
        trace = capture_source(monkeypatch)
        for score in ("big", "amount"):
            result = pending.top(1, by=pending[score]).to_pandas()
            assert result.iloc[:, 0].tolist() == [("ff", 1)]
            assert trace.returned_rows[-1] == 2
        restricted = pending.fix(pending.axes[0], ("ff", 1)).to_pandas()
        assert restricted["amount"].tolist() == [1.0]
        assert "__frame_top_present" not in " ".join(trace.native_sql)
