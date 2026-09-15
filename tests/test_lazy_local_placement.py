"""Independent placement invariants over existing exact registered row methods."""

from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.placement import SourceBinding, SourceStep, place, source_eligible
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.observation.contracts import source_owner_of
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators import registry
from marivo.analysis.operators.registry import ImplementationRegistration
from tests.lazy_local_fixtures import REVENUE, setup_local


def test_maximal_source_chain_remains_one_query_recipe(tmp_path: Path) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    filtered = source.where(gt(REVENUE, 5))
    ranked = filtered.rank(filtered.fields.metric(REVENUE))
    target = ranked.limit(2).metric(REVENUE)
    physical = place(target)
    assert len(physical.steps) == 1 and isinstance(physical.steps[0], SourceStep)
    assert physical.steps[0].dataset is target


def test_source_required_successor_never_reenters_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE).where(gt(REVENUE, 5))
    target = source.aggregate()
    original = registry.implementation

    def registrations(dataset: LogicalDataset) -> ImplementationRegistration:
        registered = original(dataset)
        return (
            replace(registered, source_adapter=None)
            if registered.operator_id == "metric.where"
            else registered
        )

    monkeypatch.setattr(registry, "implementation", registrations)
    with pytest.raises(DatasetCompilationError, match="source-required"):
        place(target)


def test_binding_identity_never_uses_connection_argument_equality(tmp_path: Path) -> None:
    runtime, sources, _ = setup_local(tmp_path)
    first = sources.observe(REVENUE)
    owner = source_owner_of(first)
    other = runtime.sources(semantic_registry=owner.semantic_registry, sidecar=owner.sidecar)
    second_owner = source_owner_of(other.observe(REVENUE))
    a = SourceBinding(owner, "same-name", "duckdb", ("1.5.3", "12.0.0"))
    b = SourceBinding(second_owner, "same-name", "duckdb", ("1.5.3", "12.0.0"))
    registration = ImplementationRegistration(
        "metric.where", ("left", "right"), "duckdb", "metric.where"
    )
    assert not a.same_domain(b)
    assert source_eligible(registration, (a, a), a)
    assert not source_eligible(registration, (a, b), a)
    assert not source_eligible(registration, (None, a), a)
    assert not source_eligible(registration, (), replace(a, adapter_versions=("future", "future")))


def test_required_parts_place_locally_without_worker_or_origin_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sources, _ = setup_local(tmp_path)
    import pyarrow.parquet as pq

    from marivo.analysis.materialization import admission
    from marivo.refs import ref
    from tests.lazy_materialization_crash_worker import snapshot

    retained = sources.observe(ref.metric("sales.mean_amount")).execute()
    before = snapshot(runtime)
    queries = runtime.statistics.primary_queries
    monkeypatch.setattr(
        admission,
        "_build_backend_from_effective",
        lambda *args, **kwargs: pytest.fail("placement touched an origin"),
    )
    monkeypatch.setattr(
        pq, "ParquetFile", lambda *args, **kwargs: pytest.fail("placement read a retained part")
    )
    target = retained.where(gt(retained.fields.get("mean_amount"), 0))
    placed = place(target)
    assert len(placed.local_steps) == 1
    assert placed.local_steps[0].implementation.local_method == "metric.where"
    assert snapshot(runtime) == before
    assert runtime.statistics.events.get("local_execution_started", 0) == 0
    assert runtime.statistics.primary_queries == queries


@pytest.mark.parametrize("dependency", ["duckdb", "ibis"])
def test_unregistered_source_version_fails_before_run_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dependency: str
) -> None:
    from marivo.analysis.compiler import placement
    from tests.lazy_materialization_crash_worker import snapshot

    runtime, sources, _ = setup_local(tmp_path)
    source = sources.observe(REVENUE)
    target = source.where(gt(REVENUE, 0))
    target = target.rank(target.fields.metric(REVENUE)).limit(2).metric(REVENUE)
    before = snapshot(runtime)
    monkeypatch.setattr(getattr(placement, dependency), "__version__", "unregistered")
    with pytest.raises(DatasetCompilationError, match="source-required"):
        target.execute()
    assert runtime.last_run_ref is None
    assert runtime.statistics.primary_queries == runtime.statistics.validation_queries == 0
    assert runtime.statistics.events.get("local_execution_started", 0) == 0
    assert snapshot(runtime) == before


def test_semantic_observation_keeps_its_owner_while_comparison_federates_inputs() -> None:
    from marivo.refs import ref
    from tests.lazy_observation_fixtures import make_sources

    first, second = make_sources(), make_sources()
    population = first.population(ref.entity("sales.customers"))
    mixed = second.observe(REVENUE, population=population)
    with pytest.raises(DatasetCompilationError, match="source-required"):
        place(mixed)

    comparison = first.observe(REVENUE).aggregate().compare(second.observe(REVENUE).aggregate())
    graph = place(comparison)
    assert len(graph.steps) == 3
    assert isinstance(graph.steps[0], SourceStep)
    assert isinstance(graph.steps[1], SourceStep)
    assert not graph.steps[0].binding.same_domain(graph.steps[1].binding)
    assert graph.local_steps[0].inputs == (0, 1)
    assert graph.local_steps[0].implementation.operator_id == "metric.compare"
