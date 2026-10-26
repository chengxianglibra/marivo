"""Checked native Query formulas and Columns keep demanded failures at source."""

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

import marivo.analysis as mv
from marivo.analysis.materialization.errors import MaterializationError
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_source_query_numeric import _data
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value, terminal_runs
from tests.support.paths import PROJECT_ROOT
from tests.support.source_trace import capture_source

CASES = [("duckdb", "table"), ("trino", "iceberg"), ("clickhouse", "mergetree")]


def test_valid_nonfinite_source_values_keep_validity_through_projection_and_cold_store(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    data = _case_data("duckdb", "nan")
    data = replace(
        data,
        values=data.values.replace("2.5", "CAST('Infinity' AS DOUBLE)", 1).replace(
            "2.5", "CAST('-Infinity' AS DOUBLE)", 1
        ),
    )
    with source_case("duckdb", "table", tmp_path, monkeypatch, data) as case:
        _project("duckdb", case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("typed-validity", timezone="UTC")
        source = session.read(
            session.members(session.catalog.get("entity:sales.facts").value),
            {"amount": catalog_value(session.catalog, "sales.facts.amount", kind="field")},
        )
        fixed = source.compute()
        projected = fixed.select({"copy": fixed["amount"], "missing": fixed["amount"].is_null()})
        saved = projected.persist()
        rows = saved.to_pandas()
        assert rows["missing"].tolist() == [False, True, False, False, False, False]
        values = pa.array(rows["copy"])
        assert values.is_null().to_pylist() == [False, True, False, False, False, False]
        assert values[0].as_py() == float("inf")
        assert values[3].as_py() == float("-inf")
        assert pa.compute.is_nan(values).fill_null(False).to_pylist() == [
            False,
            False,
            True,
            False,
            False,
            False,
        ]
        derived = (
            fixed.select(
                {
                    "unary_missing": (-fixed["amount"]).is_null(),
                    "comparison_missing": (fixed["amount"] > 0).is_null(),
                }
            )
            .compute()
            .to_pandas()
        )
        for name in ("unary_missing", "comparison_missing"):
            assert derived[name].tolist() == [False, True, False, False, False, False]
        for candidate in (
            fixed.select({"value": fixed["amount"] + 0}),
            fixed.reduce({"value": fixed["amount"].sum()}),
            fixed.reduce({"value": fixed["amount"].mean()}),
        ):
            with pytest.raises(MaterializationError, match="finite"):
                candidate.compute()
        code = """
import sys
from unittest.mock import patch
import pyarrow as pa
import marivo.analysis as mv
from marivo.datasource.adapters import SourceSession
with patch.object(SourceSession, 'execute', side_effect=AssertionError('source read')):
    session = mv.session.resume(sys.argv[1])
    frame = session.persisted_frame(sys.argv[2])
    rows = frame.to_pandas()
    values = pa.array(rows['copy'])
    assert rows['missing'].tolist() == [False, True, False, False, False, False]
    assert values.is_null().to_pylist() == [False, True, False, False, False, False]
    assert pa.compute.is_nan(values).fill_null(False).to_pylist() == [False, False, True, False, False, False]
    assert values[0].as_py() == float('inf') and values[3].as_py() == float('-inf')
    again = frame.select({'copy': frame['copy'], 'missing': frame['copy'].is_null()}).compute().to_pandas()
    copied = pa.array(again['copy'])
    assert copied.is_null().to_pylist() == [False, True, False, False, False, False]
    assert pa.compute.is_nan(copied).fill_null(False).to_pylist() == [False, False, True, False, False, False]
    assert copied[0].as_py() == float('inf') and copied[3].as_py() == float('-inf')
"""
        process = subprocess.run(
            [sys.executable, "-c", code, session.id, saved.artifact.id],
            cwd=tmp_path,
            env={
                **os.environ,
                "PYTHONPATH": str(PROJECT_ROOT),
                "MARIVO_PROJECT_ROOT": str(tmp_path),
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert process.returncode == 0, process.stdout + process.stderr


def _case_data(backend: str, boundary: str) -> SourceData:
    data = _data(boundary)
    return (
        replace(data, values=data.values.replace("nan()", "CAST('NaN' AS DOUBLE)"))
        if backend == "duckdb"
        else data
    )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("operation", ("formula", "columns", "na_zero"))
def test_query_arithmetic_keeps_selected_completed_values(
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
        session = mv.session.get_or_create("source-query-arithmetic", timezone="UTC")
        amount = catalog_value(session.catalog, "sales.facts.amount", kind="field")
        big = catalog_value(session.catalog, "sales.facts.big", kind="field")
        weight = catalog_value(session.catalog, "sales.facts.weight", kind="field")
        category = catalog_value(session.catalog, "sales.facts.category", kind="field")
        if operation == "formula":
            source = session.observe(
                {
                    "total": big.sum(),
                    "n": big.count(),
                    "sparse": big.sum() + big.count().where(category == "A"),
                },
                by=(category,),
            )
            source = source.where(source["sparse"] > 40)
            query = source.top(1, by=source["sparse"])
            expected_rows = 1
        else:
            source = session.read(
                session.members(session.catalog.get("entity:sales.facts").value),
                {"amount": amount, "big": big, "weight": weight},
            )
            if operation == "na_zero":
                source = source.where(source["amount"].is_null())
                query = source.select({"value": source["amount"] / (source["big"] - source["big"])})
            else:
                source = source.where(source["big"] + 5 > 20)
                source = source.select(
                    {"value": (source["big"] * 2 - source["weight"]) / (source["weight"] + 1)}
                )
                query = source.top(2, by=-(source["value"] + 1))
            expected_rows = 2
        trace = capture_source(monkeypatch)
        actual = query.compute().to_pandas()
        assert len(actual) == expected_rows
        assert trace.returned_rows == ([3] if operation == "formula" else [6])
        assert trace.uploaded_key_rows == []
        if operation == "formula":
            assert actual["sparse"].tolist() == [110]
            assert actual["total"].tolist() == [110]
            assert actual["n"].tolist() == [2]
        elif operation == "columns":
            assert actual["value"].tolist() == [19.5, 29.5]
        elif operation == "na_zero":
            assert actual["value"].isna().all()
        trace.save(
            f"query-arithmetic-{backend}-{operation}",
            case.environment,
            {"rows": 6},
            {"operation": operation},
            None,
            (case.session,),
        )


@pytest.mark.parametrize("backend,profile", source_params(CASES))
@pytest.mark.parametrize("projection", ("top", "empty_select"))
def test_local_query_overflow_survives_projection(
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    profile: str,
    projection: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    with source_case(
        backend, profile, tmp_path, monkeypatch, _case_data(backend, "overflow")
    ) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        session = mv.session.get_or_create("source-query-checks", timezone="UTC")
        big = catalog_value(session.catalog, "sales.facts.big", kind="field")
        source = session.read(
            session.members(session.catalog.get("entity:sales.facts").value), {"big": big}
        )
        source = source.select({"value": source["big"] + 1})
        query = source.top(0, by=source["value"]) if projection == "top" else source.select({})
        trace = capture_source(monkeypatch)
        with pytest.raises(MaterializationError, match="overflow"):
            query.compute()
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
        session = mv.session.get_or_create("unary-query", timezone="UTC")
        source = session.read(
            session.members(session.catalog.get("entity:sales.facts").value),
            {"amount": catalog_value(session.catalog, "sales.facts.amount", kind="field")},
        )
        score = -source["amount"]
        query = source.where(score > 100) if operation == "filter" else source.top(2, by=score)
        trace = capture_source(monkeypatch)
        actual = query.compute().to_pandas()
        assert actual[actual.attrs["axes"][0]["name"]].tolist() == (
            [("t4", 4)] if operation == "filter" else [("t4", 4), ("t5", 5)]
        )
        assert terminal_runs(session) == ()
        from marivo._definition.query import (
            FilterValues,
            TopValues,
            ValueBinary,
            ValueLiteral,
            ValueSlot,
            ValueUnary,
        )
        from marivo.analysis.compiler.query_program import FrameProgram
        from marivo.analysis.core.data_schema import DataAxis, DataSchema
        from marivo.analysis.core.data_types import DataColumn, ScalarDataType
        from marivo.analysis.methods.frame_values import evaluate_frame

        schema = DataSchema(
            (DataAxis("id", (DataColumn("id", "id", ScalarDataType("int64"), False),), name="id"),),
            (DataColumn("amount", "amount", ScalarDataType("float64"), True),),
            value_paths=(("amount",),),
        )
        score_expression = ValueUnary("neg", ValueSlot(0))
        kernel = (
            FilterValues(ValueBinary("gt", score_expression, ValueLiteral(100)))
            if operation == "filter"
            else TopValues(2, score_expression, True)
        )
        program = FrameProgram(kernel, schema, schema)
        raw = pd.DataFrame(
            {
                "id": [1, 2, 3, 4, 5, 6],
                "amount": [float("inf"), None, 1.0, float("-inf"), -1.0, 0.0],
            }
        )
        rows = evaluate_frame(program, (raw,))
        assert rows.id.tolist() == ([4] if operation == "filter" else [4, 5])


@pytest.mark.parametrize("negated", (False, True))
@pytest.mark.parametrize("top_zero", (False, True))
def test_local_float_select_preserves_direct_values_and_checks_computed_outputs(
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
        session = mv.session.get_or_create("finite-select", timezone="UTC")
        source = session.read(
            session.members(session.catalog.get("entity:sales.facts").value),
            {"amount": catalog_value(session.catalog, "sales.facts.amount", kind="field")},
        )
        query = source.select({"value": -source["amount"] if negated else source["amount"]})
        if top_zero:
            query = query.top(0, by=query["value"])
        if negated:
            with pytest.raises(MaterializationError, match=r"Frame\.select: output overflow"):
                query.compute()
        else:
            actual = query.compute().to_pandas()
            assert actual.empty if top_zero else actual["value"].iloc[0] == float("inf")
        assert terminal_runs(session) == ()
        from marivo._definition.query import SelectValues, ValueSlot, ValueUnary
        from marivo.analysis.compiler.query_program import FrameProgram
        from marivo.analysis.core.data_schema import DataSchema
        from marivo.analysis.core.data_types import DataColumn, ScalarDataType
        from marivo.analysis.methods.frame_values import evaluate_frame

        input_schema = DataSchema(
            (),
            (DataColumn("amount", "amount", ScalarDataType("float64"), True),),
            value_paths=(("amount",),),
        )
        output_schema = DataSchema(
            (),
            (DataColumn("value", "value", ScalarDataType("float64"), True),),
            value_paths=(("value",),),
        )
        expression = ValueUnary("neg", ValueSlot(0)) if negated else ValueSlot(0)
        program = FrameProgram(SelectValues((("value", expression),)), input_schema, output_schema)
        if negated:
            with pytest.raises(SemanticExpressionError, match=r"Frame\.select: output overflow"):
                evaluate_frame(program, (pd.DataFrame({"amount": [float("inf")]}),))
        else:
            selected = evaluate_frame(program, (pd.DataFrame({"amount": [float("inf")]}),))
            assert selected["value"].tolist() == [float("inf")]
