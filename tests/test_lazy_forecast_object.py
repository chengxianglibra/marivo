"""Forecast object publication through the native SDK with exact queued responses."""

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

from marivo.analysis.materialization import object_storage
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import ObjectReceipt
from marivo.analysis.materialization.reads import payload_batches
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.materialization.targets import LocalTarget, ObjectTarget, S3Access
from marivo.analysis.operators.forecast_contracts import periods
from marivo.analysis.operators.forecast_dataset import MaterializedForecastDataset
from tests.lazy_forecast_fixtures import history, setup_forecast

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

pytestmark = pytest.mark.runtime


def test_forecast_object_roundtrip_and_local_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, source, database = setup_forecast(tmp_path)
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
    result = history(source).forecast(horizon=periods(4)).execute()
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
    assert table.num_rows == 4 and result.evidence_digest.finding_count == 4
    assert reads and len(stored) == 2
    database.rename(tmp_path / "origin.offline")
    reopened = DatasetRuntime.open(tmp_path, runtime.session_ref, target=LocalTarget())
    reopened.object_bindings = (access,)
    recovered = reopened.artifact(result.state.artifact_ref.ref)
    assert isinstance(recovered, MaterializedForecastDataset)
    selected_result = recovered.limit(2).execute()
    assert selected_result.to_pandas().horizon_ordinal.tolist() == [1, 2]
    assert selected_result.evidence_digest.finding_count == 2
    assert reopened.statistics.primary_queries == 1
    assert reopened.statistics.events.get("profile_resolution", 0) == 0
    assert reopened.statistics.worker_pid is None
    assert reopened.statistics.transferred_rows == 2
