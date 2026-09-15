"""Version-pinned Driver Candidates preserve scopes and native identity barriers."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import ObjectReceipt
from marivo.analysis.materialization.reads import payload_batches
from marivo.analysis.materialization.storage import ReadPolicy
from marivo.analysis.materialization.targets import LocalTarget, ObjectTarget, S3Access
from marivo.analysis.observation.predicates import gt
from marivo.analysis.operators.candidate_dataset import MaterializedCandidateDataset
from marivo.analysis.operators.driver_contracts import DriverCandidateEvaluationSummary
from tests.lazy_adapter_runtime_worker import snapshot
from tests.lazy_candidate_object_fixtures import stub_candidate_objects
from tests.lazy_driver_runtime_fixtures import CHANNEL, driver_metric, setup_driver

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize("retained_input", [False, True])
def test_driver_object_roundtrip_preserves_original_screening_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retained_input: bool
) -> None:
    fixture = setup_driver(tmp_path)
    runtime, sources = fixture.runtime, fixture.sources
    access = S3Access("fixture", "http://127.0.0.1:9", "bucket", "test-key", "test-secret")
    objects = stub_candidate_objects(monkeypatch, access)
    runtime.target, runtime.object_bindings = ObjectTarget("fixture"), (access,)
    current = driver_metric(sources, temporal=True, region=True)
    baseline = driver_metric(sources, baseline=True, temporal=True, region=True)
    delta = current.compare(baseline)
    if retained_input:
        checkpoint = delta.execute()
        fixture.database.rename(tmp_path / "origin.offline")
        result = checkpoint.discover.driver_axes(search_space=[CHANNEL]).execute()
    else:
        result = delta.discover.driver_axes(search_space=[CHANNEL]).execute()
        fixture.database.rename(tmp_path / "origin.offline")
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
    assert table.num_rows == 4
    assert table.column("axis_ref").to_pylist() == [CHANNEL.path] * 4
    assert set(table.column("comparison_ordinal").to_pylist()) == {0, 1}
    assert descriptor.candidate_evidence is not None
    original = descriptor.candidate_evidence
    assert isinstance(original.evaluation, DriverCandidateEvaluationSummary)
    assert original.evaluation.scope_count == original.evaluation.evaluated_axis_count == 4
    assert original.emitted_finding_count == 0 and result.findings().items == ()
    assert objects.reads and len(objects.stored) >= 2

    reopened = DatasetRuntime.open(tmp_path, runtime.session_ref, target=LocalTarget())
    reopened.object_bindings = (access,)
    recovered = reopened.artifact(result.state.artifact_ref.ref)
    assert isinstance(recovered, MaterializedCandidateDataset)
    filtered = recovered.where(gt(recovered.fields.get("score"), 0))
    selected = filtered.rank(filtered.fields.get("score")).limit(1).execute()
    frame = selected.to_pandas()
    assert len(frame) == 1 and frame.item_id.iloc[0] in table.column("item_id").to_pylist()
    assert frame.reason_codes.iloc[0] == ("axis_concentration",)
    assert selected.findings().items == () and selected.evidence_digest.finding_count == 0
    selected_record = reopened.store.artifact(selected.state.artifact_ref.ref)
    assert selected_record is not None and selected_record.descriptor.candidate_evidence is not None
    evidence = selected_record.descriptor.candidate_evidence
    assert evidence.definition == original.definition
    assert evidence.evaluation == original.evaluation and evidence.row_count == 1
    assert reopened.statistics.primary_queries == 1


def test_entity_driver_object_continues_through_registered_native_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = setup_driver(tmp_path)
    runtime, sources = fixture.runtime, fixture.sources
    access = S3Access("fixture", "http://127.0.0.1:9", "bucket", "test-key", "test-secret")
    objects = stub_candidate_objects(monkeypatch, access)
    runtime.target, runtime.object_bindings = ObjectTarget("fixture"), (access,)
    current = driver_metric(sources, axes=False, entity=True)
    baseline = driver_metric(sources, baseline=True, axes=False, entity=True)
    result = current.compare(baseline).discover.driver_axes(search_space=[CHANNEL]).execute()
    record = runtime.store.artifact(result.state.artifact_ref.ref)
    assert record is not None and isinstance(record.descriptor.storage_receipt, ObjectReceipt)
    original = record.descriptor.candidate_evidence
    assert original is not None and original.emitted_finding_count == 0
    fixture.database.rename(tmp_path / "origin.offline")
    reopened = DatasetRuntime.open(tmp_path, runtime.session_ref, target=LocalTarget())
    reopened.object_bindings = (access,)
    recovered = reopened.artifact(result.state.artifact_ref.ref)
    assert isinstance(recovered, MaterializedCandidateDataset)
    recovered.show()
    rendered = capsys.readouterr().out
    assert "<identity>" in rendered and "axis_concentration" in rendered
    assert "{'id':" not in rendered
    assert recovered.findings().items == () and recovered.evidence_digest.finding_count == 0
    before = snapshot(reopened)
    for successor in (
        recovered.where(gt(recovered.fields.get("score"), 0)),
        recovered.rank(recovered.fields.get("score")),
        recovered.limit(1),
    ):
        result = successor.execute()
        assert 0 < len(result.to_pandas()) <= len(recovered.to_pandas())
        record = reopened.store.artifact(result.state.artifact_ref.ref)
        assert record is not None and record.descriptor.candidate_evidence is not None
        assert record.descriptor.candidate_evidence.definition == original.definition
        assert reopened.statistics.events.get("local_execution_started", 0) == 0
    assert snapshot(reopened)["dataset_artifacts"] == before["dataset_artifacts"] + 3
    assert objects.reads
    assert reopened.store.resources(reopened.session_ref) == ()
