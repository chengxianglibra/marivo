"""Version-pinned Candidate objects preserve terminal reads and continuation admission."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pyarrow as pa
import pytest
from botocore.stub import Stubber

from marivo.analysis.materialization import object_storage
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import ObjectReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.reads import payload_batches
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.materialization.targets import LocalTarget, ObjectTarget, S3Access
from marivo.analysis.operators.candidate_contracts import CandidateObjective
from marivo.analysis.operators.candidate_dataset import MaterializedCandidateDataset
from tests.lazy_candidate_fixtures import candidate_input, discover, setup_candidate
from tests.lazy_candidate_object_fixtures import stub_candidate_objects
from tests.lazy_materialization_crash_worker import snapshot

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize(
    "objective", ["point_anomalies", "interesting_windows", "period_shifts", "entity_outliers"]
)
@pytest.mark.parametrize("input_kind", ["logical", "object"])
def test_candidate_object_roundtrip_and_local_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    objective: CandidateObjective,
    input_kind: str,
) -> None:
    runtime, source, database = setup_candidate(tmp_path)
    access = S3Access("fixture", "http://127.0.0.1:9", "bucket", "test-key", "test-secret")
    objects = stub_candidate_objects(monkeypatch, access)
    stored, reads = objects.stored, objects.reads
    runtime.target, runtime.object_bindings = ObjectTarget("fixture"), (access,)
    input_value = candidate_input(source, objective)
    if input_kind == "object":
        retained = input_value.execute()
        database.rename(tmp_path / "origin.offline")
        result = discover(retained, objective).execute()
    else:
        result = discover(input_value, objective).execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and isinstance(record.descriptor.storage_receipt, ObjectReceipt)
    descriptor = record.descriptor
    table = pa.Table.from_batches(
        list(
            payload_batches(
                tmp_path,
                descriptor.storage_receipt,
                policy=ReadPolicy(),
                bindings=(access,),
                row=descriptor.row_contract,
                rows=descriptor.row_set_contract,
                audit=True,
            )
        )
    )
    assert table.num_rows > 0 and result.evidence_digest.finding_count == 0
    assert result.findings().items == () and descriptor.candidate_evidence is not None
    original = descriptor.candidate_evidence
    first_id = table.column("item_id")[0].as_py()
    assert reads and len(stored) >= 2
    if input_kind == "logical":
        assert len(stored) == 2
        database.rename(tmp_path / "origin.offline")
    reopened = DatasetRuntime.open(tmp_path, runtime.session_ref, target=LocalTarget())
    reopened.object_bindings = (access,)
    recovered = reopened.artifact(result.state.artifact_ref.ref)
    assert isinstance(recovered, MaterializedCandidateDataset)
    if objective == "entity_outliers":
        assert table.column("entity_identity").to_pylist() == [
            {"id": identity} for identity in range(213, 206, -1)
        ]
        assert recovered.evidence_digest.finding_count == 0
        assert recovered.findings().items == ()
        recovered_record = reopened.store.artifact(recovered.state.artifact_ref.ref)
        assert recovered_record is not None
        assert recovered_record.descriptor.candidate_evidence == original
        assert recovered_record.descriptor.storage_receipt == descriptor.storage_receipt
        recovered.show()
        rendered = capsys.readouterr().out
        assert "<identity>" in rendered and "entity_mad_threshold_met" in rendered
        assert "{'id':" not in rendered
    selected_result = recovered.limit(1).execute()
    frame = selected_result.to_pandas()
    assert frame.item_id.tolist() == [first_id]
    assert isinstance(frame.reason_codes.iloc[0], tuple)
    assert (
        selected_result.evidence_digest.finding_count == 0
        and selected_result.findings().items == ()
    )
    selected_record = reopened.store.artifact(selected_result.state.artifact_ref.ref)
    assert selected_record is not None and selected_record.descriptor.candidate_evidence is not None
    selected_evidence = selected_record.descriptor.candidate_evidence
    assert selected_evidence.definition == original.definition
    assert selected_evidence.evaluation == original.evaluation and selected_evidence.row_count == 1
    assert reopened.statistics.primary_queries == 1
    assert reopened.statistics.events.get("local_execution_started", 0) == 0


@pytest.mark.parametrize("objective", ["point_anomalies", "entity_outliers"])
def test_candidate_object_denial_precedes_evaluation_and_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, objective: CandidateObjective
) -> None:
    runtime, source, _ = setup_candidate(tmp_path)
    access = S3Access("fixture", "http://127.0.0.1:9", "bucket", "private-key", "private-secret")
    runtime.target, runtime.object_bindings = ObjectTarget("fixture"), (access,)
    base = object_storage.client

    @contextmanager
    def denied(binding: S3Access) -> Iterator[S3Client]:
        with base(binding) as client, Stubber(client) as stub:
            stub.add_client_error(
                "get_bucket_versioning",
                service_error_code="AccessDenied",
                service_message="private-candidate-canary",
                http_status_code=403,
                expected_params={"Bucket": "bucket"},
            )
            yield client
            stub.assert_no_pending_responses()

    monkeypatch.setattr(object_storage, "client", denied)
    before = snapshot(runtime)
    with pytest.raises(MaterializationError) as error:
        discover(candidate_input(source, objective), objective).execute()
    assert error.value.stage == "storage_selection"
    assert "private-candidate-canary" not in str(error.value)
    old, new = before["tables"], snapshot(runtime)["tables"]
    assert isinstance(old, dict) and isinstance(new, dict)
    for table in ("dataset_artifacts", "dataset_evidence", "findings", "action_resource_journal"):
        assert old[table] == new[table]
    assert (
        runtime.statistics.events.get("local_execution_started", 0) == 0
        and runtime.statistics.primary_queries == 0
    )
