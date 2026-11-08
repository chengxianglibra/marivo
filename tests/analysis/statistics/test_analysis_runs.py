"""Calendar interval recipes use complete exported values and saved bounds."""

from datetime import timedelta

import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from tests.analysis.statistics.runs_worker import segments
from tests.shared_fixtures import DslCase, DslCaseFactory, terminal_runs


def _entity(catalog: ms.SemanticCatalog, path: str) -> ms.Entity:
    result = catalog.get(path)
    assert result.kind == "entity"
    return result


def _metric(catalog: ms.SemanticCatalog, path: str) -> ms.Metric:
    result = catalog.get(path)
    assert result.kind == "metric"
    return result


def _daily(case: DslCase) -> mv.AnalysisFrame:
    grid_scope = case.session.time_scope(start="2026-08-01", end="2026-08-04")
    return case.session.observe(
        {"value": _metric(case.session.catalog, "metric:sales.revenue")},
        by=(grid_scope.by(mv.grain("day")),),
    )


def test_ratio_exports_na_without_requiring_retained_coverage(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j2")
    history = _daily(case)
    ratio = history.select({"value": history["value"] / history["value"]}).compute()
    frame = ratio.to_pandas()
    assert frame["value"].iloc[0] == 1
    assert frame["value"].iloc[1:].isna().all()
    result = segments(frame)
    assert result["count"].tolist() == [1]
    assert result["duration"].tolist() == [timedelta(days=1)]


def test_public_runs(analysis_dsl_case_factory: DslCaseFactory) -> None:
    case = analysis_dsl_case_factory("j2")
    daily = _daily(case)
    fixed = daily.compute()
    frame = fixed.to_pandas()
    assert frame["value"].tolist() == [60, 0, 0]
    before = len(terminal_runs(case.session))
    result = segments(frame)
    assert result["count"].tolist() == [1]
    assert result["duration"].tolist() == [timedelta(days=1)]
    assert result.loc[result["count"] > 0].equals(result)
    assert result.loc[result["duration"] > timedelta(hours=12), "start"].equals(result["start"])
    assert segments(frame, threshold=-1)["count"].tolist() == [3]
    assert len(terminal_runs(case.session)) == before


@pytest.mark.parametrize("fixed", [False, True])
@pytest.mark.filterwarnings(
    "error:Specifying null_placement in RankOptions is deprecated:FutureWarning"
)
def test_new_grid_after_subject_selection(
    analysis_dsl_case_factory: DslCaseFactory, fixed: bool
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = case.session
    scope = session.time_scope(start="2026-08-01", end="2026-08-04")
    members = session.members(_entity(session.catalog, "entity:sales.customer"))
    revenue = _metric(session.catalog, "metric:sales.revenue")
    values = session.observe({"value": revenue}, by=mv.product(scope, members))
    selection = values.where(values["value"] > 0)
    selected = session.population_from(
        selection, on=((selection.axes[1], _entity(session.catalog, "entity:sales.customer")),)
    )
    fresh = session.observe(
        {"value": revenue},
        by=mv.product(selected, scope.by(mv.grain("day"))),
        where=mv.in_population(selected),
    )
    artifact = fresh.persist()
    receiver = session.persisted_frame(artifact.artifact_id) if fixed else artifact
    frame = receiver.to_pandas()
    assert frame["value"].tolist() == [60, 0, 0]
    before = len(terminal_runs(session))
    assert segments(frame)["count"].tolist() == [1]
    ranked = frame.assign(rank=frame["value"].astype(object).rank(method="dense", ascending=False))
    assert ranked["value"].loc[ranked["rank"] == 1].tolist() == [60]
    assert len(terminal_runs(session)) == before
