"""Independent private Candidate construction, schema and selector contracts."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.placement import PandasStep, SourceStep, place
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.observation.predicates import eq, gt
from marivo.analysis.operators.candidate_contracts import (
    CandidateObjective,
    CandidatePayload,
    CandidateSemantics,
)
from marivo.analysis.operators.candidate_dataset import LogicalCandidateDataset
from marivo.analysis.operators.discovery import (
    candidate_filterable_field,
    validate_candidate,
    validate_definition,
)
from marivo.analysis.operators.errors import CandidateError
from marivo.analysis.session._lazy_sources import make_lazy_sources
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_forecast_fixtures import history
from tests.lazy_observation_fixtures import NoIoActionPort, make_sources


def _candidate(objective: CandidateObjective, *, panel: bool = False) -> LogicalCandidateDataset:
    metric = history(make_sources(), panel=panel)
    if objective == "point_anomalies":
        return metric.discover.point_anomalies()
    if objective == "interesting_windows":
        return metric.discover.interesting_windows()
    return metric.compare(metric).discover.period_shifts()


@pytest.mark.parametrize("panel", [False, True])
@pytest.mark.parametrize(
    ("objective", "shape", "tail", "key_tail"),
    [
        (
            "point_anomalies",
            "point-anomaly",
            (
                "time_coordinate",
                "observed_value",
                "baseline_value",
                "signed_deviation",
                "direction",
            ),
            ("time_coordinate",),
        ),
        (
            "interesting_windows",
            "interesting-window",
            (
                "window_start",
                "window_end",
                "point_count",
                "peak_absolute_zscore",
                "direction",
                "baseline_start",
                "baseline_end",
            ),
            ("window_start", "window_end"),
        ),
        (
            "period_shifts",
            "period-shift",
            (
                "window_start",
                "window_end",
                "baseline_start",
                "baseline_end",
                "window_size",
                "peak_absolute_zscore",
                "direction",
            ),
            ("window_start", "window_end", "baseline_start", "baseline_end"),
        ),
    ],
)
def test_exact_objective_schema_keys_identity_and_order(
    objective: CandidateObjective,
    shape: str,
    tail: tuple[str, ...],
    key_tail: tuple[str, ...],
    panel: bool,
) -> None:
    candidate = _candidate(objective, panel=panel)
    dimensions = ("channel",) if panel else ()
    assert candidate.kind == "candidate"
    assert candidate.row_contract.shape_id.local_shape_id == shape
    fields = {f.name: f for f in candidate.schema.columns}
    assert tuple(fields) == ("item_id", "score", "reason_codes", *dimensions, *tail)
    assert candidate.row_contract.key_field_ids == tuple(
        fields[n].field_id for n in (*dimensions, *key_tail)
    )
    assert isinstance(candidate.row_set_contract.ordering, d._OrderedOrdering)
    assert tuple(term.field_id for term in candidate.row_set_contract.ordering.terms) == (
        fields["score"].field_id,
        *candidate.row_contract.key_field_ids,
        fields["item_id"].field_id,
    )
    assert [term.direction for term in candidate.row_set_contract.ordering.terms] == [
        "descending"
    ] + ["ascending"] * (len(key_tail) + len(dimensions) + 1)
    assert fields["reason_codes"].logical_type_id == "candidate_reasons"
    assert fields["reason_codes"].role_id == "candidate_reason_codes"
    assert not candidate_filterable_field(fields["reason_codes"])
    for field in candidate.schema.columns:
        if field.role_id not in ("dimension", "time_dimension"):
            assert field.field_id.value == f"generated.discover.{objective}.{field.name}@v1"
            assert field.derivation_identity == f"discover.{objective}.{field.name}@v1"
            assert not field.nullable
        if field.name != "reason_codes":
            assert candidate_filterable_field(field)
    if objective == "point_anomalies":
        time = next(
            f for f in history(make_sources()).schema.columns if f.role_id == "time_dimension"
        )
        assert fields["time_coordinate"] == replace(
            time, _token=d._CORE_TOKEN, name="time_coordinate"
        )
    assert isinstance(candidate.row_set_contract.cardinality, d._KeyedCardinality)
    assert isinstance(candidate.row_set_contract.cardinality.row_bound, d._StaticRowBound)
    assert candidate.row_set_contract.cardinality.row_bound.max_rows == 50


def test_namespace_and_definition_identity_are_pure_and_private() -> None:
    metric = history(make_sources())
    namespace = metric.discover
    assert not callable(namespace)
    assert not hasattr(metric, "point_anomalies")
    assert not hasattr(namespace, "period_shifts")
    with pytest.raises(FrozenInstanceError):
        attribute = "_dataset"
        setattr(namespace, attribute, history(make_sources(session_id="foreign")))
    first = namespace.point_anomalies()
    second = namespace.point_anomalies()
    assert first.definition_fingerprint == second.definition_fingerprint
    assert first._inputs == (metric,)
    assert isinstance(first._root, LogicalRootHandle)
    assert isinstance(first._root.payload, CandidatePayload)
    spec = first._root.payload.spec
    assert spec.definition.input_state_kind == "logical"
    assert spec.definition.input_authority == metric.definition_fingerprint
    assert spec.definition.threshold == 3.0 and spec.definition.limit == 50
    assert spec.definition.method_id == "point_zscore@v1"
    assert spec.definition.metric_key == "metric:sales.revenue"
    assert spec.time_name == "order_time" and spec.metric_name == "revenue"
    assert spec.baseline_time_name is None
    assert (
        len(
            {
                first.definition_fingerprint,
                namespace.point_anomalies(threshold=2).definition_fingerprint,
                namespace.point_anomalies(limit=20).definition_fingerprint,
                namespace.interesting_windows().definition_fingerprint,
            }
        )
        == 4
    )
    delta = metric.compare(metric)
    assert not callable(delta.discover)
    assert not hasattr(delta.discover, "point_anomalies")
    assert not hasattr(delta, "period_shifts")
    assert delta.discover.period_shifts()._inputs == (delta,)
    exports = mv.__all__
    assert isinstance(exports, (list, tuple))
    for name in (
        "LogicalCandidateDataset",
        "MaterializedCandidateDataset",
        "CandidateDataset",
        "MetricDiscovery",
        "DeltaDiscovery",
    ):
        assert name not in exports and not hasattr(mv, name)
    assert "descriptive screening" in first.contract().render()
    for value in (first, first.where(gt(first.fields.get("score"), 0))):
        rendered = value.contract().render()
        assert "threshold" in rendered and "3.0" in rendered
        assert "discovery_limit" in rendered and "50" in rendered
        assert metric.definition_fingerprint in rendered
        assert "population mean/stddev" in rendered
        assert "input_authority" not in {f.name for f in value.schema.columns}
    assert not hasattr(first, "discover") and not hasattr(first, "compare")


@pytest.mark.parametrize("threshold", [True, 0, -1.0, float("nan"), float("inf"), 10**1000])
def test_invalid_threshold_rejected_without_actions(threshold: float) -> None:
    with pytest.raises(CandidateError):
        history(make_sources()).discover.point_anomalies(threshold=threshold)


@pytest.mark.parametrize("limit", [True, 0, -1, 1001, 1.5, "3", None])
def test_invalid_discovery_limit_rejected_without_actions(limit: int) -> None:
    with pytest.raises(CandidateError):
        history(make_sources()).discover.interesting_windows(limit=limit)


def test_shape_admission_and_normalized_parameters() -> None:
    source = make_sources()
    for invalid in (
        source.observe(ref.metric("sales.revenue")),
        source.observe(ref.metric("sales.revenue")).aggregate(),
    ):
        with pytest.raises(CandidateError):
            invalid.discover.point_anomalies()
        with pytest.raises(CandidateError):
            invalid.compare(invalid).discover.period_shifts()
    metric = history(source)
    assert (
        metric.discover.point_anomalies(threshold=2).definition_fingerprint
        == metric.discover.point_anomalies(threshold=2.0).definition_fingerprint
    )


@pytest.mark.parametrize("objective", ["point_anomalies", "interesting_windows", "period_shifts"])
def test_candidate_continuations_and_selector_ownership(objective: CandidateObjective) -> None:
    candidate = _candidate(objective, panel=True)
    selected = candidate.where(
        gt(candidate.fields.get("score"), 0), eq(candidate.fields.get("direction"), "high")
    )
    selected = selected.where(eq(ref.dimension("sales.orders.channel"), "web"))
    ranked = selected.rank(selected.fields.get("score")).limit(2)
    assert isinstance(ranked, LogicalCandidateDataset)
    assert ranked.row_contract.family_semantics == candidate.row_contract.family_semantics
    assert ranked.row_contract.key_field_ids == candidate.row_contract.key_field_ids
    with pytest.raises(DatasetConstructionError):
        ranked.rank(ranked.fields.get("score"))
    different_rank = candidate.rank(candidate.fields.get("score"), order="ascending")
    with pytest.raises(DatasetConstructionError):
        different_rank.where(gt(ranked.fields.get("rank"), 0))
    foreign = history(make_sources(session_id="foreign")).discover.point_anomalies()
    with pytest.raises(DatasetConstructionError):
        candidate.where(gt(foreign.fields.get("score"), 0))
    with pytest.raises(CandidateError):
        candidate.where(eq(candidate.fields.get("reason_codes"), "global_zscore_run"))
    with pytest.raises(DatasetConstructionError):
        candidate.rank(candidate.fields.get("direction"))


def test_corrupt_generated_field_semantics_and_definition_rejected() -> None:
    candidate = _candidate("interesting_windows")
    row = candidate.row_contract
    semantics = row.family_semantics
    assert isinstance(semantics, CandidateSemantics)
    for change in (
        {"method_id": "point_zscore@v1"},
        {"score_field_id": semantics.item_id_field_id},
    ):
        bad = replace(
            row,
            _token=d._CORE_TOKEN,
            family_semantics=replace(semantics, _token=d._CORE_TOKEN, **change),
        )
        with pytest.raises(CandidateError):
            validate_candidate(bad, candidate.row_set_contract)
    columns = tuple(
        replace(f, _token=d._CORE_TOKEN, derivation_identity="changed@v1")
        if f.name == "score"
        else f
        for f in row.schema.columns
    )
    with pytest.raises(CandidateError):
        validate_candidate(
            replace(row, _token=d._CORE_TOKEN, schema=d._make_schema(columns)),
            candidate.row_set_contract,
        )
    assert isinstance(candidate._root, LogicalRootHandle)
    assert isinstance(candidate._root.payload, CandidatePayload)
    definition = candidate._root.payload.spec.definition
    for bad_definition in (
        replace(definition, input_authority="ds_invalid"),
        replace(definition, metric_key="metric:other.metric"),
        replace(definition, baseline_fold_authority=definition.fold_authority),
    ):
        with pytest.raises(CandidateError):
            validate_definition(bad_definition)


def test_registered_local_frontier_without_backend_access() -> None:
    semantic, sidecar = make_execution_registry(Path("never-opened.duckdb"))
    sources = make_lazy_sources(
        semantic_registry=semantic,
        sidecar=sidecar,
        session_id="candidate-test",
        store_id="candidate-test",
        action_port=NoIoActionPort(),
    )
    candidate = history(sources).discover.point_anomalies()
    selected = candidate.where(gt(candidate.fields.get("score"), 0))
    graph = place(selected.rank(selected.fields.get("score")).limit(2))
    assert len(graph.steps) == 5 and isinstance(graph.steps[0], SourceStep)
    assert all(isinstance(step, PandasStep) for step in graph.steps[1:])
    assert graph.local_steps[0].implementation.local_method == "discover.point_anomalies"
    assert graph.local_steps[0].implementation.source_adapter is None
