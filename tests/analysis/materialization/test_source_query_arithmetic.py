"""Checked native Query formulas and Columns keep demanded failures at source."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.errors import MaterializationError
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_source_query_fusion import _execute
from tests.analysis.materialization.test_source_query_numeric import _data
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import capture_source

CASES = [("duckdb", "table"), ("trino", "iceberg"), ("clickhouse", "mergetree")]


def _case_data(backend: str, boundary: str) -> SourceData:
    data = _data(boundary)
    return (
        replace(data, values=data.values.replace("nan()", "CAST('NaN' AS DOUBLE)"))
        if backend == "duckdb"
        else data
    )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("operation", ("formula", "columns", "na_zero"))
def test_query_arithmetic_transports_only_its_selected_completed_values(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
    operation: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        _case_data(backend, "ordinary" if operation == "na_zero" else "strict"),
    ) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("source-query-arithmetic", report_timezone="UTC")
        amount = session.catalog.fields.get("sales.facts.amount")
        big = session.catalog.fields.get("sales.facts.big")
        weight = session.catalog.fields.get("sales.facts.weight")
        category = session.catalog.fields.get("sales.facts.category")
        if operation == "formula":
            source = session.observe(
                {
                    "total": big.sum(empty="zero"),
                    "n": big.count(),
                    "sparse": big.sum(empty="zero") + big.count().where(category == "A"),
                },
                by=(category,),
            )
            source = source.where(source["sparse"] > 40)
            query = source.top(1, by=source["sparse"])
            expected_rows = 1
        else:
            source = session.read(
                session.members(ms.ref.entity("sales.facts")),
                {"amount": amount, "big": big, "weight": weight},
            )
            if operation == "na_zero":
                source = source.where(source["amount"].is_null())
                query = source.select(
                    {
                        "value": source["amount"].divide(
                            source["big"] - source["big"], zero_denominator="error"
                        )
                    }
                )
            else:
                source = source.where(source["big"] + 5 > 20)
                source = source.select(
                    {
                        "value": (source["big"] * 2 - source["weight"]).divide(
                            source["weight"] + 1, zero_denominator="error"
                        )
                    }
                )
                query = source.top(2, by=-(source["value"] + 1))
            expected_rows = 2
        expected = _execute(session, query, fuse=False)
        trace = capture_source(monkeypatch)
        actual = _execute(session, query, fuse=True)
        pd.testing.assert_frame_equal(actual.table, expected.table, check_exact=True)
        assert trace.returned_rows == [expected_rows]
        assert trace.uploaded_key_rows == []
        if operation == "columns":
            assert actual.table["value.0"].tolist() == [19.5, 29.5]
        elif operation == "na_zero":
            assert actual.table["value.0"].isna().all()
        trace.save(
            f"query-arithmetic-{backend}-{operation}",
            case.environment,
            {"rows": 6},
            {"operation": operation},
            None,
            (case.session,),
        )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("boundary", ("overflow", "zero", "formula_zero"))
def test_checked_query_arithmetic_fails_before_empty_top(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
    boundary: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(
        backend,
        profile,
        tmp_path,
        monkeypatch,
        _case_data(backend, "overflow" if boundary == "overflow" else "strict"),
    ) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("source-query-checks", report_timezone="UTC")
        big = session.catalog.fields.get("sales.facts.big")
        weight = session.catalog.fields.get("sales.facts.weight")
        category = session.catalog.fields.get("sales.facts.category")
        if boundary == "formula_zero":
            source = session.observe(
                {
                    "value": big.sum().divide(
                        big.count().where(category == "missing"), zero_denominator="error"
                    )
                },
                by=(category,),
            )
        else:
            source = session.read(
                session.members(ms.ref.entity("sales.facts")), {"big": big, "weight": weight}
            )
            source = source.select(
                {
                    "value": source["big"] + 1
                    if boundary == "overflow"
                    else source["big"].divide(source["weight"] - 1, zero_denominator="error")
                }
            )
        query = source.top(0, by=source["value"])
        trace = capture_source(monkeypatch)
        with pytest.raises(
            MaterializationError,
            match="overflow" if boundary == "overflow" else "nonzero denominator",
        ):
            query.execute()
        assert trace.returned_rows == [1]
        assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("operation", ("filter", "top"))
def test_unary_float_selection_preserves_infinite_scores_without_new_checks(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    data = _case_data("duckdb", "strict")
    data = replace(
        data,
        values=data.values.replace("2.5", "CAST('Infinity' AS DOUBLE)", 1).replace(
            "2.5", "CAST('-Infinity' AS DOUBLE)", 1
        ),
    )
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        _project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("unary-query", report_timezone="UTC")
        source = session.read(
            session.members(ms.ref.entity("sales.facts")),
            {"amount": session.catalog.fields.get("sales.facts.amount")},
        )
        score = -source["amount"]
        query = source.where(score > 100) if operation == "filter" else source.top(2, by=score)
        expected = _execute(session, query, fuse=False)
        trace = capture_source(monkeypatch)
        actual = _execute(session, query, fuse=True)
        pd.testing.assert_frame_equal(actual.table, expected.table, check_exact=True)
        assert trace.returned_rows == [len(expected.table)]
        assert trace.uploaded_key_rows == []


@pytest.mark.parametrize("negated", (False, True))
@pytest.mark.parametrize("top_zero", (False, True))
def test_float_select_rejects_nonfinite_output_before_empty_top(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    negated: bool,
    top_zero: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    data = _case_data("duckdb", "strict")
    data = replace(data, values=data.values.replace("2.5", "CAST('Infinity' AS DOUBLE)", 1))
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        _project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("finite-select", report_timezone="UTC")
        source = session.read(
            session.members(ms.ref.entity("sales.facts")),
            {"amount": session.catalog.fields.get("sales.facts.amount")},
        )
        query = source.select({"value": -source["amount"] if negated else source["amount"]})
        if top_zero:
            query = query.top(0, by=query["value"])
        for fuse in (False, True):
            with pytest.raises(
                (MaterializationError, SemanticExpressionError),
                match=r"Column\.select: output overflow",
            ):
                _execute(session, query, fuse=fuse)
