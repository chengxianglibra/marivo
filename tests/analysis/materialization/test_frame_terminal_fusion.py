"""Native Frame terminal completion preserves rows, validity, keys and effects."""

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.materialization.frame_terminal_align import _key_equality
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.datasource.spi import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.terminal_fusion_fixtures import (
    TERMINAL_SOURCE_PROFILES,
    terminal_key,
    terminal_key_rows,
    terminal_rows,
)
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import source_case, source_params
from tests.formal.reference.equivalence import assert_equivalent, run_frame
from tests.formal.reference.model import Column, Key, Obligation, Result
from tests.shared_fixtures import catalog_value
from tests.support.source_trace import SourceTrace, capture_source


@pytest.fixture(params=source_params([case[0] for case in TERMINAL_SOURCE_PROFILES]))
def pending(
    request: pytest.FixtureRequest,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[str, mv.AnalysisFrame, mv.AnalysisFrame, mv.AnalysisFrame, SourceTrace]]:
    backend = request.param
    profile = dict(TERMINAL_SOURCE_PROFILES)[backend]
    data = terminal_key_rows(backend, terminal_rows())
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("native-frame-terminal", timezone="UTC")
        entity = catalog_value(session.catalog, "sales.facts", kind="entity")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        big = catalog_value(session.catalog, "sales.facts.big", kind="measure")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="measure")
        left = session.read(session.members(entity, where=weight >= 1), {"n": amount})
        right = session.read(session.members(entity, where=weight < 1), {"n": big})
        empty = session.read(session.members(entity, where=weight > 1), {"n": amount})
        yield backend, left, right, empty, capture_source(monkeypatch)


def _expect(
    frame: mv.AnalysisFrame,
    result: Result,
    trace: SourceTrace,
    *,
    native_reads: int = 1,
    sql: str,
) -> None:
    assert_equivalent(result, run_frame(frame, OptimizationPolicy.disabled()))
    calls, owners = len(trace.native_sql), len(trace.owners)
    assert_equivalent(result, run_frame(frame, OptimizationPolicy()))
    emitted = trace.native_sql[calls:]
    assert len(emitted) == native_reads, emitted
    assert any(sql in statement.upper() for statement in emitted), emitted
    purposes = [
        submission.purpose for owner in trace.owners[owners:] for submission in owner.submissions
    ]
    assert len(purposes) == native_reads
    if native_reads == 1 and sql != "__FRAME_TOP_PRESENT":
        assert purposes[0].startswith("query.frame:")
    assert not trace.uploaded_key_rows


def test_terminal_fix_reduce_and_over_preserve_complete_scope(
    pending: tuple[str, mv.AnalysisFrame, mv.AnalysisFrame, mv.AnalysisFrame, SourceTrace],
) -> None:
    backend, left, _, empty, trace = pending
    first = terminal_key(backend, "a", 1)
    present: dict[Key, int] = {first: 1, terminal_key(backend, "a", 2): 1}

    _expect(
        left.fix(left.axes[0], first),
        Result(((),), {}, {"n": Column.from_values({(): 1})}),
        trace,
        sql="WHERE",
    )
    _expect(
        left.reduce({"rows": mv.row_count(), "amount": left["n"].sum()}, by=left.axes),
        Result(
            tuple(present),
            {},
            {"rows": Column.from_values(present), "amount": Column.from_values(present)},
        ),
        trace,
        sql="GROUP BY",
    )
    _expect(
        empty.reduce({"rows": mv.row_count()}),
        Result(((),), {}, {"rows": Column.from_values({(): 0})}),
        trace,
        sql="COUNT",
    )
    for frame, keys in ((left, tuple(present)), (empty, ())):
        _expect(
            frame.select({"rows": frame.over(mv.row_count())}),
            Result(keys, {}, {"rows": Column.from_values(dict.fromkeys(keys, len(keys)))}),
            trace,
            sql="OVER",
        )


def test_same_source_align_preserves_na_presence_and_only_current_rows(
    pending: tuple[str, mv.AnalysisFrame, mv.AnalysisFrame, mv.AnalysisFrame, SourceTrace],
) -> None:
    backend, left, right, _, trace = pending
    aligned = left.align(right, on=((left.axes[0], right.axes[0]),))
    keys = tuple(terminal_key(backend, tenant, row) for tenant in ("a", "b") for row in (1, 2))
    left_present = frozenset(keys[:2])
    right_present = frozenset(keys[2:])
    expected = Result(
        keys,
        {},
        {
            "left.n": Column.from_values(dict(zip(keys, (1, 1, None, None), strict=True))),
            "right.n": Column.from_values(
                dict(zip(keys, (None, None, 2**53 + 1, 2**53), strict=True))
            ),
        },
        presence={"left_present": left_present, "right_present": right_present},
    )
    _expect(
        aligned,
        expected,
        trace,
        native_reads=2 if backend == "mysql" else 1,
        sql="SELECT" if backend == "mysql" else "FULL OUTER JOIN",
    )
    _expect(
        aligned.reduce({"count": mv.row_count()}),
        Result(((),), {}, {"count": Column.from_values({(): 4})}),
        trace,
        native_reads=2 if backend == "mysql" else 1,
        sql="SELECT" if backend == "mysql" else "FULL OUTER JOIN",
    )
    _expect(
        aligned.select({"n": aligned.over(mv.row_count())}),
        Result(
            keys,
            {},
            {"n": Column.from_values(dict.fromkeys(keys, 4))},
            presence={"left_present": left_present, "right_present": right_present},
        ),
        trace,
        native_reads=2 if backend == "mysql" else 1,
        sql="SELECT" if backend == "mysql" else "FULL OUTER JOIN",
    )


def test_two_pending_align_operands_keep_failure_witness_order_and_empty_demand(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = terminal_rows()
    data = replace(
        source,
        columns=source.columns.replace("amount BIGINT", "amount DOUBLE"),
        values=source.values
        + ",(NULL,'a','x',1,0,1,DATE '2026-07-01')"
        + ",(NULL,'b','x',2,1,0,DATE '2026-07-01')",
    )
    monkeypatch.chdir(tmp_path)
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        _project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("aligned-operand-check-order", timezone="UTC")
        entity = catalog_value(session.catalog, "sales.facts", kind="entity")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="measure")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        a = session.read(session.members(entity, where=weight >= 1), {"n": amount})
        b = session.read(session.members(entity, where=weight < 1), {"n": amount})
        trace = capture_source(monkeypatch)
        for left, right, witness in ((a, b, "a"), (b, a, "b")):
            aligned = left.align(right, on=((left.axes[0], right.axes[0]),))
            for query in (aligned, aligned.reduce({"n": mv.row_count()})):
                _expect(
                    query,
                    Result(
                        (), {}, {}, frozenset({Obligation("missing_identity", (witness, None))})
                    ),
                    trace,
                    sql="FULL OUTER JOIN",
                )
        for query, expected in (
            (a.fix(a.axes[0], ("a", 1)), "a"),
            (b.fix(b.axes[0], ("missing", -1)), "b"),
        ):
            _expect(
                query,
                Result((), {}, {}, frozenset({Obligation("missing_identity", (expected, None))})),
                trace,
                sql="WHERE",
            )
        _expect(
            a.top(0, by=a["n"]),
            Result((), {}, {}, frozenset({Obligation("missing_identity", ("a", None))})),
            trace,
            sql="__FRAME_TOP_PRESENT",
        )


def test_fixed_and_protected_frame_boundaries_keep_their_own_rows(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case("duckdb", "table", tmp_path, monkeypatch, terminal_rows()) as case:
        _project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("terminal-source-boundaries", timezone="UTC")
        entity = catalog_value(session.catalog, "sales.facts", kind="entity")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="measure")
        base = session.read(session.members(entity), {"n": amount})
        restricted = session.read(session.members(entity, where=weight < 1), {"n": amount})
        memory = base.compute()
        saved = memory.persist()
        stored = session.persisted_frame(saved.artifact_id)

        trace = capture_source(monkeypatch)
        for fixed in (memory, stored):
            assert fixed.reduce({"total": mv.row_count()}).to_pandas()["total"].tolist() == [4]
            assert fixed.fix(fixed.axes[0], ("a", 1)).to_pandas()["n"].tolist() == [1]
            assert (
                fixed.select({"rows": fixed.over(mv.row_count())}).to_pandas()["rows"].tolist()
                == [4] * 4
            )
        assert trace.returned_rows == []
        for fixed in (memory, stored):
            aligned = fixed.align(restricted, on=((fixed.axes[0], restricted.axes[0]),))
            assert aligned.to_pandas()["right_present"].tolist() == [False, False, True, True]
        assert trace.returned_rows == [2, 2]
        assert not trace.uploaded_key_rows
        assert all("FULL OUTER JOIN" not in sql.upper() for sql in trace.native_sql)

        windowed = base.select({"rows": base.over(mv.row_count())})
        both = windowed.align(base, on=((windowed.axes[0], base.axes[0]),)).to_pandas()
        assert both["left.rows"].tolist() == [4] * 4
        assert both["right.n"].tolist() == [1] * 4
        assert trace.returned_rows == [2, 2, 4]
        assert "OVER (" not in trace.native_sql[-1].upper()

        protected = session.population_from(windowed, on=((windowed.axes[0], entity),))
        before = len(trace.native_sql)
        result = session.read(protected, {"n": amount}).to_pandas()
        assert result["n"].tolist() == [1] * 4
        assert all("OVER (" not in sql.upper() for sql in trace.native_sql[before:])


def test_align_completes_fresh_one_query_receipts_in_each_invocation(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case("duckdb", "table", tmp_path, monkeypatch, terminal_rows()) as case:
        _project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("fresh-alignment-sources", timezone="UTC")
        entity = catalog_value(session.catalog, "sales.facts", kind="entity")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="measure")
        left = session.read(session.members(entity, where=weight >= 1), {"n": amount})
        right = session.read(session.members(entity, where=weight < 1), {"n": amount})
        expected_scopes = {
            scope
            for frame in (left, right)
            for read in frame.persist().source_reads
            for scope in read.scopes
        }
        source = left.align(right, on=((left.axes[0], right.axes[0]),))
        trace = capture_source(monkeypatch)
        first, second = (source.persist() for _ in range(2))
        (left_read,), (right_read,) = first.source_reads, second.source_reads
        assert set(left_read.scopes) == set(right_read.scopes) == expected_scopes
        assert len(expected_scopes) == 4
        assert left_read.read_id != right_read.read_id
        assert trace.returned_rows == [4, 4]
        assert len(trace.owners) == 2 and trace.owners[0] is not trace.owners[1]
        assert not trace.uploaded_key_rows


@pytest.mark.runtime
def test_clickhouse_native_terminal_preserves_int64_wrap_and_zero_share(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = (("a", 1, 0), ("a", 2, 0), ("b", 1, 2**63 - 1), ("b", 2, 1), ("c", 1, None))
    data = replace(
        terminal_rows(),
        clickhouse_columns=terminal_rows().clickhouse_columns.replace(
            "amount Int64", "amount Nullable(Int64)"
        ),
        rows=[
            {
                "id": row,
                "tenant": tenant,
                "category": "x",
                "amount": value,
                "big": 0,
                "weight": 1,
                "day": date(2026, 7, 1),
            }
            for tenant, row, value in values
        ],
    )
    monkeypatch.chdir(tmp_path)
    data = terminal_key_rows("clickhouse", data)
    with source_case("clickhouse", "mergetree", tmp_path, monkeypatch, data) as case:
        _project("clickhouse", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("clickhouse-exact-frame-sum", timezone="UTC")
        entity = catalog_value(session.catalog, "sales.facts", kind="entity")
        tenant = catalog_value(session.catalog, "sales.facts.tenant", kind="dimension")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        trace = capture_source(monkeypatch)
        for label, total in (("a", 0), ("b", -(2**63)), ("c", None), ("d", 0)):
            frame = session.read(session.members(entity, where=tenant == ord(label)), {"n": amount})
            _expect(
                frame.reduce({"total": frame["n"].sum()}),
                Result(((),), {}, {"total": Column.from_values({(): total})}),
                trace,
                sql="SUM",
            )
            present = {terminal_key("clickhouse", t, i): v for t, i, v in values if t == label}
            _expect(
                frame.select({"share": frame["n"].share(along=frame.axes)}),
                Result(
                    tuple(present),
                    {},
                    {
                        "share": Column.from_values(
                            {
                                key: None
                                if value is None or not total
                                else float(value) / float(total)
                                for key, value in present.items()
                            }
                        )
                    },
                ),
                trace,
                sql="OVER",
            )


def test_nested_source_alignment_retains_left_first_and_prefixed_presence(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case("duckdb", "table", tmp_path, monkeypatch, terminal_rows()) as case:
        _project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("nested-native-align", timezone="UTC")
        entity = catalog_value(session.catalog, "sales.facts", kind="entity")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="measure")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        a = session.read(session.members(entity, where=weight >= 1), {"amount": amount})
        b = session.read(session.members(entity, where=weight < 1), {"amount": amount})
        missing = session.read(session.members(entity, where=weight >= 2), {"amount": amount})
        inner = a.align(missing, on=((a.axes[0], missing.axes[0]),))
        outer = b.align(inner, on=((b.axes[0], inner.axes[0]),))
        keys = (("b", 1), ("b", 2), ("a", 1), ("a", 2))
        _expect(
            outer,
            Result(
                keys,
                {},
                {
                    "left.amount": Column.from_values(dict.fromkeys(keys[:2], 1)),
                    "right.left.amount": Column.from_values(dict.fromkeys(keys[2:], 1)),
                    "right.right.amount": Column.from_values({}),
                },
                presence={
                    "left_present": frozenset(keys[:2]),
                    "right_present": frozenset(keys[2:]),
                    "right.left_present": frozenset(keys[2:]),
                    "right.right_present": frozenset(),
                },
            ),
            capture_source(monkeypatch),
            sql="FULL OUTER JOIN",
        )


@pytest.mark.runtime
def test_postgres_native_full_join_distinguishes_nullable_keys_from_real_filler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = replace(
        terminal_rows(),
        values=",".join(
            f"({index},'a',{token},1,1,1,DATE '2026-07-01')"
            for index, token in ((1, "NULL"), (2, "''"), (3, "''"), (4, "NULL"), (5, "'x'"))
        ),
    )
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        table = case.session.bind(case.source, source_identity="terminal-pg-key-validity").relation
        left = table.filter(table.id <= 2)
        right = table.filter(table.id > 2)
        output = left.outer_join(
            right, _key_equality(left.category, right.category, "postgres", nullable=True)
        ).select(left_id=left.id, right_id=right.id)
        rows = case.session.collect_bounded(output, purpose="terminal-pg-key-validity", max_rows=5)
        assert sorted(
            (int(left_id) if pd.notna(left_id) else 0, int(right_id))
            for left_id, right_id in rows.itertuples(index=False, name=None)
        ) == [(0, 5), (1, 4), (2, 3)]


def test_fix_accepts_na_on_a_nullable_pending_ordinary_axis(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = terminal_rows()
    data = replace(fixture, values=fixture.values + ",(99,'a',NULL,8,0,0,DATE '2026-07-01')")
    monkeypatch.chdir(tmp_path)
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        _project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("nullable-frame-fix", timezone="UTC")
        category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        query = session.observe({"amount": amount.min(time=None)}, by=(category,))
        _expect(
            query.fix(query.axes[0], None),
            Result(((),), {}, {"amount": Column.from_values({(): 8})}),
            capture_source(monkeypatch),
            sql="WHERE",
        )


def test_fix_uses_canonical_int64_key_even_when_the_storage_is_narrower(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = terminal_rows()
    data = replace(fixture, columns=fixture.columns.replace("id BIGINT", "id INTEGER"))
    monkeypatch.chdir(tmp_path)
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        _project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("canonical-int64-key", timezone="UTC")
        entity = catalog_value(session.catalog, "sales.facts", kind="entity")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="measure")
        source = session.read(session.members(entity), {"n": amount})
        _expect(
            source.fix(source.axes[0], ("a", 2**40)),
            Result((), {}, {"n": Column.from_values({})}),
            capture_source(monkeypatch),
            sql="IS NOT DISTINCT FROM",
        )


def test_align_keeps_two_statement_dependent_view_reads(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    initial = terminal_rows()
    data = replace(
        initial,
        columns=initial.columns.replace("id BIGINT", "id VARCHAR(10)"),
        values="('one','a','x',4,0,1,DATE '2026-07-01'),('two','a','y',8,0,1,DATE '2026-07-02')",
    )
    with source_case("duckdb", "view", tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        case.session.close()
        name = case.source.table
        parent = name.removesuffix("_view")
        assert parent != name
        backend = ibis.duckdb.connect(tmp_path / "source.duckdb")
        try:
            backend.raw_sql(
                f"CREATE OR REPLACE VIEW {name} AS SELECT * REPLACE "
                f"(CAST(current_timestamp AS VARCHAR) AS category) FROM {parent}"
            )
        finally:
            backend.disconnect()
        _project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("terminal-view", timezone="UTC")
        root = catalog_value(session.catalog, "sales.facts", kind="entity")
        category = catalog_value(session.catalog, "sales.facts.category", kind="dimension")
        left = session.read(session.members(root), {"time": category})
        right = session.read(session.members(root), {"time": category})
        joined = left.align(right, on=((left.axes[0], right.axes[0]),))
        trace = capture_source(monkeypatch)
        rows = joined.compute().to_pandas()
        assert len(rows) == 2
        assert (rows["left.time"] != rows["right.time"]).all()
        assert len(trace.native_sql) == 2
        assert not any("FULL OUTER JOIN" in sql.upper() for sql in trace.native_sql)
