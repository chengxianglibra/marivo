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
from marivo.analysis.operators.registry import BackendRegistration, ImplementationRegistration
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
            replace(registered, backends=())
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
    a = SourceBinding(owner, "same-name", "duckdb")
    b = SourceBinding(second_owner, "same-name", "duckdb")
    registration = ImplementationRegistration(
        "metric.where",
        ("left", "right"),
        (BackendRegistration("duckdb", source=True),),
        "metric.where",
    )
    assert not a.same_domain(b)
    assert source_eligible(registration, (a, a), a)
    assert not source_eligible(registration, (a, b), a)
    assert not source_eligible(registration, (None, a), a)


@pytest.mark.runtime
def test_required_parts_place_locally_without_worker_or_origin_work(
    retained_r54_case, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pyarrow.parquet as pq

    import marivo.analysis as mv
    import marivo.semantic as ms
    from marivo.analysis.compiler.graph_plan import LocalMethodStage, RouteChoice
    from marivo.analysis.materialization.graph_execution import prepare_graph
    from marivo.datasource.runtime import DatasourceConnectionService
    from tests.lazy_materialization_crash_worker import snapshot

    case = retained_r54_case
    runtime = case.session._runtime
    retained = (
        case.session.members(ms.ref.entity("sales.customer"))
        .observe(
            ms.ref.metric("sales.mean_amount"),
            during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
            via=ms.ref.relationship("sales.order_buyer"),
        )
        .execute()
    )
    before = snapshot(runtime)
    queries = runtime.statistics.primary_queries

    def forbidden(*args, **kwargs):
        pytest.fail("placement read retained parts or touched an origin")

    monkeypatch.setattr(DatasourceConnectionService, "use_backend", forbidden)
    monkeypatch.setattr(pq, "ParquetFile", forbidden)
    target = retained.where(retained.value.gt(0))
    placed = prepare_graph(
        target._node.root,
        session_ref=case.session.id,
        routes=(RouteChoice(target._node.root.identity, "artifact_python"),),
    )
    local = [s for s in placed.admitted.stages if isinstance(s, LocalMethodStage)]
    assert len(local) == 1
    assert local[0].node.method.name == "parts_transport"
    assert snapshot(runtime) == before
    assert runtime.statistics.events.get("local_execution_started", 0) == 0
    assert runtime.statistics.primary_queries == queries


@pytest.mark.runtime
@pytest.mark.parametrize("dependency", ["duckdb", "ibis"])
@pytest.mark.parametrize("version", [None, "unregistered"])
def test_diagnostic_version_does_not_change_selection_or_execution(
    retained_r54_case, monkeypatch: pytest.MonkeyPatch, dependency: str, version: str | None
) -> None:
    import importlib

    import marivo.analysis as mv
    import marivo.semantic as ms
    from marivo.analysis.materialization.graph_execution import prepare_graph

    case = retained_r54_case
    source = case.session.members(ms.ref.entity("sales.customer")).observe(
        ms.ref.metric("sales.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship("sales.order_buyer"),
    )
    target = source.where(source.value.gt(0))
    from marivo.analysis.compiler.graph_plan import RouteChoice
    from marivo.analysis.core.graph import MethodNode, topology

    routes = tuple(
        RouteChoice(node.identity, "ibis")
        for node in topology(target._node.root)
        if isinstance(node, MethodNode)
    )
    before = prepare_graph(target._node.root, session_ref=case.session.id, routes=routes)
    module = importlib.import_module(dependency)
    if version is None:
        monkeypatch.delattr(module, "__version__")
    else:
        monkeypatch.setattr(module, "__version__", version)
    after = prepare_graph(target._node.root, session_ref=case.session.id, routes=routes)
    assert before == after
    assert target.execute().to_pandas()["value"].sum() == 147


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
