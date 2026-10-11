"""Public source and fixed statistical graph execution."""

from decimal import Decimal

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError, StatisticalRelationError
from marivo.analysis.evidence._dataset_types import AssociationFindingValueV1
from tests.shared_fixtures import DslCaseFactory


def test_public_source_association_and_spearman_fixed_continuation(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j4")
    members = case.session.members(ms.ref.entity("sales.customer"))
    via = ms.ref.relationship("sales." + case.names.buyer)
    during = mv.time_scope(start="2026-08-01", end="2026-09-01")
    a = members.observe(
        ms.ref.metric("sales.revenue"),
        during=during,
        via=via,
        by=(mv.member(),),
    )
    b = members.observe(
        ms.ref.metric("sales.order_count"),
        during=during,
        via=via,
        by=(mv.member(),),
    )
    for method in ("pearson", "spearman", "kendall"):
        logical = a.correlate(b, method=method)
        result = logical.execute()
        rows = result.coefficient.to_pandas()
        assert len(rows) == 1 and -1 <= rows.value.iloc[0] <= 1
        assert result.selected.to_pandas().value.tolist() == [True]
        assert logical.coefficient.execute().to_pandas().value.tolist() == rows.value.tolist()
        if method != "spearman":
            continue
        assert result.contract().actions == (
            mv.AnalysisAction("relation.coefficient", "analysis.MaterializedCoefficientRelation"),
            mv.AnalysisAction("relation.selected", "analysis.MaterializedBooleanRelation"),
            mv.AnalysisAction(
                "relation.where(predicate)", "analysis.dsl.MaterializedAssociationResult.where"
            ),
        )
        fixed = a.execute().correlate(b.execute(), method=method).execute()
        assert fixed.coefficient.to_pandas().value.tolist() == rows.value.tolist()
        ranking = result.coefficient.rank(order="descending", ties="dense").limit(1).execute()
        assert ranking.values.to_pandas().value.tolist() == rows.value.tolist()
        assert ranking.ranks.to_pandas().value.tolist() == [1]
        table = mv.table(coefficient=result.coefficient, selected=result.selected).execute()
        assert len(table.to_pandas()) == 1
        assert len(result.findings().items) == 1
        finding = result.finding(result.findings().items[0].finding_id)
        assert isinstance(finding.value, AssociationFindingValueV1)
        assert finding.value.coefficient == rows.value.iloc[0]
        assert result.evidence_digest().finding_count == 1
        selected = result.where(result.selected.value.eq(True)).execute()
        assert selected.coefficient.to_pandas().value.tolist() == rows.value.tolist()
        assert selected.evidence_digest().finding_count == result.evidence_digest().finding_count


def test_public_source_fixed_forecast(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
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
    for name, model in (
        ("naive", mv.naive()),
        ("drift", mv.drift()),
        ("seasonal_naive", mv.seasonal_naive(periods=2)),
    ):
        logical = history.forecast(horizon=mv.periods(2), model=model)
        result = logical.execute()
        prediction = result.prediction.to_pandas()
        lower = result.lower.to_pandas()
        upper = result.upper.to_pandas()
        assert len(prediction) == 8
        assert list(lower.value <= prediction.value) == [True] * 8
        assert list(upper.value >= prediction.value) == [True] * 8
        if name != "naive":
            continue
        assert logical.lower.execute().to_pandas().value.tolist() == lower.value.tolist()
        fixed = history.execute().forecast(horizon=mv.periods(2), model=model).execute()
        assert fixed.prediction.to_pandas().value.tolist() == prediction.value.tolist()
        table = mv.table(
            prediction=result.prediction, lower=result.lower, upper=result.upper
        ).execute()
        assert len(table.to_pandas()) == len(prediction)
        assert len(result.findings().items) == len(prediction)
        selected = result.where(result.prediction.value.gte(0)).execute()
        assert len(selected.lower.to_pandas()) == int((prediction.value >= 0).sum())
        assert selected.evidence_digest().finding_count == result.evidence_digest().finding_count
        before = case.session.runs().items
        for level in (0.0, 1.0, float("nan"), float("inf")):
            with pytest.raises(StatisticalRelationError):
                history.forecast(horizon=mv.periods(1), model=model, interval_level=level)
            assert case.session.runs().items == before
        with pytest.raises(AnalysisError):
            result.lower.aggregate(mv.sum())


def test_all_requested_pairs_and_static_ceiling(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j4")
    members = case.session.members(ms.ref.entity("sales.customer"))
    metric = ms.ref.metric("sales.order_count")
    quantities = tuple(
        members.observe(
            mv.runtime_metric.linear(add=[metric] * (i + 1), label=f"times_{i}"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales." + case.names.buyer),
            by=(mv.member(),),
        )
        for i in range(1, 4)
    )
    result = quantities[0].correlate(*quantities[1:]).execute()
    rows = result.coefficient.to_pandas()
    assert len(rows) == 3
    assert rows.value.tolist() == [1.0] * 3
    assert result.evidence_digest().finding_count == 3

    model_file = case.root / "models/semantic/sales/models.py"
    model_file.write_text(
        model_file.read_text()
        + "\n"
        + "\n".join(
            f"count_{i} = ms.count(name='count_{i}', entity=orders, time=ordered_at)"
            for i in range(16)
        )
    )
    ms.load(workspace_dir=case.root)
    session = mv.session.get_or_create("dsl-j4-expanded", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.customer"))
    quantities = tuple(
        members.observe(
            ms.ref.metric(f"sales.count_{i}"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales." + case.names.buyer),
            by=(mv.member(),),
        )
        for i in range(16)
    )
    with pytest.raises(StatisticalRelationError) as repeated:
        quantities[0].correlate(quantities[0])
    assert repeated.value.code == "r8.input_identity"
    with pytest.raises(StatisticalRelationError) as ceiling:
        quantities[0].correlate(*quantities[1:], lag_range=range(-1000000, 1000000))
    assert ceiling.value.code == "r8.candidate_ceiling"


@pytest.mark.parametrize("calendar", (False, True))
def test_lag_counts_ties_and_approved_future(
    analysis_dsl_case_factory: DslCaseFactory, calendar: bool
) -> None:
    from tests.analysis.statistics.deviation_fixture import prepare_profiles
    from tests.analysis.statistics.deviation_time_worker import publish_calendar

    case = analysis_dsl_case_factory("j2")
    prepare_profiles(
        case, "KS", "parquet", "us", "UTC", calendar, followup=True, profile_indices=(0, 5)
    )
    catalog = ms.load(workspace_dir=case.root)
    if calendar:
        publish_calendar(catalog, "UTC")
    session = mv.session.get_or_create("lag-r84", report_timezone="UTC")
    members = session.members(ms.ref.entity("sales.order"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-07" if calendar else "2026-08-04"),
        grain=ms.calendar_grain(calendar=ms.ref.period_calendar("sales.unequal"), level="period")
        if calendar
        else mv.grain("day"),
    )
    a = (
        members.observe(ms.ref.metric("sales.total_0"), during=grid, by=(mv.member(),))
        .group_by(grid)
        .rollup()
    )
    b = (
        members.observe(ms.ref.metric("sales.total_5"), during=grid, by=(mv.member(),))
        .group_by(grid)
        .rollup()
    )
    result = a.correlate(b, lag_range=range(-2, 3)).execute()
    rows = result.to_pandas().sort_values("lag")
    assert rows.status.tolist() == [
        "insufficient_pairs",
        "valid",
        "valid",
        "valid",
        "insufficient_pairs",
    ]
    assert rows.matched_count.tolist() == [1, 2, 3, 2, 1]
    assert rows.boundary_drop_count.tolist() == [2, 1, 0, 1, 2]
    assert rows.complete_pair_count.tolist() == rows.matched_count.tolist()
    assert rows.selected.tolist() == [False, False, True, False, False]
    selected = result.where(result.selected.value.eq(True)).execute()
    assert selected.evidence_digest().finding_count == 3
    history_grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04" if calendar else "2026-08-03"),
        grain=ms.calendar_grain(calendar=ms.ref.period_calendar("sales.unequal"), level="period")
        if calendar
        else mv.grain("day"),
    )
    history = (
        members.observe(
            ms.ref.metric("sales.total_5"),
            during=history_grid,
            by=(mv.member(),),
        )
        .group_by(history_grid)
        .rollup()
    )
    future = history.forecast(horizon=mv.periods(1)).execute()
    assert future.prediction.to_pandas().value.iloc[0] == Decimal("0.000002")
    assert future.lower.to_pandas().value.iloc[0] == Decimal("0.000000")
    assert future.upper.to_pandas().value.iloc[0] == Decimal("0.000004")
    fixed_a = a.execute()
    assert isinstance(fixed_a, mv.MaterializedGroupedNumericRelation)
    with pytest.raises(StatisticalRelationError) as missing:
        fixed_a.where(fixed_a.value.gt(0)).forecast(horizon=mv.periods(1))
    assert missing.value.code == "r8.grid_incomplete"
    if calendar:
        with pytest.raises(StatisticalRelationError) as exhausted:
            a.forecast(horizon=mv.periods(1))
        assert exhausted.value.code == "r8.future_grid"


@pytest.mark.parametrize("point", ("insert_evidence", "insert_findings", "before_commit"))
def test_statistical_failure_is_atomic(
    analysis_dsl_case_factory: DslCaseFactory, point: str
) -> None:
    case = analysis_dsl_case_factory("j4")
    members = case.session.members(ms.ref.entity("sales.customer"))
    via = ms.ref.relationship("sales." + case.names.buyer)
    during = mv.time_scope(start="2026-08-01", end="2026-09-01")
    a = members.observe(
        ms.ref.metric("sales.revenue"),
        during=during,
        via=via,
        by=(mv.member(),),
    )
    b = members.observe(
        ms.ref.metric("sales.order_count"),
        during=during,
        via=via,
        by=(mv.member(),),
    )
    logical = a.correlate(b)
    fixed = logical.execute()

    def inject(actual: str) -> None:
        if actual == point:
            raise RuntimeError("injected statistical publication failure")

    case.session._runtime._hook = inject
    with pytest.raises(AnalysisError):
        logical.execute()
    case.session._runtime._hook = None
    restored = case.session.artifact(fixed.evidence_digest().artifact_ref.ref)
    assert isinstance(restored, mv.MaterializedAssociationResult)
    assert restored.evidence_digest().finding_count == 1
    with case.session._runtime.store._read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM dataset_artifacts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM action_resource_journal").fetchone()[0] == 0


def test_forecast_prediction_is_a_complete_numeric_receiver(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import SourceSession
    from tests.analysis.statistics.recovery_worker import forbidden

    case = analysis_dsl_case_factory("j2")
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
    prior = history.forecast(horizon=mv.periods(2), model=mv.drift()).execute()
    monkeypatch.setattr(SourceSession, "__enter__", forbidden)
    next_forecast = prior.prediction.forecast(horizon=mv.periods(1))
    result = next_forecast.execute()
    assert result.prediction.to_pandas().value.tolist() == [-1.0, 0.0, 0.0, 0.0]
    assert next_forecast.execute().state.artifact_ref == result.state.artifact_ref
    assert case.session._runtime.store.resources(case.session.id) == ()
