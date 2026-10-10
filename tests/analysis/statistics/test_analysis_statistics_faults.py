"""Composed public statistical journeys and forecast finding limits."""

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.shared_fixtures import DslCaseFactory


def test_three_quantity_search_and_forecast_views(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from tests.analysis.statistics.deviation_fixture import prepare_profiles

    case = analysis_dsl_case_factory("j2")
    prepare_profiles(
        case, "KS", "parquet", "us", "UTC", False, followup=True, profile_indices=(0, 1, 5)
    )
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("a11-statistics", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.order"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    observations = tuple(
        members.observe(
            ms.ref.metric(f"sales.total_{i}"),
            during=grid,
            by=(mv.member(),),
        )
        .group_by(grid)
        .rollup()
        for i in (0, 1, 5)
    )
    for method in ("pearson", "spearman", "kendall"):
        search = observations[0].correlate(*observations[1:], method=method, lag_range=range(-1, 2))
        selected = search.where(search.selected.value.eq(True))
        table = mv.table(
            coefficient=selected.coefficient.rank(order="descending", ties="dense").values
        ).execute()
        assert table.to_pandas().coefficient.tolist() == [1.0, 1.0, 1.0]
        assert table._dataset is not None
        assert table._dataset.evidence_digest().finding_count == 9
    for model in (mv.naive(), mv.drift(), mv.seasonal_naive(periods=2)):
        forecast = observations[0].forecast(horizon=mv.periods(2), model=model)
        selected_forecast = forecast.where(forecast.prediction.value.gt(0))
        table = mv.table(
            prediction=selected_forecast.prediction,
            lower=selected_forecast.lower,
            upper=selected_forecast.upper,
        ).execute()
        rows = table.to_pandas()
        assert len(rows) == 2
        assert all(rows.lower <= rows.prediction) and all(rows.prediction <= rows.upper)
        assert table._dataset is not None
        assert table._dataset.evidence_digest().finding_count == 2


@pytest.mark.slow
def test_forecast_findings_cap_and_selection_transport(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    import duckdb

    with duckdb.connect(str(case.database_path)) as db:
        # Four fixture customers plus 997 extras produce the smallest one-step
        # forecast above the 1,000-finding cap.
        db.execute(
            "INSERT INTO customer(customer_id, region) "
            "SELECT 'cap_' || CAST(i AS VARCHAR), 'zero-history' FROM range(997) AS fixtures(i)"
        )
    members = case.session.members(ms.ref.entity("sales.customer"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    history = members.observe(
        ms.ref.metric("sales.order_count"),
        during=grid,
        via=ms.ref.relationship("sales." + case.names.buyer),
        by=(mv.member(),),
    )
    result = history.forecast(horizon=mv.periods(1)).execute()
    assert result._dataset is not None
    from marivo.analysis.materialization.graph_findings import _policy

    policy = _policy(result._dataset.verified().parts)
    assert (policy.eligible, policy.emitted, policy.truncated) == (1001, 1000, 1)
    assert result.evidence_digest().finding_count == policy.emitted
    page = result.findings(limit=1)
    assert len(page.items) == 1 and page.next_cursor is not None
    empty = result.where(result.prediction.value.lt(0)).execute()
    assert empty.prediction.to_pandas().empty
    transported = empty.findings(limit=1)
    assert tuple((i.canonical_item_key, i.value) for i in transported.items) == tuple(
        (i.canonical_item_key, i.value) for i in page.items
    )
    assert all(i.artifact_ref == empty.state.artifact_ref for i in transported.items)
