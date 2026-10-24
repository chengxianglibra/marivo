"""Complex original routes keep each physical contribution root independent."""

import duckdb
import pytest

import marivo.analysis as mv
from tests.shared_fixtures import DslCaseFactory
from tests.support.source_trace import capture_source


def test_multi_root_original_ratio_recomputes_before_host_finish(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    session = case.session
    customer = session.catalog.fields.get("sales.order.customer_id")
    metric = session.catalog.metrics.get("sales." + case.names.aov)
    trace = capture_source(monkeypatch)
    result = session.observe({"aov": metric}).compute().to_pandas()
    assert result["value", "aov"].tolist() == [200 / 101]
    assert trace.returned_rows == [1]
    assert trace.uploaded_key_rows == []
    assert not any("__original_value" in columns for columns in trace.returned_columns)


def test_multi_root_selection_restricts_each_actual_fiber(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    session = case.session
    customer = session.catalog.fields.get("sales.order.customer_id")
    revenue = session.catalog.metrics.get("sales." + case.names.line_revenue)
    count = session.catalog.metrics.get("sales." + case.names.order_count)
    fine = session.observe({"revenue": revenue, "orders": count}, by=(customer,))
    fixed = fine.where(fine["orders"] == 1).compute()
    selected = session.population_from(fixed, on=((fixed.axes[0], customer),))
    trace = capture_source(monkeypatch)
    result = (
        session.observe({"revenue": revenue, "orders": count}, where=mv.in_population(selected))
        .compute()
        .to_pandas()
    )
    assert result["value", "revenue"].tolist() == [100]
    assert result["value", "orders"].tolist() == [1]


def test_multi_root_missing_group_keeps_empty_zero_alignment(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    with duckdb.connect(str(case.database_path)) as connection:
        connection.execute("DELETE FROM order_line WHERE order_id='j3_b'")
    session = case.session
    customer = session.catalog.fields.get("sales.order.customer_id")
    revenue = session.catalog.metrics.get("sales." + case.names.line_revenue)
    count = session.catalog.metrics.get("sales." + case.names.order_count)
    fine = session.observe({"revenue": revenue, "orders": count}, by=(customer,))
    trace = capture_source(monkeypatch)
    fixed = fine.where(fine["revenue"] == 0).compute()
    selected = session.population_from(fixed, on=((fixed.axes[0], customer),))
    result = (
        session.observe({"revenue": revenue, "orders": count}, where=mv.in_population(selected))
        .compute()
        .to_pandas()
    )
    assert result["value", "revenue"].tolist() == [0]
    assert result["value", "orders"].tolist() == [1]


def test_multi_root_drill_recomputes_each_selected_contribution_root(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j3_weighting")
    session = case.session
    customer = session.catalog.fields.get("sales.order.customer_id")
    channel = session.catalog.fields.get("sales.order.channel")
    revenue = session.catalog.metrics.get("sales." + case.names.line_revenue)
    count = session.catalog.metrics.get("sales." + case.names.order_count)
    fine = session.observe({"revenue": revenue, "orders": count}, by=(customer,))
    fixed = fine.where(fine["orders"] == 1).compute()
    selected = session.population_from(fixed, on=((fixed.axes[0], customer),))
    trace = capture_source(monkeypatch)
    result = (
        session.observe(
            {"revenue": revenue, "orders": count},
            by=(customer, channel),
            where=mv.in_population(selected),
        )
        .compute()
        .to_pandas()
    )
    assert result["value", "revenue"].tolist() == [100]
    assert result["value", "orders"].tolist() == [1]
    assert result.iloc[:, 1].tolist() == ["web"]
