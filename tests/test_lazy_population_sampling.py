"""Private sampling policy, physical single-realization, and durable receipt acceptance."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import duckdb
import ibis.expr.types as ir
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from marivo.analysis.compiler import compile_dataset
from marivo.analysis.compiler.nodes import CompiledSampleFence
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import (
    DefinitionInput,
    LogicalInputToken,
    LogicalRootHandle,
    _sharing_occurrences,
)
from marivo.analysis.materialization import admission
from marivo.analysis.materialization import sampling as sampling_runtime
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import (
    LocalReceipt,
    SamplingRealization,
    decode_descriptor,
    decode_sampling,
    encode_descriptor,
    sampling_payload,
)
from marivo.analysis.materialization.duckdb_execution import DuckDBExecutionAdapter
from marivo.analysis.materialization.errors import IntegrityError, MaterializationError
from marivo.analysis.materialization.execution import ExecutionAdapter
from marivo.analysis.materialization.storage import sampling_state_read, validate_sampling_state
from marivo.analysis.observation.predicates import eq
from marivo.analysis.observation.sampling import EntitySamplingPolicy, engine_sample
from marivo.analysis.session._lazy_sources import LazySources
from marivo.refs import ref
from tests.lazy_execution_fixtures import (
    execution_fixture,
    make_execution_registry,
    seed_execution_database,
)
from tests.lazy_materialization_crash_worker import snapshot
from tests.lazy_observation_fixtures import make_sources

pytestmark = pytest.mark.runtime


def _setup(
    project: Path, *, event: Callable[[str], None] | None = None
) -> tuple[DatasetRuntime, LazySources, Path]:
    database = project / "warehouse.duckdb"
    seed_execution_database(database)
    registry, sidecar = make_execution_registry(database)
    runtime = DatasetRuntime.create(project, "sampling", event=event)
    return runtime, runtime.sources(semantic_registry=registry, sidecar=sidecar), database


@pytest.mark.parametrize("value", [True, False, 0, -1, 2.0, "2", None])
def test_target_rows_has_exact_positive_integer_contract(value: int) -> None:
    with pytest.raises(DatasetConstructionError):
        engine_sample(target_rows=value)


@pytest.mark.parametrize("value", [True, False, 2.0, "2"])
def test_seed_is_an_exact_integer_request(value: int) -> None:
    with pytest.raises(DatasetConstructionError):
        engine_sample(target_rows=2, seed=value)


def test_policy_repr_stays_bounded_for_large_exact_integer_requests() -> None:
    policy = engine_sample(target_rows=1 << 20_000, seed=1 << 20_000)
    assert repr(policy) == "EntitySamplingPolicy(target_rows=<large integer>, seeded=True)"
    assert repr(engine_sample(target_rows=100)) == (
        "EntitySamplingPolicy(target_rows=100, seeded=False)"
    )


def _sampling_receipt(*, ordinal: int = 0, seed: int | None = None) -> SamplingRealization:
    return SamplingRealization(ordinal, "ds_" + "a" * 64, "ds_" + "b" * 64, 2, seed, 2, "c" * 64)


@pytest.mark.parametrize("seed", [-1, True, 2**31])
def test_sampling_decoder_rejects_unsupported_physical_seed(seed: int) -> None:
    with pytest.raises(IntegrityError):
        decode_sampling(sampling_payload((_sampling_receipt(seed=seed),)))


@pytest.mark.parametrize("seed", [None, 0, 2**31 - 1])
def test_sampling_decoder_preserves_supported_physical_seed(seed: int | None) -> None:
    receipt = (_sampling_receipt(seed=seed),)
    assert decode_sampling(sampling_payload(receipt)) == receipt


def test_sampling_decoder_bounds_realization_metadata_count() -> None:
    receipts = tuple(_sampling_receipt(ordinal=ordinal) for ordinal in range(64))
    assert decode_sampling(sampling_payload(receipts)) == receipts
    for unsupported in ((), (*receipts, _sampling_receipt(ordinal=64))):
        with pytest.raises(IntegrityError):
            decode_sampling(sampling_payload(unsupported))


def test_sampling_is_sealed_pure_and_establishes_distinct_handles() -> None:
    with pytest.raises(DatasetConstructionError):
        EntitySamplingPolicy(target_rows=2, seed=None)
    policy = engine_sample(target_rows=2, seed=42)
    field_name = "target_rows"
    with pytest.raises(FrozenInstanceError):
        setattr(policy, field_name, 3)
    source = make_sources()
    target = source.population(ref.entity("sales.customers"))
    first, second = target.sample(policy), target.sample(engine_sample(target_rows=2, seed=42))
    assert first.definition_fingerprint == second.definition_fingerprint
    assert first.definition_fingerprint != target.definition_fingerprint
    assert first.row_contract == target.row_contract
    assert first.row_set_contract == target.row_set_contract
    assert isinstance(first._root, LogicalRootHandle)
    assert isinstance(second._root, LogicalRootHandle)
    assert first._root.realizations[0].handle is not second._root.realizations[0].handle
    shared = (
        DefinitionInput("left", LogicalInputToken(first.definition_fingerprint), first._root),
        DefinitionInput("right", LogicalInputToken(first.definition_fingerprint), first._root),
    )
    independent = (
        shared[0],
        DefinitionInput("right", LogicalInputToken(second.definition_fingerprint), second._root),
    )
    assert [ordinal for _, ordinal in _sharing_occurrences(shared, ())] == [0, 0]
    assert [ordinal for _, ordinal in _sharing_occurrences(independent, ())] == [0, 1]
    with pytest.raises(DatasetConstructionError, match="before sampling"):
        first.where(eq(ref.dimension("sales.customers.region"), "EU"))
    with pytest.raises(DatasetConstructionError, match="second sampling"):
        first.sample(policy)


@pytest.mark.parametrize("entity,target", [("customers", 2), ("composite", 2), ("customers", 20)])
def test_native_entity_sample_publishes_bounded_state(
    tmp_path: Path, entity: str, target: int, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime, source, _ = _setup(tmp_path)
    sampled = source.population(ref.entity(f"sales.{entity}")).sample(
        engine_sample(target_rows=target, seed=42)
    )
    result = sampled.execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    receipt = record.descriptor.sampling_execution
    assert receipt is not None and len(receipt) == 1
    assert receipt[0].target_rows == target
    assert receipt[0].realized_entity_count == min(target, 3 if entity == "composite" else 4)
    assert receipt[0].population_definition_fingerprint == sampled.definition_fingerprint
    assert record.descriptor.retained_parts[0].role == "population_sampling_state"
    assert record.descriptor.retained_parts[0].storage_receipt.realized_row_count == 1
    assert decode_descriptor(encode_descriptor(record.descriptor)) == record.descriptor
    assert len(result.to_pandas()) == receipt[0].realized_entity_count
    assert runtime.statistics.sampling_fences == 1
    assert runtime.statistics.primary_queries == 1
    assert runtime.statistics.events["sampling_reserved"] == 1
    assert runtime.store.resources(runtime.session_ref) == ()
    assert record.evidence.finding_count == 0
    result.show()
    output = capsys.readouterr().out
    assert "Sampling: approximate Entity sample; realizations=1" in output
    assert f"target={target}, realized={receipt[0].realized_entity_count}, seeded=True" in output
    assert "seed=42" not in output and len(output.encode("utf-8")) <= 8192


def test_filtered_sample_is_shared_by_all_metric_branches_and_inherited_after_reduction(
    tmp_path: Path,
) -> None:
    runtime, source, _ = _setup(tmp_path)
    target = source.population(ref.entity("sales.customers")).where(
        eq(ref.dimension("sales.customers.region"), "EU")
    )
    sampled = target.sample(engine_sample(target_rows=2, seed=42))
    dataset = source.observe(
        [ref.metric("sales.revenue"), ref.metric("sales.order_count")], population=sampled
    ).aggregate()
    result = dataset.execute()
    values = result.to_pandas()
    assert values.loc[0, "revenue"] == 47
    assert values.loc[0, "order_count"] == 4
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.sampling_execution is not None
    assert record.descriptor.storage_receipt.realized_row_count == 1
    assert record.descriptor.sampling_execution[0].realized_entity_count == 2
    assert (
        record.descriptor.sampling_execution[0].target_population_definition_fingerprint
        == target.definition_fingerprint
    )
    assert {part.role for part in record.descriptor.retained_parts} >= {"population_sampling_state"}
    assert runtime.statistics.sampling_fences == 1
    statements = runtime.statistics.statements
    sample_sql = next(sql for kind, sql in statements if kind == "sampling_fence")
    assert "WHERE" in sample_sql and "SAMPLE" in sample_sql
    assert len([sql for kind, sql in statements if kind == "sampling_fence"]) == 1


def test_sampling_metadata_rejects_missing_or_mismatched_retained_authority(
    tmp_path: Path,
) -> None:
    runtime, source, _ = _setup(tmp_path)
    result = (
        source.population(ref.entity("sales.customers"))
        .sample(engine_sample(target_rows=2, seed=42))
        .execute()
    )
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    descriptor = record.descriptor
    receipt = descriptor.sampling_execution
    assert receipt is not None
    malformed = (
        replace(descriptor, sampling_execution=None),
        replace(descriptor, retained_parts=()),
        replace(
            descriptor,
            sampling_execution=(replace(receipt[0], realized_entity_count=1),),
        ),
        replace(
            descriptor,
            sampling_execution=(replace(receipt[0], ordinal=1),),
        ),
        replace(
            descriptor,
            sampling_execution=(replace(receipt[0], population_definition_fingerprint="0" * 64),),
        ),
    )
    for candidate in malformed:
        with pytest.raises(IntegrityError):
            decode_descriptor(encode_descriptor(candidate))


def test_empty_eligible_population_produces_zero_member_receipt(tmp_path: Path) -> None:
    runtime, source, _ = _setup(tmp_path)
    target = source.population(ref.entity("sales.customers")).where(
        eq(ref.dimension("sales.customers.region"), "absent")
    )
    result = target.sample(engine_sample(target_rows=2)).execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and record.descriptor.sampling_execution is not None
    assert record.descriptor.sampling_execution[0].realized_entity_count == 0
    assert result.to_pandas().empty


def test_same_session_reconstruction_recovers_sample_without_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, source, _ = _setup(tmp_path)
    result = (
        source.population(ref.entity("sales.customers"))
        .sample(engine_sample(target_rows=2))
        .execute()
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("sample recovery touched the origin")

    monkeypatch.setattr(admission, "_build_backend_from_effective", forbidden)
    original_parquet_file = pq.ParquetFile
    monkeypatch.setattr(pq, "ParquetFile", forbidden)
    recovered = (
        source.population(ref.entity("sales.customers"))
        .sample(engine_sample(target_rows=2))
        .execute()
    )
    assert recovered.state.artifact_ref == result.state.artifact_ref
    assert runtime.statistics.sampling_fences == 0
    assert runtime.statistics.primary_queries == 0
    assert not runtime.statistics.events.get("profile_resolution", 0)
    monkeypatch.setattr(pq, "ParquetFile", original_parquet_file)
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref).artifact(result.state.artifact_ref)
    assert cold.to_pandas().equals(result.to_pandas())


def test_corrupt_sampling_state_blocks_its_validation_but_not_primary_reads(
    tmp_path: Path,
) -> None:
    runtime, source, _ = _setup(tmp_path)
    result = (
        source.population(ref.entity("sales.customers"))
        .sample(engine_sample(target_rows=2))
        .execute()
    )
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    part = next(
        part
        for part in record.descriptor.retained_parts
        if part.role == "population_sampling_state"
    )
    assert isinstance(part.storage_receipt, LocalReceipt)
    path = tmp_path / part.storage_receipt.project_relative_path / "data.parquet"
    path.write_bytes(b"corrupt")
    recovered = DatasetRuntime.open(tmp_path, runtime.session_ref).artifact(
        result.state.artifact_ref
    )
    assert recovered.state.artifact_ref == result.state.artifact_ref
    assert len(recovered.to_pandas()) == 2
    recovered.show()
    with pytest.raises(IntegrityError, match="sampling state"):
        validate_sampling_state(tmp_path, sampling_state_read(record.descriptor))


def test_new_session_executes_its_own_seeded_realization(tmp_path: Path) -> None:
    first, source, database = _setup(tmp_path)
    original = (
        source.population(ref.entity("sales.customers"))
        .sample(engine_sample(target_rows=2, seed=42))
        .execute()
    )
    registry, sidecar = make_execution_registry(database)
    second = DatasetRuntime.create(tmp_path, "independent-sampling")
    other_source = second.sources(semantic_registry=registry, sidecar=sidecar)
    other = (
        other_source.population(ref.entity("sales.customers"))
        .sample(engine_sample(target_rows=2, seed=42))
        .execute()
    )
    assert other.state.artifact_ref != original.state.artifact_ref
    assert second.statistics.sampling_fences == 1
    assert other.to_pandas().equals(original.to_pandas())


@pytest.mark.parametrize("seed,target", [(-1, 2), (2**31, 2), (None, 1_000_000_001)])
def test_unsupported_native_request_rejects_before_run_or_source(
    tmp_path: Path, seed: int | None, target: int
) -> None:
    runtime, source, _ = _setup(tmp_path)
    sampled = source.population(ref.entity("sales.customers")).sample(
        engine_sample(target_rows=target, seed=seed)
    )
    with pytest.raises(MaterializationError, match="registered engine capability"):
        sampled.execute()
    assert runtime.last_run_ref is None
    assert runtime.statistics.events == {"reconciliation": 1}


def test_duplicate_source_identity_cannot_be_hidden_by_sampling(tmp_path: Path) -> None:
    runtime, source, database = _setup(tmp_path)
    with duckdb.connect(str(database)) as connection:
        connection.execute("INSERT INTO customers (id, region) VALUES (4, 'EU')")
    with pytest.raises(MaterializationError, match="source validation failed"):
        source.population(ref.entity("sales.customers")).sample(
            engine_sample(target_rows=1)
        ).execute()
    assert runtime.statistics.sampling_fences == 0
    assert not runtime.statistics.events.get("sampling_reserved", 0)
    assert runtime.store.resources(runtime.session_ref) == ()


def test_fence_failure_discharges_reserved_resource_without_publication(tmp_path: Path) -> None:
    def fail(point: str) -> None:
        if point == "sampling_fence":
            raise RuntimeError("injected sampling failure")

    runtime, source, _ = _setup(tmp_path, event=fail)
    with pytest.raises(RuntimeError):
        source.population(ref.entity("sales.customers")).sample(
            engine_sample(target_rows=2)
        ).execute()
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.parametrize("point", ["sample_query", "primary_query", "state_write", "publication"])
def test_sampled_failure_never_publishes_partial_rows_or_state(
    tmp_path: Path, point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    hits: list[str] = []

    created_payloads = 0

    def event(name: str) -> None:
        nonlocal created_payloads
        if name == "parquet_payload_create":
            created_payloads += 1
            if point == "state_write" and created_payloads == 3:
                hits.append(point)
                raise OSError("sampling-failure-canary")
        if point == "publication" and name == "insert_artifact":
            hits.append(point)
            raise RuntimeError("sampling-failure-canary")

    if point == "sample_query":

        def sample_failure(backend: ExecutionAdapter, fence: CompiledSampleFence) -> str:
            hits.append(point)
            return f"CREATE TEMPORARY TABLE \"{fence.relation_name}\" AS SELECT error('sampling-failure-canary') AS invalid"

        monkeypatch.setattr(admission, "sample_statement", sample_failure)
    elif point == "primary_query":
        original_batches = DatasetRuntime._batches

        def primary_failure(
            self: DatasetRuntime, backend: ExecutionAdapter, expression: ir.Table, batch_rows: int
        ) -> Iterator[pa.RecordBatch]:
            hits.append(point)
            backend.submit(backend.statement("SELECT error('sampling-failure-canary')"))
            yield from original_batches(self, backend, expression, batch_rows)

        monkeypatch.setattr(DatasetRuntime, "_batches", primary_failure)

    runtime, source, _ = _setup(tmp_path, event=event)
    sampled = source.population(ref.entity("sales.customers")).sample(
        engine_sample(target_rows=2, seed=42)
    )
    with pytest.raises(
        OSError
        if point == "state_write"
        else (RuntimeError if point == "publication" else duckdb.InvalidInputException)
    ) as failed:
        source.observe(
            [ref.metric("sales.revenue"), ref.metric("sales.order_count")], population=sampled
        ).aggregate().execute()
    assert hits == [point]
    assert "sampling-failure-canary" in str(failed.value)
    assert runtime.last_run_ref is not None
    run = runtime.store.run(runtime.last_run_ref)
    assert run is not None and run.lifecycle == "failed" and run.output_artifact_ref is None
    counts = snapshot(runtime)["counts"]
    assert isinstance(counts, dict)
    assert counts["analysis_action_runs"] == counts["analysis_action_run_terminals"] == 1
    assert counts["dataset_artifacts"] == counts["dataset_evidence"] == counts["findings"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    assert not tuple(runtime.store.layout.session_dir(runtime.session_ref).rglob("*.parquet"))


def test_distinct_authored_samples_keep_separate_physical_fences(tmp_path: Path) -> None:
    with execution_fixture(tmp_path) as fixture:
        target = fixture.sources.population(ref.entity("sales.customers"))
        first = target.sample(engine_sample(target_rows=2, seed=42))
        second = target.sample(engine_sample(target_rows=2, seed=42))
        statements: list[str] = []
        samples = []
        for ordinal, dataset in enumerate((first, second)):
            recipe = compile_dataset(dataset, fixture.tables(dataset))
            fences = tuple(
                step for step in recipe.preparations if isinstance(step, CompiledSampleFence)
            )
            assert len(fences) == 1
            # Distinct authored handles use separate action-local names when composed.
            fence = replace(fences[0], relation_name=f"__mv_sample_{ordinal}")
            adapter = DuckDBExecutionAdapter(fixture.backend)
            adapter.observe(
                lambda receipt: (
                    statements.append(receipt.sql) if receipt.role == "sampling_fence" else None
                ),
                "source",
            )
            samples.append(
                sampling_runtime.execute_sample(
                    adapter,
                    fence,
                    statement=adapter.statement(
                        sampling_runtime.sample_statement(adapter, fence), role="sampling_fence"
                    ),
                    ordinal=ordinal,
                    event=lambda _: None,
                )
            )
        assert len(statements) == 2 and statements[0] != statements[1]
        assert samples[0].ordinal != samples[1].ordinal
        assert (
            samples[0].population_definition_fingerprint
            == samples[1].population_definition_fingerprint
        )
        assert samples[0].membership_digest == samples[1].membership_digest
