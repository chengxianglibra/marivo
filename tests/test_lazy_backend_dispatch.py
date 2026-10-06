"""Exact backend selection without enabling another production backend."""

from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.compiler.placement import (
    SourceStep,
    place,
)
from marivo.analysis.datasets.base import LogicalDataset
from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.operators import registry
from marivo.analysis.operators.registry import (
    BackendName,
    BackendRegistration,
    ImplementationRegistration,
)
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.semantic.ir import AggKind
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_local_fixtures import REVENUE, setup_local
from tests.lazy_observation_fixtures import NoIoActionPort
from tests.lazy_runtime_patch_targets import runtime_patch_owner


def _sources(backend: BackendName, *, aggregation: AggKind = "sum") -> LazySources:
    semantic, sidecar = make_execution_registry(Path("unopened.duckdb"))
    semantic = replace(
        semantic,
        metrics={
            **semantic.metrics,
            "sales.revenue": replace(semantic.metrics["sales.revenue"], aggregation=aggregation),
        },
        datasources={
            name: replace(value, backend_type=backend)
            for name, value in semantic.datasources.items()
        },
    )
    semantic.freeze()
    return make_lazy_sources(
        semantic_registry=semantic,
        sidecar=sidecar,
        session_id="dispatch",
        store_id="dispatch",
        action_port=NoIoActionPort(),
    )


def test_duplicate_backend_registration_is_rejected() -> None:
    with pytest.raises(DatasetRegistrationError, match="duplicate backend") as duplicate:
        ImplementationRegistration(
            "metric.where",
            ("input",),
            (
                BackendRegistration("duckdb", True),
                BackendRegistration("duckdb", False, "distribution"),
            ),
            "metric.where",
        )
    assert duplicate.value.location == "dataset.implementation_registry"
    assert duplicate.value.received is not None and "metric.where" in duplicate.value.received
    assert duplicate.value.repair is not None
    assert "Merge" in duplicate.value.repair.action
    with pytest.raises(DatasetRegistrationError, match="empty backend") as empty:
        BackendRegistration("duckdb", False)
    assert empty.value.location == "dataset.implementation_registry"
    assert empty.value.received is not None and "duckdb" in empty.value.received
    assert empty.value.repair is not None
    assert "omit this backend entry" in empty.value.repair.action


@pytest.mark.parametrize("backend", ["duckdb", "postgres"])
def test_closed_collection_selects_exact_backend_without_io(
    backend: BackendName, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Test-only registrations exercise dispatch; no remote runtime is enabled.
    entries = (BackendRegistration("duckdb", True), BackendRegistration("postgres", True))
    original = registry.implementation

    def registered(value: LogicalDataset) -> ImplementationRegistration:
        return replace(original(value), backends=entries)

    monkeypatch.setattr(registry, "implementation", registered)
    logical = _sources(backend).observe(REVENUE).aggregate()
    graph = place(logical)
    assert len(graph.steps) == 1
    step = graph.steps[0]
    assert isinstance(step, SourceStep)
    assert step.implementation is next(item for item in entries if item.backend == backend)
    assert step.operation == "source"
    assert step.binding.adapter == backend
    assert registered(logical).for_backend("unknown") is None


@pytest.mark.parametrize("backend", ["postgres", "mysql", "sqlite", "trino", "clickhouse"])
def test_production_backend_places_qualified_median(backend: BackendName) -> None:
    logical = _sources(backend, aggregation="median").observe(REVENUE)
    graph = place(logical)
    assert isinstance(graph.steps[0], SourceStep)
    assert graph.steps[0].implementation.backend == backend


def test_unknown_method_has_no_backend_default() -> None:
    from marivo.analysis.datasets.errors import DatasetConstructionError
    from marivo.analysis.datasets.handles import LogicalRootHandle

    logical = _sources("duckdb").observe(REVENUE)
    root = logical._root
    assert isinstance(root, LogicalRootHandle)
    # Bypass the public immutable constructor solely to test the registry's guard.
    object.__setattr__(root, "operator_id", "metric.unknown")
    with pytest.raises(DatasetConstructionError, match="unsupported producer"):
        registry.implementation(logical)


@pytest.mark.parametrize(
    "backend", ["duckdb", "postgres", "mysql", "sqlite", "trino", "clickhouse"]
)
def test_execute_continuation_resolves_for_each_backend(backend: BackendName) -> None:
    logical = _sources(backend).observe(REVENUE)
    from marivo.analysis._capabilities.surface import ANALYSIS_LIVE_SURFACE
    from marivo.introspection.live.resolve import resolve_live_target

    routed = resolve_live_target("actions.execute", ANALYSIS_LIVE_SURFACE)
    bound = resolve_live_target(logical.execute, ANALYSIS_LIVE_SURFACE)
    assert bound.descriptor is routed.descriptor
    assert bound.canonical_id == "actions.execute"


def test_retained_import_reads_the_execution_declaration(monkeypatch: pytest.MonkeyPatch) -> None:
    execution = registry.backend_execution("duckdb")
    assert execution is not None and execution.retained_import
    assert registry.backend_execution("postgres") == registry.BackendExecution(
        "postgres", retained_import=False
    )
    monkeypatch.setattr(
        registry, "backend_execution", lambda _: replace(execution, retained_import=False)
    )
    assert not registry.supports_retained_import("duckdb")
    monkeypatch.setattr(registry, "backend_execution", lambda _: execution)
    assert registry.supports_retained_import("postgres")


@pytest.mark.runtime
def test_missing_execution_owner_rejects_before_run_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError
    from tests.lazy_adapter_runtime_worker import forbidden, snapshot

    runtime, sources, _ = setup_local(tmp_path)
    before = snapshot(runtime)
    monkeypatch.setattr(registry, "backend_execution", lambda _: None)
    monkeypatch.setattr(
        runtime_patch_owner("_build_backend_from_effective"),
        "_build_backend_from_effective",
        forbidden,
    )
    with pytest.raises(MaterializationError):
        sources.observe(REVENUE).aggregate().execute()
    assert runtime.last_run_ref is None
    assert snapshot(runtime) == before
    assert runtime.statistics.events == {"reconciliation": 1}
