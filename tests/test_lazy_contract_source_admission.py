"""Dataset contracts disclose pure source rejection before action execution."""

from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.placement import place
from marivo.analysis.operators.registry import source_unsupported_reason
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.refs import ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic._quantile import distribution_metric_input
from marivo.semantic.validator import Registry
from tests.lazy_distribution_fixtures import make_distribution_registry
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_observation_fixtures import NoIoActionPort


def _sources(
    backend: str,
    *,
    distribution: bool = False,
    composite: bool = False,
) -> LazySources:
    factory = make_distribution_registry if distribution else make_execution_registry
    registry: Registry
    sidecar: CompiledExpressionSidecar
    registry, sidecar = factory(Path("must-not-open.duckdb"))
    entities = dict(registry.entities)
    if composite:
        orders = entities["sales.orders"]
        entities["sales.orders"] = replace(orders, primary_key=("id", "channel"))
    registry = replace(
        registry,
        entities=entities,
        datasources={
            name: replace(value, backend_type=backend)
            for name, value in registry.datasources.items()
        },
    )
    registry.freeze()
    return make_lazy_sources(
        semantic_registry=registry,
        sidecar=sidecar,
        action_port=NoIoActionPort(),
        session_id="contract-admission",
        store_id="contract-admission",
    )


@pytest.mark.parametrize("backend", ["sqlite", "mysql"])
def test_source_rejection_matches_placement_without_source_io(backend: str) -> None:
    dataset = (
        _sources(backend, distribution=True)
        .observe(distribution_metric_input(ref.metric("sales.revenue"), method="duckdb_tdigest@v1"))
        .aggregate()
    )
    reason = source_unsupported_reason(dataset, backend)
    assert reason is not None
    contract = dataset.contract()
    rendered = contract.render()
    assert f"source_admission: rejected backend={backend}: {reason}" in rendered
    assert rendered.index("source_admission:") < rendered.index("source_checks:")
    assert len(rendered.encode("utf-8")) <= 8192
    with pytest.raises(DatasetCompilationError, match=reason):
        place(dataset)
    assert contract.render() == rendered


def test_selected_legacy_source_route_discloses_migration_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _sources("sqlite").observe(ref.metric("sales.revenue"))
    contract = dataset.contract()
    rendered = contract.render()
    assert (
        "source_admission: blocked_r1.1 backend=sqlite: legacy Dataset source route awaits R5 migration"
        in rendered
    )
    assert "operators:" in rendered
    assert "metric.source_capability@v1" in contract.render(max_output_bytes=None)

    def unexpected_recheck(*args: object) -> None:
        raise AssertionError("render repeated source admission")

    monkeypatch.setattr(
        "marivo.analysis.compiler.source_admission.source_unsupported_reason", unexpected_recheck
    )
    assert contract.render() == rendered


@pytest.mark.parametrize(
    "backend", ("duckdb", "sqlite", "postgres", "mysql", "trino", "clickhouse")
)
def test_basic_population_discloses_candidate_without_source_io(backend: str) -> None:
    dataset = _sources(backend).population(ref.entity("sales.customers"))
    assert (
        f"source_admission: candidate_basic_r1.2 backend={backend}: "
        "physical qualification remains execution-time"
    ) in dataset.contract().render()


def test_mysql_composite_population_discloses_identity_compiler_block() -> None:
    dataset = _sources("mysql", composite=True).population(ref.entity("sales.orders"))
    assert (
        "source_admission: blocked_r1.2 backend=mysql: "
        "Ibis MySQL cannot compile a composite identity struct"
    ) in dataset.contract().render()


@pytest.mark.parametrize("backend", ("sqlite", "mysql"))
def test_basic_metric_aggregate_discloses_candidate_without_source_io(backend: str) -> None:
    dataset = _sources(backend).observe(ref.metric("sales.revenue")).aggregate()
    assert (
        f"source_admission: candidate_basic_r1.2 backend={backend}: "
        "physical qualification remains execution-time"
    ) in dataset.contract().render()


def test_mysql_composite_metric_discloses_identity_compiler_block() -> None:
    dataset = _sources("mysql", composite=True).observe(ref.metric("sales.revenue")).aggregate()
    assert (
        "source_admission: blocked_r1.2 backend=mysql: "
        "Ibis MySQL cannot compile a composite identity struct"
    ) in dataset.contract().render()


def test_unknown_backend_is_a_static_rejection() -> None:
    dataset = _sources("unknown").observe(ref.metric("sales.revenue"))
    assert (
        "source_admission: rejected backend=unknown: no registered source implementation "
        "for session.observe"
    ) in dataset.contract().render()
