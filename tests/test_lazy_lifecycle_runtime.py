"""Real native replay and atomic canonical retention."""

from pathlib import Path

import pytest

from marivo.analysis.domains.lifecycle import ROLES, MaterializedLifecycleDataset
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.lifecycle_codec import LifecycleEvidenceSummary
from tests.lazy_lifecycle_fixtures import history, setup_lifecycle

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("engine", [False, True])
def test_native_history_and_cold_recovery(tmp_path: Path, engine: bool) -> None:
    runtime, sources, database = setup_lifecycle(tmp_path, engine=engine)
    logical = history(sources)
    result = logical.execute()
    assert isinstance(result, MaterializedLifecycleDataset)
    rows = result.to_pandas()
    assert rows.model_state.tolist() == ["open", "done", "open"]
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    assert tuple(p.role for p in record.descriptor.retained_parts) == ROLES
    assert isinstance(record.descriptor.lifecycle_evidence, LifecycleEvidenceSummary)
    assert record.descriptor.lifecycle_evidence.transition_count == 1
    assert record.descriptor.lifecycle_evidence.violation_count == 1
    database.unlink()
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref)
    recovered = cold.artifact(result.state.artifact_ref)
    assert isinstance(recovered, MaterializedLifecycleDataset)
    assert recovered.to_pandas().equals(rows)
    assert logical.execute().state.artifact_ref == result.state.artifact_ref


@pytest.mark.parametrize("engine", [False, True])
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("cancel", [False, True])
def test_each_required_part_fails_atomically_and_retries(
    tmp_path: Path, engine: bool, role: str, cancel: bool
) -> None:
    from marivo.analysis.materialization.errors import MaterializationError
    from tests.lazy_adapter_runtime_worker import snapshot
    from tests.lazy_event_runtime_worker import assert_identity_private

    armed = True

    def fail(point: str) -> None:
        if armed and point == f"lifecycle_part_write.{role}":
            if cancel:
                raise KeyboardInterrupt("lifecycle-private-canary")
            raise OSError("lifecycle-private-canary")

    runtime, sources, _ = setup_lifecycle(tmp_path, engine=engine, event=fail)
    logical = history(sources)
    with pytest.raises(MaterializationError) as caught:
        logical.execute()
    assert "canary" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    counts = snapshot(runtime)
    assert counts["dataset_artifacts"] == counts["dataset_evidence"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    assert_identity_private(runtime)
    armed = False
    result = logical.execute()
    assert isinstance(result, MaterializedLifecycleDataset)
    assert snapshot(runtime)["dataset_artifacts"] == 1


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("mutate", [False, True])
def test_revalidation_rejects_missing_and_corrupt_required_parts(
    tmp_path: Path, role: str, mutate: bool
) -> None:
    from marivo.analysis.materialization.contracts import LocalReceipt
    from tests.lazy_adapter_runtime_worker import snapshot

    runtime, sources, database = setup_lifecycle(tmp_path)
    result = history(sources).execute()
    clean = runtime.revalidate(result.state.artifact_ref)
    assert clean.storage_authority == "readable"
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    receipt = next(p.storage_receipt for p in record.descriptor.retained_parts if p.role == role)
    assert isinstance(receipt, LocalReceipt)
    path = tmp_path / receipt.project_relative_path / "data.parquet"
    if mutate:
        path.write_bytes(b"corrupt-lifecycle-part")
    else:
        path.unlink()
    database.unlink()
    before = snapshot(runtime)
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref)
    report = cold.revalidate(result.state.artifact_ref)
    assert report.storage_authority == ("mutated" if mutate else "missing")
    assert snapshot(cold) == before


def test_large_history_uses_native_identity_execution(tmp_path: Path) -> None:
    from unittest.mock import patch

    import duckdb

    from marivo.analysis.materialization import admission
    from tests.lazy_adapter_runtime_worker import forbidden
    from tests.lazy_event_runtime_worker import assert_identity_private

    runtime, sources, database = setup_lifecycle(tmp_path, engine=True)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM started_rows")
        connection.execute("DELETE FROM finished_rows")
        connection.execute("INSERT INTO customers(id) SELECT 881730041+i FROM range(5000) t(i)")
        connection.execute(
            "INSERT INTO started_rows SELECT 981730041+i,881730041+i,TIMESTAMP '2026-02-01 00:00:00' FROM range(5000) t(i)"
        )
    with (
        patch.object(admission, "supervise", forbidden),
        patch.object(DatasetRuntime, "_batches", forbidden),
        patch("marivo.analysis.materialization.reads.payload_batches", forbidden),
    ):
        result = history(sources).execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and isinstance(
        record.descriptor.lifecycle_evidence, LifecycleEvidenceSummary
    )
    assert record.descriptor.lifecycle_evidence.row_count == 5000
    assert record.descriptor.lifecycle_evidence.subject_count >= 5000
    assert runtime.statistics.transferred_rows == runtime.statistics.transferred_bytes == 0
    assert runtime.statistics.local_handoffs == ()
    assert_identity_private(runtime, ("881730041", "981730041"))


@pytest.mark.parametrize("complete", [False, True])
def test_empty_history_retains_every_admitted_subject(tmp_path: Path, complete: bool) -> None:
    import duckdb
    import pyarrow as pa

    from marivo.analysis.materialization.reads import payload_batches
    from marivo.analysis.materialization.storage import ReadPolicy

    runtime, sources, database = setup_lifecycle(tmp_path)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM started_rows")
        connection.execute("DELETE FROM finished_rows")
    result = history(sources, complete=complete).execute()
    assert result.to_pandas().empty
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and isinstance(
        record.descriptor.lifecycle_evidence, LifecycleEvidenceSummary
    )
    ledger = next(p for p in record.descriptor.retained_parts if p.role == ROLES[1])
    rows = pa.Table.from_batches(
        list(payload_batches(tmp_path, ledger.storage_receipt, policy=ReadPolicy(), audit=True))
    ).to_pylist()
    assert len(rows) == record.descriptor.lifecycle_evidence.subject_count > 0
    assert all(
        r["classification"] == ("not_incepted" if complete else "coverage_censored") for r in rows
    )
    assert runtime.revalidate(result.state.artifact_ref).storage_authority == "readable"


@pytest.mark.parametrize("mode", ["logical_metric", "retained_metric", "sampled"])
def test_history_uses_exact_admitted_membership(tmp_path: Path, mode: str) -> None:
    import duckdb
    import pyarrow as pa

    from marivo.analysis.domains.subject import PopulationInput
    from marivo.analysis.materialization.reads import payload_batches
    from marivo.analysis.materialization.storage import ReadPolicy
    from marivo.analysis.observation.predicates import gt
    from marivo.analysis.observation.sampling import engine_sample
    from marivo.refs import ref

    runtime, sources, database = setup_lifecycle(tmp_path)
    population: PopulationInput
    if mode == "sampled":
        population = sources.population(ref.entity("sales.customers")).sample(
            engine_sample(target_rows=2, seed=3)
        )
    else:
        metric = sources.observe(
            ref.metric("sales.revenue"),
            population=sources.population(ref.entity("sales.customers")),
        )
        selected = metric.where(gt(metric.fields.metric(ref.metric("sales.revenue")), 50))
        if mode == "retained_metric":
            from marivo.analysis.materialization.targets import EngineTarget, LocalTarget

            runtime.target = EngineTarget("warehouse")
            population = selected.execute()
            runtime.target = LocalTarget()
        else:
            population = selected
    if mode == "retained_metric":
        with duckdb.connect(str(database), config={"threads": 1}) as connection:
            connection.execute("DROP TABLE orders")
    result = history(sources, population=population).execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and isinstance(
        record.descriptor.lifecycle_evidence, LifecycleEvidenceSummary
    )
    ledger = next(
        p for p in record.descriptor.retained_parts if p.role == "lifecycle_subject_coverage"
    )
    rows = pa.Table.from_batches(
        list(payload_batches(tmp_path, ledger.storage_receipt, policy=ReadPolicy(), audit=True))
    ).to_pylist()
    if mode == "sampled":
        assert len(rows) == 2
        assert record.descriptor.sampling_execution is not None
        assert any(p.role == "population_sampling_state" for p in record.descriptor.retained_parts)
    else:
        assert [r["entity_identity"] for r in rows] == [{"id": 2}]
    assert runtime.revalidate(result.state.artifact_ref).storage_authority == "readable"


@pytest.mark.parametrize("identities", [(1, 2), (2, 1)])
def test_cross_event_tie_rejects_atomically_and_retries(
    tmp_path: Path, identities: tuple[int, int]
) -> None:
    import duckdb

    from marivo.analysis.materialization.errors import MaterializationError
    from tests.lazy_adapter_runtime_worker import snapshot

    runtime, sources, database = setup_lifecycle(tmp_path)
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("DELETE FROM started_rows")
        connection.execute("DELETE FROM finished_rows")
        connection.execute(
            "INSERT INTO started_rows VALUES (?,1,'2026-02-01 00:00:00')", [identities[0]]
        )
        connection.execute(
            "INSERT INTO finished_rows VALUES (?,1,'2026-02-01 00:00:00')", [identities[1]]
        )
    logical = history(sources)
    with pytest.raises(MaterializationError):
        logical.execute()
    counts = snapshot(runtime)
    assert counts["dataset_artifacts"] == counts["dataset_evidence"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    with duckdb.connect(str(database), config={"threads": 1}) as connection:
        connection.execute("UPDATE finished_rows SET occurred_at='2026-02-01 01:00:00'")
    rows = logical.execute().to_pandas()
    assert rows.model_state.tolist() == ["open", "done"]


@pytest.mark.parametrize("field", ["initial", "seed_fingerprint"])
def test_corrupt_lifecycle_authority_returns_integrity_repair(tmp_path: Path, field: str) -> None:
    import json

    from marivo.analysis.materialization.contracts import canonical_json, encode_descriptor
    from marivo.analysis.materialization.errors import IntegrityError
    from tests.lazy_adapter_runtime_worker import snapshot

    runtime, sources, database = setup_lifecycle(tmp_path)
    result = history(sources).execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    payload = json.loads(encode_descriptor(record.descriptor))
    payload["row_contract"]["family_semantics"][field] = "invalid-retained-authority"
    with runtime.store._write() as connection:
        connection.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (canonical_json(payload), result.state.artifact_ref.ref),
        )
    database.unlink()
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref)
    before = snapshot(cold)
    report = cold.revalidate(result.state.artifact_ref)
    assert report.artifact_integrity == "invalid"
    assert any(issue.kind == "metadata_invalid" for issue in report.issues)
    with pytest.raises(IntegrityError):
        cold.artifact(result.state.artifact_ref)
    assert snapshot(cold) == before
