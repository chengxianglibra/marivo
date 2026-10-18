"""Native remote joins retain complete endpoint presence and checked finishing."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import ibis
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.query_key_fusion import _outer_join_value
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_source_query_fusion import _execute
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import capture_source

CASES = [("trino", "iceberg", 1), ("clickhouse", "mergetree", 0), ("clickhouse", "mergetree", 1)]


def _data() -> SourceData:
    rows: list[dict[str, object]] = [
        {
            "id": index,
            "tenant": side,
            "category": category,
            "amount": value,
            "big": 1,
            "weight": 1,
            "day": date(2026, 7, 1),
        }
        for index, (side, category, value) in enumerate(
            (
                ("left", "A", None),
                ("right", "A", 0),
                ("left", "B", 5),
                ("right", "C", None),
                ("right", "", 1),
                ("left", None, 2),
                ("right", None, 3),
            ),
            1,
        )
    ]
    return SourceData(
        "id BIGINT NOT NULL, tenant VARCHAR(16) NOT NULL, category VARCHAR(16), amount BIGINT, big BIGINT, weight BIGINT, day DATE",
        ",".join(
            f"({row['id']},'{row['tenant']}',"
            + ("NULL" if row["category"] is None else f"'{row['category']}'")
            + ","
            + ("NULL" if row["amount"] is None else str(row["amount"]))
            + ",1,1,DATE '2026-07-01')"
            for row in rows
        ),
        "id Int64, tenant String, category Nullable(String), amount Nullable(Int64), big Int64, weight Int64, day Date",
        rows,
    )


def test_clickhouse_outer_input_declares_nullable_structure_even_for_literals() -> None:
    table = ibis.table({"key": ibis.dtype("string").copy(nullable=False)}, name="endpoint")
    sql = ibis.to_sql(
        table.select(
            key=_outer_join_value(table.key, "clickhouse"),
            present=_outer_join_value(ibis.literal(True), "clickhouse"),
        ),
        dialect="clickhouse",
    ).upper()
    assert "NULLABLE(STRING)" in sql
    assert "NULLABLE(BOOL)" in sql


@contextmanager
def _join_mode(backend: str, join_nulls: int) -> Iterator[None]:
    if backend != "clickhouse":
        yield
        return
    from tests.datasource.environment import clickhouse_analysis as ch

    with ch.connection(admin=True) as admin:
        with ch.connection() as reader:
            row = reader.query("SELECT getSetting('join_use_nulls')").first_row
            assert row is not None
            original = int(row[0])
        admin.command(f"ALTER USER analysis_reader SETTINGS join_use_nulls={join_nulls}")
        try:
            with ch.connection() as reader:
                assert reader.query("SELECT getSetting('join_use_nulls')").first_row == (
                    join_nulls,
                )
            yield
        finally:
            admin.command(f"ALTER USER analysis_reader SETTINGS join_use_nulls={original}")


@pytest.mark.parametrize(
    "backend,profile,join_nulls",
    source_params(CASES),
)
@pytest.mark.parametrize("operation", ("align", "compare", "broadcast"))
def test_remote_string_endpoints_join_once_with_independent_presence(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
    join_nulls: int,
    operation: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        source_case(backend, profile, tmp_path, monkeypatch, _data()) as case,
        _join_mode(backend, join_nulls),
    ):
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-relations", report_timezone="UTC")
        category = session.catalog.fields.get("sales.facts.category")
        tenant = session.catalog.fields.get("sales.facts.tenant")
        amount = session.catalog.fields.get("sales.facts.amount")
        left = session.observe(
            {"v": amount.sum(empty="null").where(tenant == "left")}, by=(category,)
        )
        right = session.observe(
            {"v": amount.sum(empty="null").where(tenant == "right")}, by=(category,)
        )
        if operation == "broadcast":
            total = session.observe({"total": amount.count()})
            query = left.select({"reference": mv.broadcast(total["total"], to=left)})
            missing = total.where(total["total"] < 0)
            invalid = left.select({"reference": mv.broadcast(missing["total"], to=left)})
            invalid = invalid.top(0, by=invalid["reference"])
            expected_error = "exactly one zero-axis reference key"
        else:
            query = (
                left.align(right, how="outer")
                if operation == "align"
                else left.compare(right, how="outer", zero_denominator="error")
            )
            invalid = left.align(right)
            invalid = invalid.top(0, by=invalid["left.v"])
            expected_error = "equal complete endpoint key images"
        expected = _execute(session, query, fuse=False)
        trace = capture_source(monkeypatch)
        actual = _execute(session, query, fuse=True)
        pd.testing.assert_frame_equal(actual.table, expected.table, check_exact=True)
        assert trace.returned_rows == [3 if operation == "broadcast" else 5]
        assert trace.uploaded_key_rows == []
        with pytest.raises(AnalysisError, match=expected_error):
            _execute(session, invalid, fuse=True)
        assert len(trace.returned_rows) == 2
        assert "JOIN" in trace.native_sql[0].upper()
        trace.save(
            f"query-relations-{backend}-{join_nulls}-{operation}",
            case.environment,
            {"rows": 7, "join_use_nulls": join_nulls},
            {"operation": operation, "checked_failure": expected_error},
            None,
            (case.session,),
        )


@pytest.mark.parametrize("backend,profile,join_nulls", source_params(CASES))
@pytest.mark.parametrize("empty", ("zero", "null"))
def test_remote_shared_consumers_keep_separate_group_images_and_parent_checks(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
    join_nulls: int,
    empty: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        source_case(backend, profile, tmp_path, monkeypatch, _data()) as case,
        _join_mode(backend, join_nulls),
    ):
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-shared-groups", report_timezone="UTC")
        category = session.catalog.fields.get("sales.facts.category")
        amount = session.catalog.fields.get("sales.facts.amount")
        producer = session.observe({"n": amount.count()}, by=(category,))
        selected = producer.where(producer["n"] >= 1).population()
        condition = mv.in_population(selected)
        query = session.observe(
            {
                "a": amount.sum(empty=empty).where(condition).where(category == "A"),
                "c": amount.sum(empty=empty).where(~condition).where(category == "C"),
                "null_key": amount.sum(empty="null").where(condition).where(category.isnull()),
            },
            by=(category,),
        )
        expected = _execute(session, query, fuse=False)
        trace = capture_source(monkeypatch)
        actual = _execute(session, query, fuse=True)
        expected_frame = expected.table.copy()
        if backend == "trino":
            # Joined missing cells cross Trino's native pandas float carrier.
            floating = ("value.0", "value.2") + (("value.1",) if empty == "zero" else ())
            for slot in floating:
                expected_frame[slot] = expected_frame[slot].astype("double[pyarrow]")
        pd.testing.assert_frame_equal(actual.table, expected_frame, check_exact=True)
        assert trace.returned_rows == [3]
        assert trace.uploaded_key_rows == []
        if empty == "zero":
            parent = session.observe({"n": amount.sum(nulls="error")}, by=(category,))
            checked = parent.top(0, by=parent["n"]).population()
            failed = session.observe(
                {
                    "a": amount.count().where(mv.in_population(checked)),
                    "b": amount.count().where(~mv.in_population(checked)),
                },
                by=(category,),
            )
            with pytest.raises(SemanticExpressionError, match="non-NA"):
                _execute(session, failed, fuse=True)
        trace.save(
            f"query-shared-{backend}-{join_nulls}-{empty}",
            case.environment,
            {"rows": 7, "join_use_nulls": join_nulls},
            {
                "empty": empty,
                "native_columns": {
                    slot: {
                        "dtype": str(actual.table[slot].dtype),
                        "values": actual.table[slot]
                        .astype(object)
                        .where(actual.table[slot].notna(), None)
                        .tolist(),
                    }
                    for slot in actual.table.columns
                },
            },
            None,
            (case.session,),
        )


@pytest.mark.parametrize("backend,profile,join_nulls", source_params(CASES))
def test_remote_multiple_shared_parameters_are_replaced_atomically(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
    join_nulls: int,
) -> None:
    monkeypatch.chdir(tmp_path)
    with (
        source_case(backend, profile, tmp_path, monkeypatch, _data()) as case,
        _join_mode(backend, join_nulls),
    ):
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("remote-parameter-block", report_timezone="UTC")
        category = session.catalog.fields.get("sales.facts.category")
        amount = session.catalog.fields.get("sales.facts.amount")
        producer = session.observe({"n": amount.count()}, by=(category,))
        left = producer.where(producer["n"] >= 1).population()
        right = producer.where(producer["n"] <= 1).population()
        query = session.observe(
            {"n": amount.count().where(mv.in_population(left) & ~mv.in_population(right))}
        )
        expected = _execute(session, query, fuse=False)
        trace = capture_source(monkeypatch)
        actual = _execute(session, query, fuse=True)
        pd.testing.assert_frame_equal(actual.table, expected.table, check_exact=True)
        assert actual.table["value.0"].tolist() == [2]
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []
        trace.save(
            f"query-parameter-block-{backend}-{join_nulls}",
            case.environment,
            {"rows": 7, "join_use_nulls": join_nulls},
            {"count": 2},
            None,
            (case.session,),
        )
