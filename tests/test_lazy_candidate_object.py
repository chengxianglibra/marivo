"""Version-pinned Candidate objects preserve terminal reads and continuation admission."""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pyarrow as pa
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from marivo.analysis.compiler.errors import DatasetCompilationError
from marivo.analysis.materialization import object_storage
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import ObjectReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.reads import payload_batches
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.materialization.targets import LocalTarget, ObjectTarget, S3Access
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators.candidate_contracts import CandidateObjective
from marivo.analysis.operators.candidate_dataset import MaterializedCandidateDataset
from tests.lazy_candidate_fixtures import candidate_input, discover, setup_candidate
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
    base = object_storage.client
    stored: dict[str, tuple[str, bytes, dict[str, object]]] = {}
    reads: list[tuple[str, str]] = []

    @contextmanager
    def selected(binding: S3Access) -> Iterator[S3Client]:
        assert binding == access
        with base(binding) as client, Stubber(client) as stub:
            transport_method = "_make_api_call"
            original: Callable[[str, dict[str, object]], object] = getattr(client, transport_method)

            def request(operation: str, params: dict[str, object]) -> object:
                key = str(params.get("Key", ""))
                response: dict[str, object]
                if operation == "GetBucketVersioning":
                    response = {"Status": "Enabled"}
                elif operation == "PutObject":
                    body = params["Body"]
                    data = (
                        body
                        if isinstance(body, bytes)
                        else body.read()
                        if isinstance(body, io.BufferedIOBase)
                        else None
                    )
                    assert (
                        isinstance(data, bytes)
                        and len(data) == params["ContentLength"]
                        and key not in stored
                    )
                    metadata = params["Metadata"]
                    assert isinstance(metadata, dict)
                    version = f"version-{len(stored) + 1}"
                    stored[key] = (version, data, metadata)
                    response = {"VersionId": version}
                elif operation in ("HeadObject", "GetObject"):
                    version, data, metadata = stored[key]
                    assert params["VersionId"] == version
                    reads.append((key, version))
                    if operation == "HeadObject":
                        response = {
                            "VersionId": version,
                            "ContentLength": len(data),
                            "Metadata": metadata,
                        }
                    else:
                        start, end = (
                            int(v) for v in str(params["Range"]).removeprefix("bytes=").split("-")
                        )
                        chunk = data[start : end + 1]
                        response = {
                            "VersionId": version,
                            "Body": StreamingBody(io.BytesIO(chunk), len(chunk)),
                        }
                elif operation == "ListObjectVersions":
                    prefix = str(params["Prefix"])
                    response = {
                        "IsTruncated": False,
                        "Versions": [
                            {"Key": key, "VersionId": v[0]}
                            for key, v in stored.items()
                            if key.startswith(prefix)
                        ],
                    }
                elif operation == "DeleteObject":
                    assert params["VersionId"] == stored[key][0]
                    del stored[key]
                    response = {}
                else:
                    raise AssertionError(operation)
                names = {
                    "GetBucketVersioning": "get_bucket_versioning",
                    "PutObject": "put_object",
                    "HeadObject": "head_object",
                    "GetObject": "get_object",
                    "ListObjectVersions": "list_object_versions",
                    "DeleteObject": "delete_object",
                }
                stub.add_response(names[operation], response, params)
                return original(operation, params)

            monkeypatch.setattr(client, "_make_api_call", request)
            yield client
            stub.assert_no_pending_responses()

    monkeypatch.setattr(object_storage, "client", selected)
    runtime.target, runtime.object_bindings = ObjectTarget("fixture"), (access,)
    input_value = candidate_input(source, objective)
    if input_kind == "object":
        retained = input_value.execute()
        database.rename(tmp_path / "origin.offline")
        if objective == "entity_outliers":
            before = snapshot(runtime)
            old_reads = tuple(reads)
            with pytest.raises(DatasetCompilationError, match="source-required"):
                discover(retained, objective).execute()
            assert snapshot(runtime) == before
            assert tuple(reads) == old_reads
            assert runtime.store.resources(runtime.session_ref) == ()
            return
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
        old_reads = tuple(reads)
        before = snapshot(reopened)
        for continuation in (
            recovered.where(gt(recovered.fields.get("score"), 1)),
            recovered.rank(recovered.fields.get("score")),
            recovered.limit(1),
        ):
            with pytest.raises(DatasetCompilationError, match="source-required"):
                continuation.execute()
            assert snapshot(reopened) == before
        assert tuple(reads) == old_reads
        assert reopened.statistics.primary_queries == 0
        assert reopened.statistics.worker_pid is None
        assert reopened.store.resources(reopened.session_ref) == ()
        return
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
    assert reopened.statistics.primary_queries == 0


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
    assert runtime.statistics.worker_pid is None and runtime.statistics.primary_queries == 0
