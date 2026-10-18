"""Exact native Decimal quantiles stay compact without integer-state finishing."""

from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.analysis.numeric.test_remote_numeric_query import _project
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.support.source_trace import capture_source


@pytest.fixture
def decimal_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
    backend: str,
    profile: str,
) -> Iterator[Session]:
    data = SourceData(
        "id BIGINT NOT NULL, tenant VARCHAR(16) NOT NULL, category VARCHAR(16), amount DECIMAL(18,2), big BIGINT, weight BIGINT, day DATE",
        "(1,'t1','A',1.25,NULL,1,DATE '2026-07-01'),"
        "(2,'t2','A',2.75,NULL,1,DATE '2026-07-01'),"
        "(3,'t3','B',NULL,NULL,1,DATE '2026-07-01'),"
        "(4,'t4',NULL,NULL,NULL,1,DATE '2026-07-01')",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(backend, profile, tmp_path, monkeypatch, data) as case:
        _project(backend, case, monkeypatch, semantic_project_factory)
        yield mv.session.get_or_create("decimal-original", report_timezone="UTC")


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("postgres", "table")])
)
@pytest.mark.parametrize("method", ("median", "quantile"))
def test_decimal_quantile_rollup_returns_fractional_native_value(
    decimal_session: Session, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    session = decimal_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    metric = amount.median() if method == "median" else amount.quantile(0.25)
    trace = capture_source(monkeypatch)
    frame = session.observe({"value": metric}, by=(category,)).rollup().execute().to_pandas()
    # The existing native Decimal handoff retains its effective source scale,
    # rather than the ideal unrounded interpolation value 1.625.
    expected = Decimal("2.00") if method == "median" else Decimal("1.62")
    assert frame[("value", "value")].tolist() == [expected]
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []
    assert not any(
        "__original_upper" in name or "__original_value" in name
        for names in trace.returned_columns
        for name in names
    )
    assert not any("APPROX" in statement.upper() for statement in trace.native_sql)


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("postgres", "table")])
)
def test_decimal_quantile_selected_parent_keeps_native_fractional_value(
    decimal_session: Session, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    session = decimal_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    fine = session.observe({"value": amount.quantile(0.25)}, by=(category,))
    trace = capture_source(monkeypatch)
    frame = fine.top(1, by=fine["value"]).rollup().execute().to_pandas()
    assert frame[("value", "value")].tolist() == [Decimal("1.62")]
    assert trace.returned_rows == ([1] if backend == "duckdb" else [3, 1])
    assert trace.uploaded_key_rows == ([] if backend == "duckdb" else [1])


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("postgres", "table")])
)
def test_decimal_quantile_all_null_and_empty_target_remain_null(
    decimal_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = decimal_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    tenant = session.catalog.fields.get("sales.facts.tenant")
    for metric in (
        amount.quantile(0.25).where(category == "B"),
        amount.median().where(tenant == "missing"),
    ):
        with monkeypatch.context() as scoped:
            trace = capture_source(scoped)
            frame = (
                session.observe({"value": metric}, by=(category,)).rollup().execute().to_pandas()
            )
            assert len(frame) == 1
            assert pd.isna(frame[("value", "value")].iloc[0])
            assert trace.returned_rows == [1]


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("postgres", "table")])
)
def test_decimal_quantile_strict_nulls_survive_top_zero(
    decimal_session: Session, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    session = decimal_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    fine = session.observe({"value": amount.quantile(0.25, nulls="error")}, by=(category,))
    trace = capture_source(monkeypatch)
    with pytest.raises(AnalysisError, match="non-NA source values"):
        fine.top(0, by=fine["value"]).rollup().execute()
    assert trace.returned_rows == [1 if backend == "duckdb" else 3]


@pytest.mark.parametrize(
    "backend,profile", source_params([("duckdb", "table"), ("postgres", "table")])
)
def test_decimal_quantile_parent_formula_error_precedes_valid_target(
    decimal_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = decimal_session
    amount = session.catalog.fields.get("sales.facts.amount")
    tenant = session.catalog.fields.get("sales.facts.tenant")
    quantile = amount.quantile(0.25)
    metric = quantile.divide(quantile - 1.25, zero_denominator="error")
    trace = capture_source(monkeypatch)
    with pytest.raises(AnalysisError, match="zero denominator"):
        session.observe({"value": metric}, by=(tenant,)).rollup().execute()
    assert trace.returned_rows == [1]


@pytest.mark.parametrize("backend,profile", source_params([("duckdb", "table")]))
def test_decimal_sample_quantiles_finish_before_per_mean(
    decimal_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = decimal_session
    amount = session.catalog.fields.get("sales.facts.amount")
    category = session.catalog.fields.get("sales.facts.category")
    tenant = session.catalog.fields.get("sales.facts.tenant")
    metric = amount.quantile(0.25).per(category).mean()
    trace = capture_source(monkeypatch)
    frame = session.observe({"value": metric}, by=(tenant,)).rollup().execute().to_pandas()
    assert frame[("value", "value")].tolist() == [Decimal("1.62")]
    assert trace.returned_rows == [7]
    assert not any("__original_upper" in name for names in trace.returned_columns for name in names)
