"""Exact-version Lifecycle bundle storage and per-role remote rollback."""

from pathlib import Path

import pyarrow as pa
import pytest

from marivo.analysis.domains.lifecycle import ROLES
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import ObjectReceipt
from marivo.analysis.materialization.lifecycle_publication import inspect_history
from marivo.analysis.materialization.reads import payload_batches
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.materialization.targets import ObjectTarget, S3Access
from tests.lazy_adapter_runtime_worker import snapshot
from tests.lazy_candidate_object_fixtures import stub_candidate_objects
from tests.lazy_lifecycle_fixtures import history, setup_lifecycle

pytestmark = pytest.mark.runtime


def test_object_bundle_pins_every_role_and_recovers_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, sources, database = setup_lifecycle(tmp_path)
    access = S3Access("fixture", "http://127.0.0.1:9", "bucket", "test-key", "test-secret")
    objects = stub_candidate_objects(monkeypatch, access)
    runtime.target, runtime.object_bindings = ObjectTarget("fixture"), (access,)
    result = history(sources).execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and isinstance(record.descriptor.storage_receipt, ObjectReceipt)
    assert tuple(p.role for p in record.descriptor.retained_parts) == ROLES
    assert all(
        isinstance(p.storage_receipt, ObjectReceipt) for p in record.descriptor.retained_parts
    )
    assert len(objects.stored) == 8
    database.unlink()
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref, object_bindings=(access,))
    recovered = cold.artifact(result.state.artifact_ref)
    assert recovered.state.artifact_ref == result.state.artifact_ref
    inspect_history(tmp_path, record.descriptor, (access,), ReadPolicy())
    for part in record.descriptor.retained_parts:
        rows = pa.Table.from_batches(
            list(
                payload_batches(
                    tmp_path,
                    part.storage_receipt,
                    policy=ReadPolicy(),
                    bindings=(access,),
                    audit=True,
                )
            )
        )
        assert rows.num_rows == part.storage_receipt.realized_row_count
    assert objects.reads and all(
        objects.stored[key][0] == version for key, version in objects.reads
    )


@pytest.mark.parametrize("failed_put", [3, 5, 7])
@pytest.mark.parametrize("cancel", [False, True])
def test_remote_required_part_failure_leaves_no_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_put: int, cancel: bool
) -> None:
    calls = 0

    def fail(point: str) -> None:
        nonlocal calls
        if point == "object_before_put":
            calls += 1
            if calls == failed_put:
                if cancel:
                    raise KeyboardInterrupt("private-object-cancel")
                raise OSError("private-object-failure")

    runtime, sources, _ = setup_lifecycle(tmp_path, event=fail)
    access = S3Access("fixture", "http://127.0.0.1:9", "bucket", "test-key", "test-secret")
    objects = stub_candidate_objects(monkeypatch, access)
    runtime.target, runtime.object_bindings = ObjectTarget("fixture"), (access,)
    with pytest.raises(KeyboardInterrupt if cancel else OSError):
        history(sources).execute()
    assert snapshot(runtime)["dataset_artifacts"] == 0
    assert runtime.store.resources(runtime.session_ref) == ()
    assert objects.stored == {}
