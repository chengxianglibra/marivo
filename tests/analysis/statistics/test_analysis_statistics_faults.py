"""Multi-quantity and large exports feed explicit pandas statistical recipes."""

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.analysis.statistics.deviation_fixture import prepare_profiles
from tests.analysis.statistics.pandas_workflows import ForecastModel, forecast, plain
from tests.shared_fixtures import DslCaseFactory, run_ids


def test_three_quantity_search_and_forecast_views(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    prepare_profiles(
        case, "KS", "parquet", "us", "UTC", False, followup=True, profile_indices=(0, 1, 5)
    )
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("statistics", timezone="UTC")
    grid = session.time_scope(start="2026-08-01", end="2026-08-04").by(mv.grain("day"))
    artifact = session.observe(
        {str(i): session.catalog.metrics.get(f"sales.total_{i}") for i in (0, 1, 5)}, by=(grid,)
    ).persist()
    frame = plain(artifact)
    assert frame["0"].tolist() == [1, 2, 7]
    before = run_ids(session)
    for method in ("pearson", "spearman", "kendall"):
        coefficients = frame[["0", "1", "5"]].astype(float).corr(method=method)
        assert coefficients["0"].loc[["1", "5"]].tolist() == pytest.approx([1, 1])
    models: tuple[tuple[ForecastModel, list[int]], ...] = (
        ("naive", [7, 7]),
        ("drift", [10, 13]),
        ("seasonal_naive", [2, 7]),
    )
    for model, expected in models:
        output = forecast(frame["0"], model)
        assert output.prediction.tolist() == expected
        assert (output.lower <= output.prediction).all() and (
            output.upper >= output.prediction
        ).all()
    assert artifact.artifact.evidence_digest().finding_count == 0
    assert run_ids(session) == before


@pytest.mark.release
@pytest.mark.slow
def test_large_export_does_not_truncate_pandas_forecast_rows(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import duckdb

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as db:
        db.execute(
            "INSERT INTO customer(customer_id, region) SELECT 'cap_' || CAST(i AS VARCHAR), 'zero-history' FROM range(997) AS fixtures(i)"
        )
    session = case.session
    grid = session.time_scope(start="2026-08-01", end="2026-08-04").by(mv.grain("day"))
    artifact = session.observe(
        {"value": session.catalog.metrics.get("sales.order_count")},
        by=mv.product(grid, session.members(session.catalog.entities.get("sales.customer"))),
    ).persist()
    frame = plain(artifact)
    assert len(frame) == 3003
    before = run_ids(session)
    member_index = next(
        index for index, axis in enumerate(frame.attrs["axes"]) if axis["role"] != "calendar"
    )
    predictions = pd.concat(
        [
            forecast(group.value, horizon=1).assign(customer=key)
            for key, group in frame.groupby(f"key_{member_index}", sort=False)
        ],
        ignore_index=True,
    )
    assert len(predictions) == 1001
    assert predictions.customer.nunique() == 1001
    assert (predictions.prediction == 0).all()
    assert predictions.loc[predictions.prediction < 0].empty
    assert artifact.artifact.evidence_digest().finding_count == 0
    assert artifact.artifact.findings().items == ()
    assert run_ids(session) == before
