"""Execute native provider example bodies against independently prepared inputs."""

from __future__ import annotations

from pathlib import Path

import pytest

from marivo._temporal import Grain, TimeScope
from marivo.analysis._capabilities.dataset_model import CallableInput
from marivo.analysis._capabilities.dataset_registry import prepare
from marivo.analysis._capabilities.dataset_render import render
from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.contract import DatasetContract
from marivo.analysis.datasets.fields import DatasetFieldRef
from marivo.analysis.observation.predicates import AnalysisPredicate
from tests.lazy_disclosure_fixtures import example_inputs

# Expected outcomes are test-owned and grouped by the owning public contracts.
PURE_RESULTS = {
    "datasets.dataset.contract": "DatasetContract",
    "datasets.fields.metric": "DatasetFieldRef",
    "datasets.fields.dimension": "DatasetFieldRef",
    "datasets.fields.get": "DatasetFieldRef",
    "datasets.contract.render": "str",
    "datasets.contract.show": "NoneType",
    "population.create": "population",
    "observe": "metric",
    "Session.source_bindings": "metric",
    "metric_dataset.with_dimensions": "metric",
    "metric_dataset.with_time_axis": "metric",
    "metric_dataset.aggregate": "metric",
    "metric_dataset.rollup": "metric",
    "metric_dataset.metric": "metric",
    "grain": "Grain",
    "time_scope": "TimeScope",
    "datasets.where": "metric",
    "datasets.rank": "metric",
    "datasets.limit": "metric",
    "metric_dataset.compare": "delta",
    "delta_dataset.attribute": "attribution",
    "metric_dataset.correlate": "association",
    "metric_dataset.forecast": "forecast",
    "window_bucket": "WindowBucketAlignment",
    "periods": "ForecastHorizon",
    "forecast_models.naive": "ForecastModel",
    "forecast_models.drift": "ForecastModel",
    "forecast_models.seasonal_naive": "ForecastModel",
    "events.match": "event",
    "lifecycle.replay": "lifecycle",
    "event_dataset.funnel": "event",
    "event_dataset.time_to_event": "event",
    "event_dataset.select_subjects": "population",
    "event_dataset.compare": "delta",
    "funnel_delta_dataset.attribute": "attribution",
    "lifecycle_dataset.distribution": "lifecycle",
    "lifecycle_dataset.transitions": "lifecycle",
    "lifecycle_dataset.dwell": "lifecycle",
    "lifecycle_dataset.violations": "lifecycle",
    "lifecycle_dataset.select_subjects": "population",
    "step": "PatternStep",
    "sequence": "EventPattern",
    "event_matching.first_per_subject": "FirstPerSubject",
    "event_matching.every_start": "EveryStart",
    "dropped_before": "DroppedBefore",
    "in_state": "InState",
    "funnel_loss_rate": "FunnelLossRate",
    "from_inception": "FromInception",
    "BoundedCompletenessDeclarationV1.create": "BoundedCompletenessDeclarationV1",
    "SourceOriginCompletenessDeclarationV1.create": "SourceOriginCompletenessDeclarationV1",
    "Grain.to_token": "str",
    "Grain.width_seconds": "int",
    "TimeScope.contract": "TimeScopeContractV1",
    "TimeScope.model_dump": "dict",
    "TimeScope.render": "str",
    "TimeScope.show": "NoneType",
    **dict.fromkeys(
        (
            "eq",
            "not_eq",
            "lt",
            "lte",
            "gt",
            "gte",
            "is_in",
            "is_null",
            "is_not_null",
            "all_of",
            "any_of",
            "not_",
        ),
        "AnalysisPredicate",
    ),
    **dict.fromkeys(
        (
            "discovery.point_anomalies",
            "discovery.interesting_windows",
            "discovery.entity_outliers",
            "discovery.period_shifts",
            "discovery.driver_axes",
        ),
        "candidate",
    ),
    "runtime_metric.aggregate": "RuntimeAggregateExpr",
    "runtime_metric.weighted_mean": "RuntimeWeightedMeanExpr",
    "runtime_metric.slice": "RuntimeSliceExpr",
    "runtime_metric.ratio": "RuntimeRatioExpr",
    "runtime_metric.linear": "RuntimeLinearExpr",
}

RUNTIME_TARGETS = frozenset(
    [
        "actions.execute",
        "actions.show",
        "actions.to_pandas",
        "session.get_or_create",
        "session.current",
        "session.resume",
        "session.recent",
        "session.inspect",
        "session.abandon_run",
        "session.artifact",
        "session.runs",
        "session.get_run",
        "session.graph",
        "session.revalidate",
        "Session.show",
        "Session.render",
        "artifact.findings",
        "artifact.finding",
        "runtime.values.render",
        "runtime.values.show",
    ]
)


@pytest.fixture(scope="module")
def inputs() -> dict[str, object]:
    return example_inputs(prepare())


def test_every_example_has_an_independent_execution_case() -> None:
    r = prepare()
    assert {
        d.canonical_id
        for d in r.descriptors
        if isinstance(d, CallableInput) and not d.example.runtime
    } == set(PURE_RESULTS)
    assert {
        d.canonical_id for d in r.descriptors if isinstance(d, CallableInput) and d.example.runtime
    } == RUNTIME_TARGETS


@pytest.mark.parametrize("target,expected", tuple(PURE_RESULTS.items()))
def test_native_pure_example(
    target: str, expected: str, inputs: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    descriptor = prepare().by_canonical_id(target)
    assert isinstance(descriptor, CallableInput)
    namespace = {name: inputs[name] for name in descriptor.example.requires}
    example = render(prepare(), target).split("Example:\n", 1)[1].rsplit("\nExpected:", 1)[0]
    exec(example, namespace)
    value = namespace[descriptor.example.result]
    if expected in (
        "population",
        "metric",
        "delta",
        "attribution",
        "association",
        "forecast",
        "candidate",
        "event",
        "lifecycle",
    ):
        assert isinstance(value, LogicalDataset)
        assert value.row_contract.shape_id.family_id == expected
        assert value._owner.session_id == "lifecycle"
        assert value._root.operator_id
    elif expected == "Grain":
        assert isinstance(value, Grain) and value.to_token() == "day"
    elif expected == "TimeScope":
        assert isinstance(value, TimeScope) and value.start < value.end
    else:
        assert type(value).__name__ == expected
    if isinstance(value, DatasetFieldRef):
        assert value.owning_session_id == "lifecycle"
    if isinstance(value, DatasetContract):
        assert "metric" in value.render()
    if isinstance(value, AnalysisPredicate):
        assert value.kind
    if expected == "str":
        assert isinstance(value, str) and value
    if target.endswith(".show"):
        assert capsys.readouterr().out


@pytest.mark.runtime
def test_all_runtime_examples_use_committed_v3_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis as mv
    from marivo.analysis.datasets.base import Dataset
    from marivo.analysis.evidence._dataset_types import ArtifactRevalidation, Finding, FindingPage
    from marivo.analysis.session._lazy_read_model import RunPage, SessionGraph, SucceededRun
    from tests.lazy_lifecycle_fixtures import setup_lifecycle
    from tests.lazy_runtime_read_fixtures import input_value

    runtime, sources, database = setup_lifecycle(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "help-examples"\n')
    monkeypatch.chdir(tmp_path)
    session = mv.Session._from_runtime(runtime)
    session._sources_value = sources
    disclosure = prepare()
    environment = example_inputs(disclosure, sources)
    delta = environment["delta"]
    assert isinstance(delta, LogicalDataset)
    materialized = delta.execute()
    assert prepare().by_callable(materialized.contract).canonical_id == "datasets.dataset.contract"
    page = materialized.findings()
    assert page.items, "The example must select a real production Finding."
    record = runtime.store.artifact(str(materialized.state.artifact_ref))
    assert record is not None
    environment.update(
        session=session,
        saved_session_id=session.id,
        saved_session_name=session.name,
        namespace=mv.session,
        materialized=materialized,
        artifact_ref=materialized.state.artifact_ref,
        artifact_digest=materialized.evidence_digest,
        run_id=record.producing_run_ref,
        finding_id=page.items[0].finding_id,
    )
    executed = set()
    for descriptor in disclosure.descriptors:
        if not isinstance(descriptor, CallableInput) or not descriptor.example.runtime:
            continue
        target = descriptor.canonical_id
        if target == "session.abandon_run":
            pending = runtime.store.admit(
                session.id, "help-pending", input_value(record.descriptor), run_ref="help-pending"
            )
            environment["pending_run"] = pending.run_ref
        scope = {name: environment[name] for name in descriptor.example.requires}
        before = runtime.statistics.events.get("source_statement", 0)
        example = render(disclosure, target).split("Example:\n", 1)[1].rsplit("\nExpected:", 1)[0]
        exec(example, scope)
        result = scope[descriptor.example.result]
        executed.add(target)
        if target == "actions.execute":
            assert (
                isinstance(result, MaterializedDataset)
                and result.row_contract.shape_id.family_id == "metric"
            )
            database.rename(database.with_suffix(".offline"))
        else:
            assert runtime.statistics.events.get("source_statement", 0) == before
        if target == "session.artifact":
            assert (
                isinstance(result, Dataset)
                and result.state.artifact_ref == materialized.state.artifact_ref
            )
        if target == "session.get_run":
            assert isinstance(result, SucceededRun)
        if target == "session.runs":
            assert isinstance(result, RunPage) and result.items
        if target == "session.graph":
            assert isinstance(result, SessionGraph) and result.runs
        if target == "session.revalidate":
            assert isinstance(result, ArtifactRevalidation)
        if target == "artifact.finding":
            assert isinstance(result, Finding) and result.finding_id == page.items[0].finding_id
        if target == "artifact.findings":
            assert isinstance(result, FindingPage) and result.items
        if target == "session.abandon_run":
            run = runtime.store.run("help-pending")
            assert run is not None and run.lifecycle == "failed"
            assert runtime.store.run(record.producing_run_ref).lifecycle == "succeeded"
        if target.endswith(".show"):
            assert capsys.readouterr().out
    assert executed == RUNTIME_TARGETS
