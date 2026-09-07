"""Real-source execution, exact-key reuse and retained terminal acceptance."""

from __future__ import annotations

import re
import sqlite3
import traceback
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from marivo.analysis import grain
from marivo.analysis.datasets.handles import MaterializedScanLeafHandle
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import (
    CollectionLimitError,
    IntegrityError,
    MaterializationError,
)
from marivo.analysis.observation.contracts import ObservationRuntimeOwner
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.population import MaterializedPopulationDataset
from marivo.analysis.observation.predicates import gt
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from tests.lazy_execution_fixtures import (
    controlled_json_source,
    make_execution_registry,
    seed_execution_database,
)

_METRICS = tuple(
    ref.metric(f"sales.{name}")
    for name in (
        "revenue",
        "order_count",
        "mean_amount",
        "weighted_amount",
        "conversion_rate",
        "cross_root_ratio",
    )
)


def _setup(
    project: Path, *, event: Callable[[str], None] | None = None
) -> tuple[DatasetRuntime, LazySources, Path]:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(project, "execution-acceptance", event=event)
    return runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar), database


def _run_count(runtime: DatasetRuntime) -> int:
    connection = sqlite3.connect(runtime.store.db_path.as_uri() + "?mode=ro", uri=True)
    try:
        count: object = connection.execute("SELECT count(*) FROM analysis_action_runs").fetchone()[
            0
        ]
        assert isinstance(count, int)
        return count
    finally:
        connection.close()


def _metadata(runtime: DatasetRuntime) -> str:
    connection = sqlite3.connect(runtime.store.db_path.as_uri() + "?mode=ro", uri=True)
    try:
        return "\n".join(connection.iterdump())
    finally:
        connection.close()


def _exception_text(error: BaseException) -> str:
    pending = [error]
    seen: set[int] = set()
    parts: list[str] = []
    while pending:
        selected = pending.pop()
        if id(selected) in seen:
            continue
        seen.add(id(selected))
        parts.extend((repr(selected), str(selected), "".join(traceback.format_exception(selected))))
        if selected.__cause__ is not None:
            pending.append(selected.__cause__)
        if selected.__context__ is not None:
            pending.append(selected.__context__)
    return "\n".join(parts)


def test_six_metrics_publish_complete_bundle_and_recompute_selected_states(tmp_path: Path) -> None:
    runtime, sources, _ = _setup(tmp_path)
    dataset = sources.observe(
        _METRICS, population=sources.population(ref.entity("sales.customers"))
    )
    fingerprint = dataset.definition_fingerprint
    materialized = dataset.execute()
    assert isinstance(materialized, MaterializedMetricDataset)
    assert dataset.state.kind == "logical"
    assert dataset.definition_fingerprint == fingerprint == materialized.definition_fingerprint
    assert materialized.row_contract == dataset.row_contract
    assert materialized.row_set_contract == dataset.row_set_contract
    assert materialized.state.producing_run_ref == runtime.last_run_ref
    assert materialized.state.artifact_session_ref == runtime.session_ref
    record = runtime.store.artifact(materialized.state.artifact_ref.ref)
    assert record is not None
    assert record.evidence.finding_count == 0
    assert len(record.descriptor.retained_parts) == 6
    run = runtime.store.run(record.producing_run_ref)
    assert run is not None and run.lifecycle == "succeeded"
    assert runtime.store.resources(runtime.session_ref) == ()
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.validation_queries > 0
    assert runtime.statistics.transferred_rows == 4
    assert runtime.statistics.transferred_bytes > 0
    assert materialized.findings().items == ()
    assert materialized.evidence_digest.fingerprint == record.evidence.evidence_digest
    result = materialized.to_pandas()
    assert result["entity_identity"].tolist() == [(1,), (2,), (3,), (4,)]
    assert result.loc[0, "revenue"] == 40
    assert result.loc[0, "mean_amount"] == 20
    assert result.loc[0, "weighted_amount"] == 25
    assert result.loc[0, "conversion_rate"] == 20
    assert result.loc[0, "cross_root_ratio"] == 15 / 40
    assert result.loc[1, "cross_root_ratio"] == 0.5
    assert pd.isna(result.loc[2, "weighted_amount"])
    assert pd.isna(result.loc[3, "revenue"])
    assert result.loc[3, "order_count"] == 0
    reduced = dataset.where(gt(ref.metric("sales.revenue"), 10)).aggregate().execute().to_pandas()
    assert reduced.loc[0, "revenue"] == 140
    assert reduced.loc[0, "mean_amount"] == pytest.approx(140 / 3)
    assert reduced.loc[0, "weighted_amount"] == 50
    assert reduced.loc[0, "cross_root_ratio"] == pytest.approx(65 / 140)


@pytest.mark.parametrize(
    "dimensions,time_axis,reduced",
    [(d, t, r) for d in (False, True) for t in (False, True) for r in (False, True)],
)
def test_eight_shapes_execute_and_cold_recover_same_contract(
    tmp_path: Path, dimensions: bool, time_axis: bool, reduced: bool
) -> None:
    runtime, sources, _ = _setup(tmp_path)
    dataset = sources.observe(ref.metric("sales.revenue"))
    if dimensions:
        dataset = dataset.with_dimensions(ref.dimension("sales.customers.region"))
    if time_axis:
        dataset = dataset.with_time_axis(
            ref.time_dimension("sales.orders.order_time"), grain=grain("day")
        )
    if reduced:
        dataset = dataset.aggregate()
    materialized = dataset.execute()
    recovered_runtime = DatasetRuntime.open(tmp_path, runtime.session_ref)
    recovered = recovered_runtime.artifact(materialized.state.artifact_ref)
    assert isinstance(recovered, MaterializedMetricDataset)
    assert recovered.row_contract == dataset.row_contract
    assert recovered.row_set_contract == dataset.row_set_contract
    assert recovered.state == materialized.state
    assert isinstance(recovered._root, MaterializedScanLeafHandle)
    assert type(recovered._owner) is ObservationRuntimeOwner
    assert not hasattr(recovered._owner, "semantic_registry")
    frame = recovered.to_pandas()
    assert tuple(frame.columns) == tuple(field.name for field in dataset.schema.columns)
    assert frame["revenue"].sum() == 147
    if dimensions:
        assert frame["region"].isna().any()
    if time_axis:
        assert frame["order_time"].isna().any()
    assert recovered_runtime.statistics.primary_queries == 0


def test_exact_key_hit_has_no_admission_or_origin_after_source_disappears(tmp_path: Path) -> None:
    runtime, sources, database = _setup(tmp_path)
    dataset = sources.observe(ref.metric("sales.revenue")).aggregate()
    materialized = dataset.execute()
    admitted = _run_count(runtime)
    database.rename(tmp_path / "warehouse.unavailable")
    hit = dataset.execute()
    assert hit.state.artifact_ref == materialized.state.artifact_ref
    assert _run_count(runtime) == admitted == 1
    assert runtime.statistics.primary_queries == 0
    assert runtime.statistics.validation_queries == 0
    reopened = DatasetRuntime.open(tmp_path, runtime.session_ref)
    restored = reopened.artifact(materialized.state.artifact_ref.ref)
    assert restored.to_pandas().loc[0, "revenue"] == 147
    assert reopened.statistics.primary_queries == 0


def test_retained_reads_are_bounded_ordered_and_isolated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime, sources, database = _setup(tmp_path)
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            "INSERT INTO orders (id, amount) SELECT 100 + i, 1 FROM range(40) AS x(i)"
        )
    finally:
        connection.close()
    logical = sources.observe(ref.metric("sales.revenue"))
    materialized = logical.execute()
    database.rename(tmp_path / "warehouse.unavailable")
    first = materialized.to_pandas()
    second = materialized.to_pandas()
    pd.testing.assert_frame_equal(first, second)
    first.loc[0, "revenue"] = -900
    assert materialized.to_pandas().loc[0, "revenue"] == 10
    materialized.show(max_output_bytes=512)
    shown = capsys.readouterr().out
    assert len(shown.encode("utf-8")) <= 512
    materialized.show(max_output_bytes=512)
    assert capsys.readouterr().out == shown
    materialized.show()
    default_output = capsys.readouterr().out
    assert len(default_output.encode("utf-8")) <= 8192
    assert re.search(r"\b139\b", default_output) is None
    assert runtime.statistics.primary_queries == 1
    assert second["entity_identity"].tolist() == [
        (i,) for i in (1, 2, 3, 4, 5, 6, *range(100, 140))
    ]


def test_preview_guidance_and_collection_use_the_selected_read_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sources, database = _setup(tmp_path)
    materialized = sources.observe(ref.metric("sales.revenue")).execute()
    database.rename(tmp_path / "warehouse.unavailable")
    monkeypatch.setattr(
        admission, "_READ_POLICY", replace(admission._READ_POLICY, preview_rows=2, max_rows=1)
    )
    materialized.show()
    shown = capsys.readouterr().out
    assert "Preview: 2 of 6 rows (maximum 2)" in shown
    assert len(shown.splitlines()[3:]) == 2
    with pytest.raises(CollectionLimitError, match="row count"):
        materialized.to_pandas()
    assert runtime.statistics.primary_queries == 1


def test_cold_recovery_is_metadata_only_and_selected_corrupt_backing_fails(tmp_path: Path) -> None:
    runtime, sources, database = _setup(tmp_path)
    materialized = sources.population(ref.entity("sales.composite")).execute()
    assert isinstance(materialized, MaterializedPopulationDataset)
    record = runtime.store.artifact(materialized.state.artifact_ref.ref)
    assert record is not None
    original_run = runtime.store.run(record.producing_run_ref)
    receipt = record.descriptor.storage_receipt
    data = tmp_path / receipt.project_relative_path / receipt.file_manifest[0].relative_path
    data.rename(data.with_suffix(".unavailable"))
    database.rename(tmp_path / "warehouse.unavailable")
    restored = DatasetRuntime.open(tmp_path, runtime.session_ref).artifact(
        materialized.state.artifact_ref
    )
    assert isinstance(restored, MaterializedPopulationDataset)
    with pytest.raises(IntegrityError):
        restored.to_pandas()
    assert runtime.store.run(record.producing_run_ref) == original_run
    assert _run_count(runtime) == 1


def test_json_capture_survives_scope_exit_and_changed_capture_separates_key(tmp_path: Path) -> None:
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    with controlled_json_source() as server:
        registry, sidecar = make_execution_registry(database, api_url=server.url)
        runtime = DatasetRuntime.create(tmp_path, "json-captures")
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        entity = ref.entity("sales.api")
        with sources.source_bindings({entity: {"tenant": "alpha"}}):
            alpha = sources.observe(ref.metric("sales.api_value"))
        assert server.requests == []
        first = alpha.execute()
        assert first.to_pandas().loc[0, "api_value"] == 10
        requests = len(server.requests)
        assert requests == 1
        assert alpha.execute().state.artifact_ref == first.state.artifact_ref
        with sources.source_bindings({entity: {"tenant": "alpha"}}):
            same = sources.observe(ref.metric("sales.api_value"))
        assert same.definition_fingerprint == alpha.definition_fingerprint
        assert same.execute().state.artifact_ref == first.state.artifact_ref
        assert len(server.requests) == requests
        with sources.source_bindings({entity: {"tenant": "beta"}}):
            beta = sources.observe(ref.metric("sales.api_value"))
        assert beta.definition_fingerprint != alpha.definition_fingerprint
        second = beta.execute()
        assert second.state.artifact_ref != first.state.artifact_ref
        assert second.to_pandas().loc[0, "api_value"] == 25
        assert len(server.requests) == requests + 1
        assert runtime.statistics.source_fences == 1
        assert runtime.statistics.primary_queries == 1
        assert _run_count(runtime) == 2
        restored = DatasetRuntime.open(tmp_path, runtime.session_ref).artifact(
            first.state.artifact_ref
        )
        assert restored.to_pandas().loc[0, "api_value"] == 10
        assert len(server.requests) == requests + 1


def test_captured_values_and_injected_errors_are_redacted_from_every_chain(tmp_path: Path) -> None:
    canary = "private-tenant-canary-79c941e0"
    database = tmp_path / "warehouse.duckdb"
    seed_execution_database(database)
    with controlled_json_source(tenant_values={canary: 12.0}) as server:
        registry, sidecar = make_execution_registry(database, api_url=server.url)
        runtime = DatasetRuntime.create(tmp_path, "json-redaction")
        sources = runtime.sources(semantic_registry=registry, sidecar=sidecar)
        with sources.source_bindings({ref.entity("sales.api"): {"tenant": canary}}):
            dataset = sources.observe(ref.metric("sales.api_value"))
        materialized = dataset.execute()
        assert materialized.to_pandas().loc[0, "api_value"] == 12
        assert canary not in _metadata(runtime)
        assert canary not in repr(dataset) + repr(dataset._root) + repr(materialized)
        assert canary not in repr(runtime.statistics)
        statement_kinds = [kind for kind, _ in runtime.statistics.statements]
        assert statement_kinds.count("source_fence_reader") == 1
        assert statement_kinds.count("source_fence") == 1
        assert statement_kinds.count("primary") == runtime.statistics.primary_queries == 1
        assert statement_kinds.count("transfer_guard") == 1
        validation_kinds = {kind for kind in statement_kinds if kind.startswith("validation:")}
        assert validation_kinds == {
            "validation:sales.api.identity_non_null",
            "validation:sales.api.source_row_unique",
            "validation:dataset.final_row_key_unique",
        }
        record = runtime.store.artifact(materialized.state.artifact_ref.ref)
        assert record is not None
        assert validation_kinds == {
            "validation:" + name
            for name, violations in record.descriptor.population_authority.validation_results
            if violations == 0
        }
        assert runtime.statistics.validation_queries == sum(
            kind.startswith("validation:") or kind in {"source_schema", "transfer_guard"}
            for kind in statement_kinds
        )

        def fail_source(point: str) -> None:
            if point == "source_statement":
                raise RuntimeError(f"query argument={canary}")

        failing = DatasetRuntime.create(tmp_path, "json-redaction-failure", event=fail_source)
        failure_sources = failing.sources(semantic_registry=registry, sidecar=sidecar)
        with failure_sources.source_bindings({ref.entity("sales.api"): {"tenant": canary}}):
            fail_dataset = failure_sources.observe(ref.metric("sales.api_value"))
        with pytest.raises(MaterializationError) as captured:
            fail_dataset.execute()
        assert canary not in _exception_text(captured.value)
        assert canary not in _metadata(failing)
        assert canary not in repr(failing.statistics)
        assert failing.last_run_ref is not None
        run = failing.store.run(failing.last_run_ref)
        assert run is not None and run.lifecycle == "failed"


def test_population_membership_and_observation_scope_remain_independent(tmp_path: Path) -> None:
    from marivo.analysis import time_scope
    from marivo.analysis.observation.predicates import eq

    runtime, sources, _ = _setup(tmp_path)
    population = sources.population(
        ref.entity("sales.orders"),
        time_scope=time_scope(start="2026-02-02", end="2026-02-04"),
    ).where(eq(ref.dimension("sales.customers.region"), "EU"))
    dataset = sources.observe(
        ref.metric("sales.revenue"),
        population=population,
        time_scope=time_scope(start="2026-02-03", end="2026-02-05"),
    )
    materialized = dataset.execute()
    record = runtime.store.artifact(materialized.state.artifact_ref.ref)
    assert record is not None
    assert (
        record.descriptor.population_authority.definition_fingerprint
        == population.definition_fingerprint
    )
    rows = materialized.to_pandas()
    assert rows["entity_identity"].tolist() == [(1,), (2,)]
    assert pd.isna(rows.loc[0, "revenue"])
    assert rows.loc[1, "revenue"] == 30
    assert runtime.statistics.primary_queries == 1


@pytest.mark.parametrize("entity_name", ["snapshots", "validity"])
def test_versioned_population_publishes_exact_selected_membership(
    tmp_path: Path, entity_name: str
) -> None:
    from marivo.analysis import time_scope

    runtime, sources, _ = _setup(tmp_path)
    logical = sources.population(
        ref.entity(f"sales.{entity_name}"),
        time_scope=time_scope(start="2026-02-01", end="2026-03-01"),
    )
    materialized = logical.execute()
    assert isinstance(materialized, MaterializedPopulationDataset)
    assert materialized.to_pandas()["entity_identity"].tolist() == [(1,), (2,)]
    record = runtime.store.artifact(materialized.state.artifact_ref.ref)
    assert record is not None
    assert record.descriptor.population_authority.version_selection is not None
    restored = DatasetRuntime.open(tmp_path, runtime.session_ref).artifact(
        materialized.state.artifact_ref
    )
    pd.testing.assert_frame_equal(restored.to_pandas(), materialized.to_pandas())


def test_empty_source_execution_still_commits_schema_and_scalar_zero_count(tmp_path: Path) -> None:
    from marivo.analysis.observation.predicates import eq

    runtime, sources, _ = _setup(tmp_path)
    population = sources.population(ref.entity("sales.orders")).where(
        eq(ref.dimension("sales.customers.region"), "absent")
    )
    empty = sources.observe(
        (ref.metric("sales.revenue"), ref.metric("sales.order_count")), population=population
    )
    committed = empty.execute()
    assert committed.state.realized_row_count == 0
    assert committed.to_pandas().empty
    assert tuple(committed.to_pandas().columns) == ("entity_identity", "revenue", "order_count")
    scalar = empty.aggregate().execute().to_pandas()
    assert len(scalar) == 1
    assert pd.isna(scalar.loc[0, "revenue"])
    assert scalar.loc[0, "order_count"] == 0
    assert _run_count(runtime) == 2


def test_live_source_type_mismatch_fails_before_computation(tmp_path: Path) -> None:
    from dataclasses import replace

    from marivo.datasource.ir import TableSourceIR

    runtime, _, database = _setup(tmp_path)
    registry, sidecar = make_execution_registry(database)
    entity = registry.entities["sales.orders"]
    assert isinstance(entity.source, TableSourceIR)
    source = replace(
        entity.source,
        columns=tuple(
            (name, replace(binding, data_type="int64") if name == "amount" else binding)
            for name, binding in entity.source.columns
        ),
    )
    changed = replace(
        registry, entities={**registry.entities, entity.semantic_id: replace(entity, source=source)}
    )
    changed.freeze()
    sources = runtime.sources(semantic_registry=changed, sidecar=sidecar)
    dataset = sources.observe(ref.metric("sales.revenue")).aggregate()
    with pytest.raises(MaterializationError) as captured:
        dataset.execute()
    assert captured.value.stage == "output_validation"
    assert runtime.statistics.primary_queries == 0
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed"
    assert run.failure is not None and run.failure.phase == "output_validation"
    assert _run_count(runtime) == 1


def test_nested_metric_membership_keeps_latest_selected_identity_authority(tmp_path: Path) -> None:
    runtime, sources, _ = _setup(tmp_path)
    first = sources.observe(
        ref.metric("sales.revenue"),
        population=sources.population(ref.entity("sales.customers")),
    ).where(gt(ref.metric("sales.revenue"), 10))
    second = sources.observe(ref.metric("sales.mean_amount"), population=first).where(
        gt(ref.metric("sales.mean_amount"), 20)
    )
    logical = sources.observe(ref.metric("sales.revenue"), population=second)
    materialized = logical.execute()
    frame = materialized.to_pandas()
    assert frame["entity_identity"].tolist() == [(2,)]
    assert frame.loc[0, "revenue"] == 100
    record = runtime.store.artifact(materialized.state.artifact_ref.ref)
    assert record is not None
    assert (
        record.descriptor.population_authority.definition_fingerprint
        == second.definition_fingerprint
    )
    assert runtime.statistics.primary_queries == 1


def test_zero_and_null_metric_components_preserve_all_population_rows(tmp_path: Path) -> None:
    runtime, sources, database = _setup(tmp_path)
    connection = duckdb.connect(str(database))
    try:
        connection.execute("INSERT INTO lines (id, order_id, amount) VALUES (6, 5, 9)")
    finally:
        connection.close()
    logical = sources.observe(
        tuple(
            ref.metric(f"sales.{name}")
            for name in (
                "conversion_rate",
                "cross_root_ratio",
                "weighted_amount",
                "order_count",
            )
        ),
        population=sources.population(ref.entity("sales.orders")),
    )
    frame = logical.execute().to_pandas()
    assert frame["entity_identity"].tolist() == [(i,) for i in range(1, 7)]
    assert frame.loc[4, "conversion_rate"] == 0
    assert pd.isna(frame.loc[4, "cross_root_ratio"])
    assert pd.isna(frame.loc[3, "conversion_rate"])
    assert frame.loc[3, "order_count"] == 0
    assert pd.isna(frame.loc[4, "weighted_amount"])
    assert pd.isna(frame.loc[5, "weighted_amount"])
    assert frame.loc[2, "weighted_amount"] == 100
    assert runtime.statistics.primary_queries == 1


def test_semantic_dependency_digest_covers_frozen_coordinate_definitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from marivo.analysis.observation import contracts
    from marivo.analysis.observation.contracts import semantic_dependency_digest
    from marivo.semantic._expression_binding import ExpressionBody

    runtime, sources, database = _setup(tmp_path)
    region_ref = ref.dimension("sales.customers.region")
    by_region = sources.observe(ref.metric("sales.revenue")).with_dimensions(region_ref).aggregate()
    by_channel = (
        sources.observe(ref.metric("sales.revenue"))
        .with_dimensions(ref.dimension("sales.orders.channel"))
        .aggregate()
    )
    registry, sidecar = make_execution_registry(database)
    body = ExpressionBody.for_column("channel")
    changed_dimension = replace(
        registry.dimensions[region_ref.path],
        source_column="channel",
        body_ast_hash=body.body_ast_hash,
    )
    changed_registry = replace(
        registry, dimensions={**registry.dimensions, region_ref.path: changed_dimension}
    )
    changed_registry.freeze()
    changed_sidecar = replace(sidecar, bodies={**sidecar.bodies, region_ref: body})
    changed_sources = runtime.sources(semantic_registry=changed_registry, sidecar=changed_sidecar)
    changed_region = (
        changed_sources.observe(ref.metric("sales.revenue")).with_dimensions(region_ref).aggregate()
    )
    datasets = (by_region, by_channel, changed_region)
    fingerprints = tuple(dataset.definition_fingerprint for dataset in datasets)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("dependency publication must not traverse the catalog or source")

    with monkeypatch.context() as guard:
        guard.setattr(contracts, "source_owner_of", forbidden)
        guard.setattr(contracts, "dependency_digest", forbidden)
        digests = tuple(semantic_dependency_digest(dataset) for dataset in datasets)
    assert len(set(digests)) == 3
    assert tuple(dataset.definition_fingerprint for dataset in datasets) == fingerprints
    assert runtime.statistics.events == {}
    for dataset, expected in zip(datasets, digests, strict=True):
        materialized = dataset.execute()
        record = runtime.store.artifact(materialized.state.artifact_ref.ref)
        assert record is not None and record.descriptor.semantic_dependency_digest == expected

    api = ref.entity("sales.api")
    with sources.source_bindings({api: {"tenant": "capture-canary-one"}}):
        one = sources.observe(ref.metric("sales.api_value"))
    with sources.source_bindings({api: {"tenant": "capture-canary-two"}}):
        two = sources.observe(ref.metric("sales.api_value"))
    assert one.definition_fingerprint != two.definition_fingerprint
    assert semantic_dependency_digest(one) == semantic_dependency_digest(two)
