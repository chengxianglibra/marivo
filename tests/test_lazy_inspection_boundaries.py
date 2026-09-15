"""Independent regression probes for full inspection authority and failure boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from marivo.analysis.materialization import inspection
from marivo.analysis.materialization.contracts import (
    LocalReceipt,
    encode_descriptor,
)
from marivo.analysis.materialization.errors import IntegrityError, StorageAccessError
from marivo.analysis.materialization.reads import payload_batches
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.refs import ref
from tests.lazy_adapter_runtime_worker import forbidden
from tests.lazy_materialization_crash_worker import snapshot
from tests.lazy_retained_fixtures import setup_retained


def test_foreign_descriptor_cannot_authorize_any_external_storage_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = setup_retained(tmp_path)
    runtime = fixture.runtime
    selected = fixture.sources.population(ref.entity("sales.customers")).execute()
    foreign = fixture.sources.population(ref.entity("sales.orders")).execute()
    foreign_record = runtime.store.artifact(foreign.state.artifact_ref.ref)
    assert foreign_record is not None
    with runtime.store._write() as conn:
        conn.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (encode_descriptor(foreign_record.descriptor), selected.state.artifact_ref.ref),
        )
    before = snapshot(runtime)
    with pytest.raises(IntegrityError):
        runtime.artifact(selected.state.artifact_ref)
    monkeypatch.setattr(inspection, "_storage_checks", forbidden)
    checked = runtime.revalidate(selected.state.artifact_ref)
    assert checked.artifact_integrity == "invalid"
    assert checked.storage_authority == "unknown"
    assert {issue.kind for issue in checked.issues} >= {
        "metadata_invalid",
        "storage_unverifiable",
    }
    assert snapshot(runtime) == before


def test_invalid_producer_timestamp_does_not_invalidate_storage_or_evidence(
    tmp_path: Path,
) -> None:
    fixture = setup_retained(tmp_path)
    runtime = fixture.runtime
    result = fixture.sources.population(ref.entity("sales.customers")).execute()
    assert runtime.last_run_ref is not None
    with runtime.store._write() as conn:
        conn.execute(
            "UPDATE analysis_action_runs SET admitted_at=? WHERE run_ref=?",
            ("not-a-timestamp", runtime.last_run_ref),
        )
    before = snapshot(runtime)
    with pytest.raises(IntegrityError):
        runtime.get_run(runtime.last_run_ref)
    checked = runtime.revalidate(result.state.artifact_ref)
    assert (
        checked.artifact_integrity,
        checked.storage_authority,
        checked.evidence_integrity,
    ) == ("invalid", "readable", "valid")
    assert {issue.kind for issue in checked.issues} == {"metadata_invalid"}
    assert snapshot(runtime) == before


@pytest.mark.parametrize("kind", ["local", "engine"])
def test_missing_backing_discards_native_exception_and_locator(
    tmp_path: Path, kind: Literal["local", "engine"]
) -> None:
    fixture = setup_retained(tmp_path, kind)
    result = fixture.sources.population(ref.entity("sales.customers")).execute()
    record = fixture.runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None
    receipt = record.descriptor.storage_receipt
    if isinstance(receipt, LocalReceipt):
        path = tmp_path / receipt.project_relative_path / "data.parquet"
    else:
        assert isinstance(receipt, LocalReceipt)
        path = tmp_path / receipt.qualified_relation_ref
    path.unlink()
    before = snapshot(fixture.runtime)
    with pytest.raises(StorageAccessError) as caught:
        list(payload_batches(tmp_path, receipt, policy=ReadPolicy()))
    error = caught.value
    assert error.storage_status == "missing"
    assert error.__context__ is None and error.__cause__ is None
    assert str(path) not in str(error) and str(path) not in repr(error)
    assert snapshot(fixture.runtime) == before
