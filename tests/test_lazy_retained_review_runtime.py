"""Review acceptance for inherited sampling disclosure and substantial object inputs."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import duckdb
import pytest

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.datasets.base import MaterializedDataset
from marivo.analysis.materialization import admission
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import ObjectReceipt
from marivo.analysis.materialization.object_storage import client, open_manifest
from marivo.analysis.materialization.targets import S3Access
from marivo.analysis.observation.metric import MaterializedMetricDataset
from marivo.analysis.observation.population import MaterializedPopulationDataset
from marivo.analysis.observation.predicates import gt
from marivo.analysis.observation.sampling import engine_sample
from marivo.refs import ref
from tests.lazy_adapter_runtime_worker import snapshot
from tests.lazy_retained_fixtures import setup_retained

pytestmark = pytest.mark.runtime

REVENUE = ref.metric("sales.revenue")
MEAN = ref.metric("sales.mean_amount")
CUSTOMERS = ref.entity("sales.customers")


def _show(dataset: MaterializedDataset) -> str:
    assert isinstance(dataset, (MaterializedMetricDataset, MaterializedPopulationDataset))
    with redirect_stdout(io.StringIO()) as rendered:
        dataset.show()
    return rendered.getvalue()


def test_sampled_membership_and_metric_checkpoints_keep_approximation_disclosure(
    tmp_path: Path,
) -> None:
    fixture = setup_retained(tmp_path, "engine")
    target = fixture.sources.population(CUSTOMERS)
    sampled = target.sample(engine_sample(target_rows=3, seed=7))
    # Sampling changes the definition and realization, not the meaning of one
    # Entity row or the cardinality/ordering-only Core row-set descriptor.
    assert sampled.row_contract == target.row_contract
    assert sampled.row_set_contract == target.row_set_contract
    assert sampled.definition_fingerprint != target.definition_fingerprint
    membership = sampled.execute()
    original = fixture.runtime.store.artifact(membership.state.artifact_ref.ref)
    assert original is not None and original.descriptor.sampling_execution is not None
    realization = original.descriptor.sampling_execution
    assert realization[0].target_population_definition_fingerprint == target.definition_fingerprint
    assert realization[0].realized_entity_count == 3
    first = fixture.sources.observe(REVENUE, population=membership).execute()
    first_record = fixture.runtime.store.artifact(first.state.artifact_ref.ref)
    assert first_record is not None
    assert first_record.descriptor.sampling_execution == realization
    assert (
        first_record.descriptor.population_authority.definition_fingerprint
        == membership.definition_fingerprint
    )
    with duckdb.connect(str(fixture.database)) as backend:
        backend.execute("DROP TABLE customers")
    second = fixture.sources.observe(MEAN, population=first).execute()
    second_record = fixture.runtime.store.artifact(second.state.artifact_ref.ref)
    assert second_record is not None
    assert second_record.descriptor.sampling_execution == realization
    assert (
        second_record.descriptor.population_authority.definition_fingerprint
        == first.definition_fingerprint
    )
    assert fixture.runtime.statistics.sampling_fences == 0
    assert not any('"customers"' in sql for _, sql in fixture.runtime.statistics.statements)
    for dataset in (membership, first, second):
        rendered = _show(dataset)
        assert "Sampling: approximate Entity sample; realizations=1" in rendered
        assert "target=3, realized=3, seeded=True" in rendered
        assert "population inference" not in rendered
        record = fixture.runtime.store.artifact(dataset.state.artifact_ref.ref)
        assert record is not None
        assert any(
            part.role == "population_sampling_state" for part in record.descriptor.retained_parts
        )
    fixture.database.rename(tmp_path / "warehouse.offline")
    reopened = DatasetRuntime.open(tmp_path, fixture.runtime.session_ref)
    before = snapshot(reopened)
    recovered = reopened.artifact(second.state.artifact_ref)
    recovered_record = reopened.store.artifact(second.state.artifact_ref.ref)
    assert recovered_record is not None
    assert recovered_record.descriptor.sampling_execution == realization
    assert recovered.row_contract == second.row_contract
    assert recovered.row_set_contract == second.row_set_contract
    assert "Sampling: approximate Entity sample" in _show(recovered)
    assert snapshot(reopened) == before
    assert reopened.statistics.sampling_fences == reopened.statistics.primary_queries == 0
    assert reopened.store.resources(reopened.session_ref) == ()


def test_substantial_object_metric_checkpoint_folds_with_source_offline(
    tmp_path: Path,
    lazy_s3_access: S3Access,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = setup_retained(tmp_path, "object", access=lazy_s3_access)
    count = 20_000
    with duckdb.connect(str(fixture.database)) as backend:
        backend.execute("DELETE FROM orders")
        backend.execute(
            "INSERT INTO orders (id, amount) SELECT i, (i % 7) + 1 FROM range(1, ?) AS ids(i)",
            [count + 1],
        )
    checkpoint = fixture.sources.observe([REVENUE, MEAN]).execute()
    assert checkpoint.state.realized_row_count == count
    original = fixture.runtime.store.artifact(checkpoint.state.artifact_ref.ref)
    assert original is not None and len(original.descriptor.retained_parts) == 2
    receipts = (
        original.descriptor.storage_receipt,
        *(part.storage_receipt for part in original.descriptor.retained_parts),
    )
    assert all(
        isinstance(receipt, ObjectReceipt) and receipt.realized_row_count == count
        for receipt in receipts
    )
    with client(lazy_s3_access) as storage:
        versions = []
        for receipt in receipts:
            assert isinstance(receipt, ObjectReceipt)
            backing = open_manifest(storage, lazy_s3_access, receipt)
            assert backing.version and backing.size > 0
            versions.append((backing.key, backing.version))
    assert len(set(versions)) == 3
    fixture.database.rename(tmp_path / "warehouse.offline")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("retained object continuation attempted source work")

    for name in ("_build_backend_from_effective", "_effective_kwargs", "compile_dataset"):
        monkeypatch.setattr(admission, name, forbidden)
    selected = checkpoint.where(gt(REVENUE, 4))
    logical = selected.aggregate()
    result = logical.execute()
    selected_values = [
        (identity % 7) + 1 for identity in range(1, count + 1) if (identity % 7) + 1 > 4
    ]
    frame = result.to_pandas()
    assert frame["revenue"].tolist() == [sum(selected_values)]
    assert frame["mean_amount"].tolist() == pytest.approx(
        [sum(selected_values) / len(selected_values)]
    )
    assert fixture.runtime.statistics.primary_queries == 0
    assert fixture.runtime.statistics.events.get("profile_resolution", 0) == 0
    assert fixture.runtime.statistics.events.get("credential_resolution", 0) == 0
    assert fixture.runtime.statistics.worker_pid is not None
    assert len(fixture.runtime.statistics.local_handoffs) == 2
    assert (
        fixture.runtime.statistics.local_handoffs[0][1]
        == fixture.runtime.statistics.local_handoffs[1][0]
    )
    assert fixture.runtime.local_policy.max_input_rows == 100_000
    output = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
    assert output is not None and isinstance(output.descriptor.storage_receipt, ObjectReceipt)
    assert all(
        part.storage_receipt.realized_row_count == 1 for part in output.descriptor.retained_parts
    )
    assert fixture.runtime.store.artifact(checkpoint.state.artifact_ref.ref) == original
    assert fixture.runtime.store.resources(fixture.runtime.session_ref) == ()
    before = snapshot(fixture.runtime)
    # Stored identities cannot enter an unrelated source domain through upload.
    with pytest.raises(DatasetCompilationError, match="source-required"):
        fixture.sources.observe(MEAN, population=checkpoint).execute()
    assert snapshot(fixture.runtime) == before
    assert logical.execute().state.artifact_ref == result.state.artifact_ref
    assert snapshot(fixture.runtime) == before
    assert fixture.runtime.statistics.worker_pid is None
    assert fixture.runtime.statistics.events == {"reconciliation": 1}
    reopened = DatasetRuntime.open(
        tmp_path, fixture.runtime.session_ref, object_bindings=(lazy_s3_access,)
    )
    recovered = reopened.artifact(result.state.artifact_ref)
    assert isinstance(recovered, MaterializedMetricDataset)
    assert recovered.to_pandas().equals(frame)
    assert snapshot(reopened) == before
