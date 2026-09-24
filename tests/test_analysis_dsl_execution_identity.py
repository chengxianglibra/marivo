"""Inactive DSL execution identity against exact Store v6 publications."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from marivo.analysis.datasets.base import LogicalDataset, _make_logical_dataset
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization.contracts import (
    ArtifactDescriptor,
    ArtifactRecord,
    RunDatasetInput,
    RunFailure,
)
from marivo.analysis.materialization.errors import IntegrityError
from marivo.analysis.materialization.execution_key import (
    execution_key,
    fixed_execution_key,
    source_execution_key,
)
from marivo.analysis.materialization.reconciliation import reconcile_session
from marivo.analysis.materialization.recovery import recover_dataset
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.refs import ref
from tests.lazy_dataset_fixtures import make_owner, make_row_contracts, make_test_registry
from tests.lazy_materialization_fixtures import descriptor
from tests.lazy_observation_fixtures import NoIoActionPort, make_sources


def _source(store: SessionStore) -> LogicalDataset:
    return make_sources(session_id="session", store_id=store.store_id).population(
        ref.entity("sales.customers")
    )


def _input(value: ArtifactDescriptor) -> RunDatasetInput:
    return RunDatasetInput(
        value.definition_fingerprint,
        value.row_contract.shape_id,
        value.row_contract_fingerprint,
        value.row_set_contract_fingerprint,
        ("session.population",),
        ("entity:sales.customers",),
    )


def _descriptor(artifact_ref: str, *, metric: bool = False) -> ArtifactDescriptor:
    value = descriptor(metric=metric)
    receipt = replace(
        value.storage_receipt,
        project_relative_path=value.storage_receipt.project_relative_path.replace(
            "/artifacts/artifact/", f"/artifacts/{artifact_ref}/"
        ),
    )
    parts = tuple(
        replace(
            part,
            storage_receipt=replace(
                part.storage_receipt,
                project_relative_path=part.storage_receipt.project_relative_path.replace(
                    "/artifacts/artifact/", f"/artifacts/{artifact_ref}/"
                ),
            ),
        )
        for part in value.retained_parts
    )
    return replace(value, storage_receipt=receipt, retained_parts=parts)


def _fixed(
    store: SessionStore, records: tuple[ArtifactRecord, ...], *, version: int = 1
) -> LogicalDataset:
    retained = tuple(
        recover_dataset(
            record,
            session_ref="session",
            store_id=store.store_id,
            action_port=NoIoActionPort(),
        )
        for record in records
    )
    row, rows = make_row_contracts()
    return _make_logical_dataset(
        owner=make_owner(session_id="session", store_id=store.store_id),
        registry=make_test_registry(),
        family_id="test",
        row_contract=row,
        row_set_contract=rows,
        operator_id="test.dsl_continuation",
        inputs=retained,
        contract_versions=(("dsl.observe_sum", f"v{version}"),),
    )


def _publish(
    store: SessionStore, source: LogicalDataset, *, run_ref: str, artifact_ref: str
) -> ArtifactRecord:
    value = _descriptor(artifact_ref)
    assert value.definition_fingerprint == source.definition_fingerprint
    store.admit("session", source_execution_key(source, run_ref), _input(value), run_ref=run_ref)
    return store.publish(run_ref, artifact_ref, value)


def test_v2_source_key_uses_one_run_identity_without_changing_the_definition(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path)
    store.create_session("dsl", session_ref="session")
    source = _source(store)
    first = source_execution_key(source, "run_first")
    second = source_execution_key(source, "run_second")
    assert source.definition_fingerprint == _source(store).definition_fingerprint
    assert first == source_execution_key(source, "run_first")
    assert first != second
    assert first != execution_key(source.definition_fingerprint)
    assert len(first) == len(second) == 64
    assert store.incomplete("session") == ()
    with pytest.raises(DatasetConstructionError):
        source_execution_key(source, "invalid run ref")


def test_v2_fixed_key_binds_ordered_exact_receipts_and_method_version(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("dsl", session_ref="session")
    source = _source(store)
    first = _publish(store, source, run_ref="run_first", artifact_ref="artifact_first")
    second = _publish(store, source, run_ref="run_second", artifact_ref="artifact_second")
    fixed = _fixed(store, (first, second))
    key = fixed_execution_key(fixed, (first, second))
    assert key == fixed_execution_key(fixed, (first, second))
    assert key != fixed_execution_key(_fixed(store, (second, first)), (second, first))
    assert key != fixed_execution_key(_fixed(store, (first, second), version=2), (first, second))
    assert key != execution_key(fixed.definition_fingerprint)

    changed_receipt = replace(
        first,
        descriptor=replace(
            first.descriptor,
            storage_receipt=replace(first.descriptor.storage_receipt, bytes_hash="b" * 64),
        ),
    )
    changed_fixed = _fixed(store, (changed_receipt, second))
    assert changed_fixed.definition_fingerprint == fixed.definition_fingerprint
    assert fixed_execution_key(changed_fixed, (changed_receipt, second)) != key


def test_fixed_binding_rejects_wrong_selected_artifact_without_reading_rows(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path)
    store.create_session("dsl", session_ref="session")
    source = _source(store)
    first = _publish(store, source, run_ref="run_first", artifact_ref="artifact_first")
    second = _publish(store, source, run_ref="run_second", artifact_ref="artifact_second")
    fixed = _fixed(store, (first, second))
    for records in (
        (second, first),
        (first,),
        (replace(first, session_ref="foreign"), second),
        (replace(first, producing_run_ref="run_other"), second),
        (replace(first, artifact_ref="artifact_other"), second),
        (replace(first, evidence=replace(first.evidence, evidence_digest="b" * 64)), second),
        (
            replace(
                first,
                descriptor=replace(
                    first.descriptor,
                    storage_receipt=replace(first.descriptor.storage_receipt, bytes_hash="b" * 64),
                ),
            ),
            second,
        ),
    ):
        with pytest.raises(IntegrityError, match="fixed input"):
            fixed_execution_key(fixed, records)
    assert store.incomplete("session") == ()


def test_same_store_fixed_input_can_belong_to_a_different_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("origin", session_ref="session")
    store.create_session("consumer", session_ref="consumer")
    record = _publish(store, _source(store), run_ref="run_first", artifact_ref="artifact_first")
    retained = recover_dataset(
        record, session_ref="session", store_id=store.store_id, action_port=NoIoActionPort()
    )
    row, rows = make_row_contracts()
    consumer = _make_logical_dataset(
        owner=make_owner(session_id="consumer", store_id=store.store_id),
        registry=make_test_registry(),
        family_id="test",
        row_contract=row,
        row_set_contract=rows,
        operator_id="test.dsl_continuation",
        inputs=(retained,),
        contract_versions=(("dsl.observe_sum", "v1"),),
    )
    assert fixed_execution_key(consumer, (record,)) != execution_key(
        consumer.definition_fingerprint
    )


def test_fixed_key_includes_every_retained_part_receipt(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("dsl", session_ref="session")
    source = make_sources(session_id="session", store_id=store.store_id).observe(
        [ref.metric("sales.revenue"), ref.metric("sales.order_count")]
    )
    value = _descriptor("artifact_metric", metric=True)
    assert value.definition_fingerprint == source.definition_fingerprint
    store.admit(
        "session",
        source_execution_key(source, "run_metric"),
        _input(value),
        run_ref="run_metric",
    )
    record = store.publish("run_metric", "artifact_metric", value)
    fixed = _fixed(store, (record,))
    key = fixed_execution_key(fixed, (record,))
    assert record.descriptor.retained_parts
    part, *rest = record.descriptor.retained_parts
    altered = replace(
        record,
        descriptor=replace(
            record.descriptor,
            retained_parts=(
                replace(
                    part,
                    storage_receipt=replace(part.storage_receipt, bytes_hash="b" * 64),
                ),
                *rest,
            ),
        ),
    )
    altered_fixed = _fixed(store, (altered,))
    assert altered_fixed.definition_fingerprint == fixed.definition_fingerprint
    assert fixed_execution_key(altered_fixed, (altered,)) != key


def test_mixed_and_wrong_key_routes_reject_before_run_admission(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("dsl", session_ref="session")
    source = _source(store)
    record = _publish(store, source, run_ref="run_first", artifact_ref="artifact_first")
    fixed = _fixed(store, (record,))
    retained = recover_dataset(
        record, session_ref="session", store_id=store.store_id, action_port=NoIoActionPort()
    )
    row, rows = make_row_contracts()
    mixed = _make_logical_dataset(
        owner=make_owner(session_id="session", store_id=store.store_id),
        registry=make_test_registry(),
        family_id="test",
        row_contract=row,
        row_set_contract=rows,
        operator_id="test.mixed",
        inputs=(source, retained),
        contract_versions=(("dsl.observe_sum", "v1"),),
    )
    with pytest.raises(DatasetConstructionError, match="live source and explicit Artifact"):
        source_execution_key(mixed, "run_second")
    with pytest.raises(DatasetConstructionError, match="live source and explicit Artifact"):
        fixed_execution_key(mixed, (record,))
    with pytest.raises(DatasetConstructionError):
        source_execution_key(fixed, "run_second")
    with pytest.raises(DatasetConstructionError):
        fixed_execution_key(source, ())
    assert store.incomplete("session") == ()


def test_store_keeps_two_exact_source_results_and_failed_retry_cannot_replace_them(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path)
    store.create_session("dsl", session_ref="session")
    source = _source(store)
    first = _publish(store, source, run_ref="run_first", artifact_ref="artifact_first")
    second = _publish(store, source, run_ref="run_second", artifact_ref="artifact_second")
    assert first.execution_key_digest != second.execution_key_digest
    assert store.lookup("session", first.execution_key_digest) == first
    assert store.lookup("session", second.execution_key_digest) == second
    assert store.artifact("artifact_first") == first
    assert store.artifact("artifact_second") == second
    with pytest.raises(IntegrityError, match="already has an Artifact"):
        store.admit("session", first.execution_key_digest, _input(first.descriptor))

    third_key = source_execution_key(source, "run_failed")
    store.admit("session", third_key, _input(first.descriptor), run_ref="run_failed")
    from marivo.analysis.errors import AnalysisRepair
    from marivo.introspection.live.model import LiveHelpTarget

    store.fail(
        "run_failed",
        RunFailure(
            "stage_execution",
            "execution_failed",
            "safe failure",
            "run",
            "rows",
            "source stopped",
            AnalysisRepair(
                kind="retry",
                action="Retry the action.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="runtime.runs"),
            ),
        ),
    )
    reopened = SessionStore.open_existing(tmp_path)
    assert reopened.lookup("session", third_key) is None
    assert reopened.artifact("artifact_first") == first
    assert reopened.artifact("artifact_second") == second
    failed = reopened.run("run_failed")
    assert failed is not None and failed.lifecycle == "failed"


def test_lost_publication_acknowledgement_reads_back_the_original_run(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.create_session("dsl", session_ref="session")
    source = _source(store)
    key = source_execution_key(source, "run_first")
    value = _descriptor("artifact_first")
    store.admit("session", key, _input(value), run_ref="run_first")

    def lost_ack(point: str) -> None:
        if point == "after_commit":
            raise RuntimeError("acknowledgement lost")

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        store.publish("run_first", "artifact_first", value, event=lost_ack)
    reopened = SessionStore.open_existing(tmp_path)
    with session_writer_guard(reopened.layout.lock_path("session"), session_ref="session"):
        reconcile_session(reopened, "session", event=lambda _: None)
    recovered = reopened.lookup("session", key)
    assert recovered is not None
    assert recovered.artifact_ref == "artifact_first"
    assert recovered.producing_run_ref == "run_first"
    producing_run = reopened.run("run_first")
    assert producing_run is not None
    assert producing_run.output_artifact_ref == recovered.artifact_ref
    assert reopened.incomplete("session") == ()
