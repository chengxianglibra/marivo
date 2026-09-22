"""Construction admission and independently pinned private Association contracts."""

from dataclasses import replace
from typing import Literal

import pytest

import marivo.analysis as mv
from marivo.analysis.compiler.placement import PandasStep, SourceStep, place
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.observation.predicates import eq, gt
from marivo.analysis.operators import registry
from marivo.analysis.operators.association import LogicalAssociationDataset
from marivo.analysis.operators.association_contracts import CorrelationMethod
from marivo.analysis.operators.correlate import validate_association
from marivo.analysis.operators.errors import CorrelationError
from marivo.analysis.operators.registry import ImplementationRegistration
from marivo.refs import ref
from tests.lazy_correlation_fixtures import association_spec
from tests.lazy_observation_fixtures import make_sources


@pytest.mark.parametrize("shape", ["entity", "dimension", "time", "dimension-time"])
@pytest.mark.parametrize("method", ["pearson", "spearman", "kendall"])
def test_contracts_are_complete_and_private(
    shape: Literal["entity", "dimension", "time", "dimension-time"], method: CorrelationMethod
) -> None:
    spec = association_spec(method, shape=shape)
    validate_association(spec.output_row, spec.output_rows)
    names = tuple(f.name for f in spec.output_row.schema.columns)
    assert "entity_identity" not in names
    assert ("lag_offset" in names) == ("time" in shape)
    assert "p_value" not in names
    assert mv.LogicalAssociationDataset is LogicalAssociationDataset
    assert not hasattr(mv, "AssociationDataset")


def test_construction_rejects_invalid_arity_and_lag_without_io() -> None:
    source = make_sources()
    one = source.observe(ref.metric("sales.revenue"))
    with pytest.raises(CorrelationError):
        one.correlate()
    two = source.observe([ref.metric("sales.revenue"), ref.metric("sales.mean_amount")])
    with pytest.raises(CorrelationError):
        two.correlate(lag_range=range(1))
    with pytest.raises(CorrelationError):
        two.aggregate().correlate()
    assert (
        len(association_spec("pearson", shape="time", lags=range(4097)).semantics.lag_offsets)
        == 4097
    )
    with pytest.raises(CorrelationError):
        association_spec("pearson", shape="time", lags=range(0))
    assert (
        len(association_spec("pearson", shape="time", lags=range(4096)).semantics.lag_offsets)
        == 4096
    )


def test_generated_predicates_and_selector_ownership() -> None:
    metric = make_sources().observe([ref.metric("sales.revenue"), ref.metric("sales.mean_amount")])
    value = metric.correlate()
    value.where(gt(value.fields.get("coefficient"), 0)).rank(value.fields.get("coefficient"))
    with pytest.raises(CorrelationError, match="identity"):
        value.where(eq(value.fields.get("metric_key_a"), "metric:sales.revenue"))
    with pytest.raises(Exception, match=r"selector|field|ownership|owner"):
        value.where(gt(metric.fields.get("revenue"), 0))


def test_registered_local_method_does_not_require_a_failed_compile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Use real table declarations so the registered source adapter can be selected.
    from pathlib import Path

    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from tests.lazy_execution_fixtures import make_execution_registry
    from tests.lazy_observation_fixtures import NoIoActionPort

    original = registry.implementation

    def no_reduction(dataset: LogicalDataset) -> ImplementationRegistration:
        result = original(dataset)
        return (
            replace(
                result,
                backends=tuple(
                    replace(item, source=False)
                    for item in result.backends
                    if item.preparation is not None
                ),
            )
            if result.operator_id == "metric.correlate"
            else result
        )

    monkeypatch.setattr(registry, "implementation", no_reduction)
    semantic, sidecar = make_execution_registry(Path("not-opened.duckdb"))
    source = make_lazy_sources(
        semantic_registry=semantic,
        sidecar=sidecar,
        session_id="session_test",
        store_id="store_test",
        action_port=NoIoActionPort(),
    )
    graph = place(
        source.observe([ref.metric("sales.revenue"), ref.metric("sales.mean_amount")]).correlate()
    )
    assert (
        len(graph.steps) == 2
        and isinstance(graph.steps[0], SourceStep)
        and graph.steps[0].operation == "correlation"
    )
    assert isinstance(graph.steps[1], PandasStep)


@pytest.mark.parametrize("count", [2, 16, 17])
def test_metric_arity_boundary_uses_distinct_governed_bindings(count: int) -> None:
    from marivo.analysis.session._lazy_sources import make_lazy_sources
    from tests.lazy_observation_fixtures import NoIoActionPort, make_semantic_registry

    registry, sidecar = make_semantic_registry()
    metrics = dict(registry.metrics)
    template = metrics["sales.revenue"]
    names = [f"sales.metric_{i}" for i in range(count)]
    for name in names:
        metrics[name] = replace(template, semantic_id=name, name=name.split(".")[1])
    registry = replace(registry, metrics=metrics)
    registry.freeze()
    source = make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="arity",
        store_id="arity",
    )
    if count == 17:
        from marivo.analysis.observation.errors import ObservationConstructionError

        with pytest.raises(ObservationConstructionError, match="sixteen"):
            source.observe([ref.metric(name) for name in names])
    else:
        value = source.observe([ref.metric(name) for name in names])
        assert value.correlate().kind == "association"


def test_correlate_keeps_shared_input_role_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    from marivo.analysis.compiler.errors import DatasetCompilationError
    from marivo.analysis.datasets.base import Dataset
    from marivo.analysis.datasets.registry import ConsumerRegistration, DatasetFamilyRegistry

    value = (
        make_sources()
        .observe([ref.metric("sales.revenue"), ref.metric("sales.mean_amount")])
        .correlate()
    )
    original = DatasetFamilyRegistry.consumer

    def mismatched(
        self: DatasetFamilyRegistry, dataset: Dataset, consumer_id: str
    ) -> ConsumerRegistration:
        result = original(self, dataset, consumer_id)
        return replace(result, input_roles=("wrong-role",))

    monkeypatch.setattr(DatasetFamilyRegistry, "consumer", mismatched)
    with pytest.raises(DatasetCompilationError, match="input role mismatch"):
        registry.implementation(value)
