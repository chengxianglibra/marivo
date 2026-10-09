"""real Runtime process boundaries and public compositions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import duckdb
import pyarrow as pa
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.core.graph import MethodNode
from marivo.analysis.core.rules import AssociationFit, DeviationFit, ForecastFit, TimeRuns
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization import (
    deviation_execution,
    runs_execution,
    statistical_execution,
)
from marivo.analysis.materialization.graph_exchange import ExchangeResult
from marivo.datasource.adapters import SourceBatchStream
from tests.analysis.statistics.journeys import (
    METHODS,
    Result,
    check,
    graphs,
    inputs,
    prepare,
    proof,
)
from tests.shared_fixtures import DslCaseFactory
from tests.support.json import Json


@pytest.mark.runtime
@pytest.mark.parametrize("form", ("parquet",))
def test_direct_score_runs_and_blocked_next_round(
    analysis_dsl_case_factory: DslCaseFactory, form: str
) -> None:
    """Read frozen boundaries, then prepare a separately chosen business round."""
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, "table")
    with duckdb.connect(str(case.database_path)) as database:
        database.execute(
            "UPDATE \"order\" SET profile_0=CASE channel WHEN 'a' THEN -2 WHEN 'b' THEN 2 ELSE -2 END"
        )
    if form == "parquet":
        from tests.shared_fixtures import export_dsl_parquet_models

        export_dsl_parquet_models(case, case.root)
        ms.load(workspace_dir=case.root)
    members = session.members(ms.ref.entity("sales.order"))
    _, _, daily = inputs(session)
    assert daily.execute().to_pandas().value.tolist() == [-2, 2, -2]
    predicate = mv.any_of(daily.value.lt(-1), daily.value.gt(1))
    direct = daily.runs(where=predicate).execute()
    assert direct.count.to_pandas().value.tolist() == [3]
    assert direct.duration.to_pandas().value.iloc[0].days == 3
    assert str(direct.start.to_pandas().value.iloc[0]).startswith("2026-08-01")
    assert str(direct.end.to_pandas().value.iloc[0]).startswith("2026-08-04")
    for algorithm in ("mad", "zscore"):
        scored = daily.deviation(method="mad" if algorithm == "mad" else "zscore")
        result = scored.score.runs(where=scored.score.value.gt(0)).execute()
        assert result.count.to_pandas().value.tolist() == [1]
        assert str(result.start.to_pandas().value.iloc[0]).startswith("2026-08-02")
        assert str(result.end.to_pandas().value.iloc[0]).startswith("2026-08-03")
    constant = daily.deviation(method="mad").reference
    unavailable = constant.deviation(method="zscore").score
    gaps = unavailable.runs(where=unavailable.value.gt(0)).execute()
    assert gaps.count.to_pandas().empty
    assert dict(gaps.contract()._facts)["original_unavailable"] == "3"
    # The next business problem uses the original additive metric and explicit
    # windows; neither scores nor interval lengths are attribution quantities.
    axis = ms.ref.dimension("sales.order.channel")
    metric = ms.ref.metric("sales.total_0")
    previous = members.observe(
        metric,
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"),
        by=(
            mv.member(),
            axis,
        ),
    )
    following = members.observe(
        metric,
        during=mv.time_scope(start="2026-08-04", end="2026-08-07"),
        by=(
            mv.member(),
            axis,
        ),
    )
    comparison = following.compare(previous)
    with pytest.raises(AnalysisError) as failure:
        comparison.attribute(axes=(axis,), mode="joint").execute()
    assert "key_set_equal" in str(failure.value)
    assert "3 violating rows" in str(failure.value)
    assert session._runtime.store.resources(session.id) == ()
    directory = os.environ.get("MARIVO_R86_EVIDENCE_DIR")
    if directory:
        row = proof(direct, "time.runs@v1", "produce", form)
        row["scenarios"] = ["A11_next_round"]
        row["outcome"] = "blocked"
        row["blocking_error"] = str(failure.value)
        row["expected_business_delta"] = 2
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / f"a11-next-{form}.json").write_text(
            json.dumps(row, sort_keys=True) + "\n"
        )


@pytest.mark.runtime
@pytest.mark.parametrize("method", METHODS)
def test_shared_method_and_transport_are_distinct_from_new_kernels(
    analysis_dsl_case_factory: DslCaseFactory, method: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, "table")
    logical = graphs(inputs(session))[method]
    if isinstance(logical, mv.LogicalDeviationResult):
        table = mv.table(
            observed=logical.observed,
            reference=logical.reference,
            deviation=logical.deviation,
            score=logical.score,
        )
    elif isinstance(logical, mv.LogicalTimeRunResult):
        table = mv.table(
            start=logical.start, end=logical.end, count=logical.count, duration=logical.duration
        )
    elif isinstance(logical, mv.LogicalAssociationResult):
        table = mv.table(coefficient=logical.coefficient, selected=logical.selected)
    else:
        table = mv.table(prediction=logical.prediction, lower=logical.lower, upper=logical.upper)
    with (
        patch.object(deviation_execution, "execute", wraps=deviation_execution.execute) as d,
        patch.object(runs_execution, "execute", wraps=runs_execution.execute) as r,
        patch.object(statistical_execution, "execute", wraps=statistical_execution.execute) as s,
    ):
        frame = table.execute().to_pandas()

        def kernels() -> int:
            return sum(
                isinstance(
                    call.args[0].parameters, (DeviationFit, TimeRuns, AssociationFit, ForecastFit)
                )
                for consumer in (d, r, s)
                for call in consumer.call_args_list
            )

        assert kernels() == 1
        result = logical.execute()
        check(result, method)
        assert len(frame) == len(result.to_pandas())
        before = kernels()
        selected: Result
        if isinstance(result, mv.MaterializedDeviationResult):
            selected = result.where(result.score.value.gt(0)).execute()
            selected.score.rank(order="descending", ties="dense").limit(1).execute()
        elif isinstance(result, mv.MaterializedTimeRunResult):
            selected = result.where(result.count.value.gt(0)).execute()
            selected.count.rank(order="descending", ties="dense").limit(1).execute()
        elif isinstance(result, mv.MaterializedAssociationResult):
            selected = result.where(result.selected.value.eq(True)).execute()
            selected.coefficient.rank(order="descending", ties="dense").limit(1).execute()
        else:
            selected = result.where(result.prediction.value.gt(0)).execute()
            mv.table(
                prediction=selected.prediction, lower=selected.lower, upper=selected.upper
            ).execute()
        assert kernels() == before
        assert selected.contract().required_parts == result.contract().required_parts
    directory = os.environ.get("MARIVO_R86_EVIDENCE_DIR")
    if directory:
        row = proof(result, method, "produce", "table")
        row["scenarios"] = ["shared_node", "transport_not_kernel"]
        row["shared_kernel_calls"] = 1
        row["transport_kernel_calls"] = 0
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / f"sharing-{method}.json").write_text(
            json.dumps(row, sort_keys=True) + "\n"
        )


@pytest.mark.runtime
def test_two_separate_axes_and_joint_keep_the_original_target(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from tests.shared_fixtures import analysis_dsl_rows

    case = analysis_dsl_case_factory("j2")
    with duckdb.connect(str(case.database_path)) as database:
        database.execute('ALTER TABLE "order" ADD COLUMN region VARCHAR')
        database.execute(
            'UPDATE "order" SET region = customer.region FROM customer WHERE "order".customer_id = customer.customer_id'
        )
    models = case.root / "models/semantic/sales/models.py"
    models.write_text(
        models.read_text()
        + "\norder_region = ms.dimension_column(name='region', entity=orders, column='region')\n"
    )
    ms.load(workspace_dir=case.root)
    members = case.session.members(ms.ref.entity("sales.customer"))
    region = ms.ref.dimension("sales.order.region")
    channel = ms.ref.dimension("sales.order.channel")
    facts = analysis_dsl_rows("j2")
    target = sum(
        1 if day.startswith("2026-08") else -1 if day.startswith("2026-07") else 0
        for _, _, _, _, day, _ in facts.orders
    )
    for axes in (
        (region,),
        (channel,),
        (region, channel),
    ):
        endpoints = []
        for month in (8, 7):
            observed = members.observe(
                ms.ref.metric("sales.order_count"),
                during=mv.time_scope(start=f"2026-{month:02d}-01", end=f"2026-{month + 1:02d}-01"),
                via=ms.ref.relationship("sales.order_buyer"),
                by=(
                    mv.member(),
                    *axes,
                ),
            )
            endpoints.append(observed.group_by(ms.ref.entity("sales.customer")).rollup())
        changed = endpoints[0].compare(endpoints[1])
        result = changed.attribute(axes=axes, mode="joint").execute()
        assert sum(result.contribution.to_pandas().value) == target
        assert dict(result.contract()._facts)["complete_partition"] == "True"


@pytest.mark.runtime
@pytest.mark.parametrize("form", ("parquet",))
def test_nine_methods_three_independent_processes(tmp_path: Path, form: str) -> None:
    project = tmp_path / "project"
    reports: list[Json] = []
    for phase in ("produce", "fixed", "cold"):
        report = tmp_path / f"{phase}.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.analysis.statistics.recovery_worker",
                str(project),
                phase,
                form,
                str(report),
            ],
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        reports.append(json.loads(report.read_text()))
    assert len({row["pid"] for row in reports if isinstance(row, dict)}) == 3
    directory = os.environ.get("MARIVO_R86_EVIDENCE_DIR")
    if directory:
        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"{form}-processes.json").write_text(
            json.dumps({"reports": reports}, indent=2, sort_keys=True) + "\n"
        )


@pytest.mark.runtime
@pytest.mark.parametrize("form", ("parquet",))
@pytest.mark.parametrize("method", ("zscore", "mad"))
def test_score_select_members_followup_one_dag(
    analysis_dsl_case_factory: DslCaseFactory, form: str, method: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, form)
    members = session.members(ms.ref.entity("sales.order"))
    current = members.observe(
        ms.ref.metric("sales.total_0"),
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"),
        by=(mv.member(),),
    )
    baseline = members.observe(
        ms.ref.metric("sales.total_0"),
        during=mv.time_scope(start="2026-07-29", end="2026-08-01"),
        by=(mv.member(),),
    )
    assert isinstance(current, mv.LogicalNumericRelation)
    assert isinstance(baseline, mv.LogicalNumericRelation)
    scored = current.compare(baseline).deviation(method="zscore" if method == "zscore" else "mad")
    defined = scored.where(scored.score.value.is_defined())
    positive = defined.where(defined.score.value.gt(0))
    selected = positive.observed.members()
    assert isinstance(selected, mv.LogicalAnalysisDomain)
    followup = selected.observe(
        ms.ref.metric("sales.total_0"),
        during=mv.time_scope(start="2026-08-03", end="2026-08-04"),
        by=(mv.member(),),
    )
    trace: list[str] = []
    iterate, compute = SourceBatchStream._iterate, deviation_execution.execute

    def read(stream: SourceBatchStream) -> Iterator[pa.RecordBatch]:
        for batch in iterate(stream):
            trace.append("source")
            yield batch

    def score(node: MethodNode, values: tuple[ExchangeResult, ...], binding: str) -> ExchangeResult:
        if isinstance(node.parameters, DeviationFit):
            trace.append("fit")
        return compute(node, values, binding)

    with (
        patch.object(SourceBatchStream, "_iterate", read),
        patch.object(deviation_execution, "execute", score),
    ):
        result = followup.summarize(mv.count_defined()).execute()
    assert result.to_pandas().value.tolist() == [1]
    assert trace.count("fit") == 1
    assert max(i for i, item in enumerate(trace) if item == "source") < trace.index("fit")
    assert session._runtime.store.resources(session.id) == ()
    followed = followup.execute().to_pandas()
    assert followed.value.tolist() == [7]
    assert followed.member.tolist() == ["c"]
    directory = os.environ.get("MARIVO_R86_EVIDENCE_DIR")
    if directory:
        row = proof(scored.execute(), f"deviation.{method}@v1", "produce", form)
        row["scenarios"] = ["A11_score_members_observe"]
        row["source_prefix_trace"] = list(trace)
        row["summary_artifact"] = result.state.artifact_ref.ref
        row["observed_members"] = ["c"]
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / f"a11-members-{method}-{form}.json").write_text(
            json.dumps(row, sort_keys=True) + "\n"
        )


@pytest.mark.runtime
@pytest.mark.parametrize("form", ("parquet",))
def test_category_time_three_methods_three_models(
    analysis_dsl_case_factory: DslCaseFactory, form: str
) -> None:
    case = analysis_dsl_case_factory("j2")
    session = prepare(case, form)
    members = session.members(ms.ref.entity("sales.order"))
    grid = mv.time_grid(
        during=mv.time_scope(start="2026-08-01", end="2026-08-04"), grain=mv.grain("day")
    )
    timed_members = members
    category = timed_members.read(ms.ref.dimension("sales.order.channel"), at=grid.before_end)
    assert isinstance(category, mv.LogicalCategoryRelation)
    values = tuple(
        timed_members.observe(
            ms.ref.metric(f"sales.total_{i}"),
            during=grid,
            by=(mv.member(),),
        )
        .group_by(category, grid)
        .rollup()
        for i in (0, 1, 5)
    )
    for algorithm in ("pearson", "spearman", "kendall"):
        logical = values[0].correlate(
            *values[1:],
            method="pearson"
            if algorithm == "pearson"
            else "spearman"
            if algorithm == "spearman"
            else "kendall",
            lag_range=range(-1, 2),
        )
        selected = logical.where(logical.selected.value.eq(True))
        result = mv.table(
            coefficient=selected.coefficient.rank(order="descending", ties="dense").values
        ).execute()
        assert result.to_pandas().coefficient.tolist() == [1.0] * 9
        assert result._dataset is not None
        # Each pair has three valid zero-lag series and two valid shifted
        # candidates in the middle series; boundary constants remain unavailable.
        assert result._dataset.evidence_digest().finding_count == 15
        directory = os.environ.get("MARIVO_R86_EVIDENCE_DIR")
        if directory:
            row = proof(logical.execute(), f"association.{algorithm}@v1", "produce", form)
            row.update(
                domain="category_time",
                key_profile="typed_complete_tuple",
                time_profile="builtin_day:grid_us:UTC",
                origin_profile=("TABLE" if form == "table" else "PARQUET") + "-US-UTC-builtin_day",
                scenarios=["A11_multi_correlation"],
            )
            (Path(directory) / f"a11-correlation-{algorithm}-{form}.json").write_text(
                json.dumps(row, sort_keys=True) + "\n"
            )
    for model in (mv.naive(), mv.drift(), mv.seasonal_naive(periods=2)):
        logical_f = values[0].forecast(horizon=mv.periods(2), model=model)
        selected_f = logical_f.where(logical_f.prediction.value.gte(0))
        result = mv.table(
            prediction=selected_f.prediction, lower=selected_f.lower, upper=selected_f.upper
        ).execute()
        frame = result.to_pandas()
        assert all(frame.lower <= frame.prediction) and all(frame.prediction <= frame.upper)
        assert frame.prediction.tolist() == (
            [0.0, 0.0, 0.0, 0.0, 7.0, 7.0]
            if model.model_id == "naive@v1"
            else [0.0, 0.0, 10.5, 14.0]
            if model.model_id == "drift@v1"
            else [0.0, 0.0, 2.0, 0.0, 0.0, 7.0]
        )
        directory = os.environ.get("MARIVO_R86_EVIDENCE_DIR")
        if directory:
            row = proof(logical_f.execute(), "forecast." + model.model_id, "produce", form)
            row.update(
                domain="category_time",
                key_profile="typed_complete_tuple",
                scenarios=["A11_three_forecasts"],
            )
            (Path(directory) / f"a11-forecast-{model.model_id}-{form}.json").write_text(
                json.dumps(row, sort_keys=True) + "\n"
            )
