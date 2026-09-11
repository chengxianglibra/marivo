"""Exact retained-role reads, complete empty membership and atomic failures."""

from pathlib import Path

import pytest

from marivo.analysis.domains.lifecycle import ROLES, LogicalLifecycleDataset
from marivo.analysis.domains.lifecycle_reducers import in_state
from marivo.analysis.materialization.contracts import EngineReceipt
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.observation.population import LogicalPopulationDataset
from marivo.semantic.state_model import ModelStateHandle
from tests.lazy_adapter_runtime_worker import snapshot
from tests.lazy_event_runtime_worker import assert_identity_private
from tests.lazy_lifecycle_fixtures import END, MODEL, START, history, setup_lifecycle

pytestmark = pytest.mark.runtime


@pytest.mark.parametrize(
    "method,required",
    [
        ("distribution", ROLES[1]),
        ("transitions", ROLES[0]),
        ("dwell", None),
        ("violations", ROLES[2]),
        ("selection", ROLES[1]),
    ],
)
@pytest.mark.parametrize("role", ROLES)
def test_only_consumed_parts_are_opened(
    tmp_path: Path, method: str, required: str | None, role: str
) -> None:
    runtime, sources, database = setup_lifecycle(tmp_path, engine=True)
    h = history(sources).execute()
    record = runtime.store.artifact(h.state.artifact_ref.ref)
    assert record is not None
    receipt = next(p.storage_receipt for p in record.descriptor.retained_parts if p.role == role)
    assert isinstance(receipt, EngineReceipt)
    (tmp_path / receipt.qualified_relation_ref).unlink()
    database.unlink()
    logical: LogicalLifecycleDataset | LogicalPopulationDataset
    if method == "selection":
        logical = h.select_subjects(in_state(ModelStateHandle(MODEL, "done"), at=END))
    elif method == "distribution":
        logical = h.distribution(at=(END,))
    elif method == "transitions":
        logical = h.transitions()
    elif method == "dwell":
        logical = h.dwell()
    else:
        logical = h.violations()
    before = snapshot(runtime)
    if role == required:
        with pytest.raises(MaterializationError):
            logical.execute()
        assert snapshot(runtime)["dataset_artifacts"] == before["dataset_artifacts"]
    else:
        logical.execute()
        assert snapshot(runtime)["dataset_artifacts"] == before["dataset_artifacts"] + 1
    assert runtime.store.resources(runtime.session_ref) == ()


@pytest.mark.parametrize("role", ROLES)
def test_corrupt_required_part_is_not_reconstructed(tmp_path: Path, role: str) -> None:
    runtime, sources, database = setup_lifecycle(tmp_path, engine=True)
    h = history(sources).execute()
    record = runtime.store.artifact(h.state.artifact_ref.ref)
    assert record is not None
    receipt = next(p.storage_receipt for p in record.descriptor.retained_parts if p.role == role)
    assert isinstance(receipt, EngineReceipt)
    (tmp_path / receipt.qualified_relation_ref).chmod(0o600)
    (tmp_path / receipt.qualified_relation_ref).write_bytes(b"corrupt-private-history-part")
    database.unlink()
    logical = (
        h.transitions()
        if role == ROLES[0]
        else h.distribution(at=(END,))
        if role == ROLES[1]
        else h.violations()
    )
    before = snapshot(runtime)
    with pytest.raises(MaterializationError) as caught:
        logical.execute()
    assert "corrupt-private" not in str(caught.value)
    assert snapshot(runtime)["dataset_artifacts"] == before["dataset_artifacts"]


def test_complete_empty_selection_and_downstream_sample(tmp_path: Path) -> None:
    from marivo.analysis.observation.sampling import engine_sample

    runtime, sources, _ = setup_lifecycle(tmp_path, engine=True)
    h = history(sources).execute()
    selected = h.select_subjects(in_state(ModelStateHandle(MODEL, "done"), at=START)).execute()
    assert selected.to_pandas().empty
    sampled = selected.sample(engine_sample(target_rows=1, seed=42)).execute()
    assert sampled.to_pandas().empty
    record = runtime.store.artifact(sampled.state.artifact_ref.ref)
    assert record is not None and record.evidence.finding_count == 0


@pytest.mark.parametrize("method", ["distribution", "dwell", "selection"])
@pytest.mark.parametrize("cancel", [False, True])
def test_failure_and_cancellation_publish_no_partial_result(
    tmp_path: Path, method: str, cancel: bool
) -> None:
    armed = False

    def fail(point: str) -> None:
        if armed and point == ("quality" if cancel else "before_commit"):
            if cancel:
                raise KeyboardInterrupt("lifecycle-selection-private-canary")
            raise OSError("lifecycle-selection-private-canary")

    runtime, sources, database = setup_lifecycle(tmp_path, engine=True, event=fail)
    h = history(sources).execute()
    database.unlink()
    logical = (
        h.distribution(at=(END,))
        if method == "distribution"
        else h.dwell()
        if method == "dwell"
        else h.select_subjects(in_state(ModelStateHandle(MODEL, "done"), at=END))
    )
    before = snapshot(runtime)
    armed = True
    with pytest.raises(MaterializationError) as caught:
        logical.execute()
    assert "private-canary" not in str(caught.value)
    assert snapshot(runtime)["dataset_artifacts"] == before["dataset_artifacts"]
    assert snapshot(runtime)["dataset_evidence"] == before["dataset_evidence"]
    assert runtime.store.resources(runtime.session_ref) == ()
    assert_identity_private(runtime)
    armed = False
    logical.execute()


@pytest.mark.parametrize("kind", ["distribution", "dwell", "selection"])
def test_cold_corrupt_continuation_evidence_fails_without_execution(
    tmp_path: Path, kind: str
) -> None:
    import sqlite3

    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.contracts import canonical_json, parse_json
    from marivo.analysis.materialization.errors import IntegrityError

    runtime, sources, database = setup_lifecycle(tmp_path, engine=True)
    h = history(sources)
    output = (
        h.distribution(at=(END,)).execute()
        if kind == "distribution"
        else h.dwell().execute()
        if kind == "dwell"
        else h.select_subjects(in_state(ModelStateHandle(MODEL, "done"), at=END)).execute()
    )
    artifact = output.state.artifact_ref.ref
    with sqlite3.connect(runtime.store.db_path) as connection:
        row = connection.execute(
            "SELECT descriptor_payload FROM dataset_artifacts WHERE artifact_ref=?", (artifact,)
        ).fetchone()
        assert row is not None
        payload = parse_json(row[0])
        assert isinstance(payload, dict)
        summary = payload["lifecycle_evidence"]
        assert isinstance(summary, dict)
        summary["row_count"] = 999
        connection.execute(
            "UPDATE dataset_artifacts SET descriptor_payload=? WHERE artifact_ref=?",
            (canonical_json(payload), artifact),
        )
    database.unlink()
    before = snapshot(runtime)
    cold = DatasetRuntime.open(tmp_path, runtime.session_ref)
    with pytest.raises(IntegrityError):
        cold.artifact(output.state.artifact_ref)
    assert snapshot(cold) == before
    assert cold.statistics.statements == []
