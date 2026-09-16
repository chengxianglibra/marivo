"""Exact backend selection without enabling another production backend."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.compiler.placement import (
    ExecutionBinding,
    ParquetBinding,
    PhysicalStageGraph,
    SourceBinding,
    SourceStep,
    place,
    source_binding,
    source_eligible,
)
from marivo.analysis.datasets.base import LogicalDataset, MaterializedDataset
from marivo.analysis.datasets.errors import DatasetRegistrationError
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.operators import registry
from marivo.analysis.operators.association_contracts import CorrelationMethod
from marivo.analysis.operators.registry import (
    BackendName,
    BackendRegistration,
    ImplementationRegistration,
)
from marivo.analysis.session._lazy_sources import LazySources, make_lazy_sources
from marivo.datasource.backends import (
    BuiltDatasourceBackend,
    EffectiveDatasourceKwargs,
    _build_backend_from_effective,
)
from marivo.datasource.ir import DatasourceIR
from marivo.refs import ref
from tests.lazy_execution_fixtures import make_execution_registry
from tests.lazy_local_fixtures import REVENUE, setup_local
from tests.lazy_observation_fixtures import NoIoActionPort


def _sources(backend: BackendName) -> LazySources:
    semantic, sidecar = make_execution_registry(Path("unopened.duckdb"))
    semantic = replace(
        semantic,
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
                BackendRegistration("duckdb", False, "correlation"),
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


@pytest.mark.parametrize("backend", ["clickhouse"])
def test_production_backend_rejection_has_method_shape_and_repair(backend: BackendName) -> None:
    logical = _sources(backend).observe(REVENUE)
    with pytest.raises(DatasetCompilationError) as caught:
        place(logical)
    error = caught.value
    assert error.received is not None
    assert f"backend={backend}" in error.received
    assert "shape=population/entity-membership@v1" in error.received
    assert "session.population" in error.received
    assert error.repair is not None and "duckdb" in error.repair.action


def test_kendall_is_preparation_only_and_cannot_authorize_full_source() -> None:
    logical = (
        _sources("duckdb")
        .observe([REVENUE, ref.metric("sales.mean_amount")])
        .correlate(method="kendall")
    )
    registered = registry.implementation(logical)
    binding = source_binding(logical)
    assert not source_eligible(registered, (binding,), binding)
    assert source_eligible(registered, (binding,), binding, preparation=True)
    graph = place(logical)
    assert len(graph.steps) == 2
    step = graph.steps[0]
    assert isinstance(step, SourceStep)
    assert step.operation == "correlation" and not step.implementation.source
    assert graph.local_steps[0].implementation.local_method == "metric.correlate"
    with pytest.raises(DatasetCompilationError, match="inconsistent source step"):
        replace(step, operation="source")
    with pytest.raises(DatasetCompilationError, match="inconsistent source step"):
        replace(step, binding=replace(binding, adapter="postgres"))


@pytest.mark.parametrize("method", ["pearson", "spearman"])
def test_source_and_preparation_are_independent_capabilities(method: CorrelationMethod) -> None:
    logical = (
        _sources("duckdb")
        .observe([REVENUE, ref.metric("sales.mean_amount")])
        .correlate(method=method)
    )
    graph = place(logical)
    assert len(graph.steps) == 1
    step = graph.steps[0]
    assert isinstance(step, SourceStep) and step.operation == "source"
    assert step.implementation.source and step.implementation.preparation == "correlation"


def test_distribution_uses_explicit_preparation_without_full_source_registration() -> None:
    from tests.lazy_distribution_fixtures import CHANNEL, METRIC, make_distribution_registry

    semantic, sidecar = make_distribution_registry()
    sources = make_lazy_sources(
        semantic_registry=semantic,
        sidecar=sidecar,
        session_id="distribution-dispatch",
        store_id="distribution-dispatch",
        action_port=NoIoActionPort(),
    )
    metric = sources.observe(METRIC).with_dimensions(CHANNEL).aggregate()
    logical = metric.compare(metric).attribute(axes=(CHANNEL,))
    registered = registry.implementation(logical)
    binding = source_binding(logical)
    assert not source_eligible(registered, (binding,), binding)
    assert source_eligible(registered, (binding,), binding, preparation=True)
    graph = place(logical)
    assert len(graph.steps) == 2
    step = graph.steps[0]
    assert isinstance(step, SourceStep) and step.operation == "distribution"
    assert graph.local_steps[0].implementation.local_method == "delta.attribute"


def test_backend_collection_does_not_expand_local_shape_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = registry.implementation

    def no_preparation(value: LogicalDataset) -> ImplementationRegistration:
        registration = original(value)
        return (
            replace(registration, backends=())
            if registration.operator_id == "metric.correlate"
            else registration
        )

    monkeypatch.setattr(registry, "implementation", no_preparation)
    observed = _sources("duckdb").observe([REVENUE, ref.metric("sales.mean_amount")])
    with pytest.raises(DatasetCompilationError, match="source-required Entity correlation"):
        place(observed.correlate(method="kendall"))
    graph = place(
        observed.with_dimensions(ref.dimension("sales.customers.region"))
        .aggregate()
        .correlate(method="kendall")
    )
    assert graph.local_steps[0].implementation.local_method == "metric.correlate"


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


@pytest.mark.parametrize("backend", ["duckdb", "postgres", "mysql", "sqlite", "trino"])
def test_execute_contract_routes_to_help_without_claiming_backend_admission(
    capsys: pytest.CaptureFixture[str],
    backend: BackendName,
) -> None:
    import marivo

    coordinator = marivo.help
    assert callable(coordinator)
    logical = _sources(backend).observe(REVENUE)
    contract = logical.contract().render(max_output_bytes=None)
    assert "dataset.execute(); marivo.help('analysis.actions.execute')" in contract
    coordinator(logical.execute)
    bound_help = capsys.readouterr().out
    coordinator("analysis.actions.execute")
    assert bound_help == capsys.readouterr().out
    assert "admitted PostgreSQL, MySQL, SQLite and Trino scalar Metrics" in bound_help


@pytest.mark.runtime
def test_unplaceable_distribution_keeps_preparation_repair(tmp_path: Path) -> None:
    from marivo.analysis.materialization.admission import DatasetRuntime
    from tests.lazy_distribution_fixtures import (
        CHANNEL,
        METRIC,
        make_distribution_registry,
        seed_distribution_database,
    )

    database = tmp_path / "warehouse.duckdb"
    seed_distribution_database(database)
    semantic, sidecar = make_distribution_registry(database)
    runtime = DatasetRuntime.create(tmp_path, "distribution-repair")
    sources = runtime.sources(semantic_registry=semantic, sidecar=sidecar)
    metric = sources.observe(METRIC).with_dimensions(CHANNEL).aggregate()
    retained_delta = metric.compare(metric).execute()
    logical = retained_delta.attribute(axes=(CHANNEL,))
    # Without an admitted retained-reader binding, the input is local-only.
    with pytest.raises(DatasetCompilationError) as caught:
        place(logical)
    error = caught.value
    assert error.expected == "the registered source-produced coalition preparation input"
    assert (
        error.received is not None and "source-required distribution preparation" in error.received
    )
    assert error.repair is not None
    assert "duckdb distribution preparation" in error.repair.action
    assert "one matching domain" in error.repair.action
    assert isinstance(error.__cause__, DatasetCompilationError)


@pytest.mark.parametrize(
    "backend", ["duckdb", "postgres", "mysql", "sqlite", "trino", "clickhouse"]
)
@pytest.mark.runtime
def test_retained_input_inherits_only_admitted_duckdb_source(
    tmp_path: Path, backend: BackendName, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization import admission

    runtime, sources, _ = setup_local(tmp_path)
    retained = sources.observe(REVENUE).aggregate().execute()
    logical = retained.compare(sources.observe(REVENUE).aggregate())
    # Isolate physical reader admission from semantic Metric compatibility.
    # Remote execution remains disabled; inspect the callback before placement.
    original_binding = source_binding

    def candidate(value: LogicalDataset) -> SourceBinding:
        return replace(original_binding(value), adapter=backend)

    monkeypatch.setattr(admission, "source_binding", candidate)
    observed: list[ExecutionBinding | None] = []

    class InspectedError(Exception):
        pass

    def inspect(
        value: LogicalDataset,
        *,
        artifact_binding: Callable[[MaterializedDataset], ExecutionBinding | None] | None = None,
    ) -> PhysicalStageGraph:
        assert value is logical and artifact_binding is not None
        observed.append(artifact_binding(retained))
        raise InspectedError

    monkeypatch.setattr(admission, "place", inspect)
    with pytest.raises(InspectedError):
        logical.execute()
    assert len(observed) == 1
    binding = observed[0]
    if backend == "duckdb":
        assert isinstance(binding, SourceBinding) and binding.adapter == "duckdb"
    else:
        assert isinstance(binding, ParquetBinding) and binding.adapter == "duckdb"
    assert runtime.last_run_ref is None
    assert runtime.statistics.events.get("profile_resolution", 0) == 0


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
def test_selected_execution_owner_admits_before_connect_and_binds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization import admission

    runtime, sources, _ = setup_local(tmp_path)
    logical = sources.observe(REVENUE).aggregate()
    from marivo.analysis.materialization import execution as execution_module

    original = execution_module.resolve_execution
    execution = original("duckdb")
    assert execution is not None
    calls: list[str] = []

    def admit(value: LogicalDataset) -> None:
        assert value is logical and runtime.last_run_ref is None
        calls.append("admit")
        execution.admit(value)

    def bind(
        candidate: object, *, reserve: Callable[[str], None], run_ref: str
    ) -> ExecutionAdapter:
        assert calls == ["admit", "connect"]
        calls.append("bind")
        return execution.bind(candidate, reserve=reserve, run_ref=run_ref)

    selected = replace(execution, admit=admit, bind=bind)
    monkeypatch.setattr(
        execution_module,
        "resolve_execution",
        lambda name: selected if name == "duckdb" else original(name),
    )
    connect = _build_backend_from_effective

    def opened(
        datasource: DatasourceIR, kwargs: EffectiveDatasourceKwargs, *, read_only: bool
    ) -> BuiltDatasourceBackend:
        assert calls == ["admit"]
        calls.append("connect")
        return connect(datasource, kwargs, read_only=read_only)

    monkeypatch.setattr(admission, "_build_backend_from_effective", opened)
    assert logical.execute().to_pandas().revenue.tolist() == [147.0]
    assert calls == ["admit", "connect", "bind"]


@pytest.mark.runtime
def test_missing_execution_owner_rejects_before_run_and_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.analysis.materialization import admission
    from marivo.analysis.materialization.errors import MaterializationError
    from tests.lazy_adapter_runtime_worker import forbidden, snapshot

    runtime, sources, _ = setup_local(tmp_path)
    before = snapshot(runtime)
    monkeypatch.setattr(registry, "backend_execution", lambda _: None)
    monkeypatch.setattr(admission, "_build_backend_from_effective", forbidden)
    with pytest.raises(MaterializationError):
        sources.observe(REVENUE).aggregate().execute()
    assert runtime.last_run_ref is None
    assert snapshot(runtime) == before
    assert runtime.statistics.events == {"reconciliation": 1}
