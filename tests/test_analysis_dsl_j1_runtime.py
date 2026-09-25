"""Private J1 Runtime identities and exact fixed-input reuse."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from threading import Event

import duckdb
import ibis
import ibis.expr.types as ir
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.materialization import dataset_publication, dsl_j1_runtime, execution_key
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.contracts import ArtifactRecord, RunDatasetInput
from marivo.analysis.materialization.dsl_j1_artifact import load_j1_artifact
from marivo.analysis.materialization.errors import MaterializationError, SessionBusyError
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.dsl_j1 import J1Context, J1Observed
from marivo.analysis.observation.dsl_j1_dataset import MaterializedJ1Dataset
from marivo.analysis.operators.dsl_j1_contracts import j1_numeric_method
from tests.shared_fixtures import DslCase, DslCaseFactory


@contextmanager
def _source(case: DslCase) -> Iterator[tuple[ibis.BaseBackend, dict[str, ir.Table]]]:
    backend = ibis.duckdb.connect(str(case.database_path))
    try:
        domain = case.names.domain
        yield (
            backend,
            {
                f"{domain}.customer": backend.table("customer"),
                f"{domain}.order": backend.table("order"),
            },
        )
    finally:
        backend.disconnect()


def _observed(case: DslCase, store: SessionStore) -> J1Observed:
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "session", store.store_id)
    domain = case.names.domain
    channel = ms.ref.dimension(f"{domain}.order.channel")
    return context.members(ms.ref.entity(f"{domain}.customer")).observe(
        ms.ref.metric(f"{domain}.revenue"),
        during=mv.time_scope(start="2026-08-01", end="2026-09-01"),
        via=ms.ref.relationship(f"{domain}.order_buyer"),
        coordinates=(channel,),
    )


@pytest.mark.runtime
def test_j1_runtime_reexecutes_source_and_reuses_fixed_result(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    store = SessionStore(case.root)
    store.create_session("w4", session_ref="session")
    runtime = DatasetRuntime(store, "session")
    domain = case.names.domain
    channel = ms.ref.dimension(f"{domain}.order.channel")
    observed = _observed(case, store)
    opens = 0

    def source() -> AbstractContextManager[tuple[ibis.BaseBackend, dict[str, ir.Table]]]:
        nonlocal opens
        opens += 1
        return _source(case)

    definition = observed.root.definition_fingerprint
    first = runtime.execute_j1(observed, source=source)
    first_evaluations = runtime.statistics.j1_source_evaluations
    first_record = store.artifact(first.state.artifact_ref.ref)
    assert first_record is not None
    assert first_record.descriptor.j1_exchange is not None
    assert first_record.descriptor.j1_exchange.input_binding == first_record.execution_key_digest
    original = store.run(first_record.producing_run_ref)
    assert (
        original is not None and original.execution_key_digest == first_record.execution_key_digest
    )

    conn = duckdb.connect(str(case.database_path))
    try:
        conn.execute("UPDATE \"order\" SET amount = amount + 50 WHERE order_id = 'j1_a'")
    finally:
        conn.close()

    second = runtime.execute_j1(observed, source=source)
    second_evaluations = runtime.statistics.j1_source_evaluations
    second_record = store.artifact(second.state.artifact_ref.ref)
    assert second_record is not None
    assert observed.root.definition_fingerprint == definition
    assert first_record.artifact_ref != second_record.artifact_ref
    assert first_record.producing_run_ref != second_record.producing_run_ref
    assert first_record.execution_key_digest != second_record.execution_key_digest
    assert opens == 2
    assert (first_evaluations, second_evaluations) == (1, 1)
    assert store.artifact(first_record.artifact_ref) == first_record
    first_rows = load_j1_artifact(
        store,
        "session",
        first_record.artifact_ref,
        observed,
        input_binding=first_record.execution_key_digest,
    ).primary.to_pylist()
    second_rows = load_j1_artifact(
        store,
        "session",
        second_record.artifact_ref,
        observed,
        input_binding=second_record.execution_key_digest,
    ).primary.to_pylist()
    assert next(row["value"] for row in first_rows if row["member"] == "A") == 450
    assert next(row["value"] for row in second_rows if row["member"] == "A") == 500

    grouped = observed.group_by(channel)
    local = runtime.execute_j1(
        grouped, input_node=observed, input_artifact_ref=first_record.artifact_ref
    )
    assert runtime.statistics.j1_source_evaluations == 0
    assert runtime.statistics.j1_fixed_cache_hits == 0
    count_before = len(runtime.runs(limit=20).items)
    repeated = runtime.execute_j1(
        grouped, input_node=observed, input_artifact_ref=first_record.artifact_ref
    )
    assert repeated.state.artifact_ref == local.state.artifact_ref
    assert len(runtime.runs(limit=20).items) == count_before
    assert opens == 2
    assert runtime.statistics.j1_source_evaluations == 0
    assert runtime.statistics.j1_fixed_cache_hits == 1

    local_second = runtime.execute_j1(
        grouped, input_node=observed, input_artifact_ref=second_record.artifact_ref
    )
    assert local_second.state.artifact_ref != local.state.artifact_ref
    first_local_record = store.artifact(local.state.artifact_ref.ref)
    second_local_record = store.artifact(local_second.state.artifact_ref.ref)
    assert first_local_record is not None and second_local_record is not None
    assert first_local_record.execution_key_digest != second_local_record.execution_key_digest
    assert first_local_record.descriptor.j1_exchange is not None
    assert (
        first_local_record.descriptor.j1_exchange.input_binding
        == first_local_record.execution_key_digest
    )
    reloaded_local = load_j1_artifact(
        store,
        "session",
        first_local_record.artifact_ref,
        grouped,
        input_binding=first_local_record.execution_key_digest,
    )
    assert not reloaded_local.primary.schema.field("non_null_count").nullable
    reloaded_second = load_j1_artifact(
        store,
        "session",
        second_local_record.artifact_ref,
        grouped,
        input_binding=second_local_record.execution_key_digest,
    )
    first_groups = {row["group"]: row["value"] for row in reloaded_local.primary.to_pylist()}
    second_groups = {row["group"]: row["value"] for row in reloaded_second.primary.to_pylist()}
    assert first_groups == {"mobile": 150, "web": 850}
    assert second_groups == {"mobile": 150, "web": 900}

    child = """
import duckdb
import sys
import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.materialization.dsl_j1_artifact import load_j1_artifact
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.observation.dsl_j1 import J1Context

def forbidden(*args, **kwargs):
    raise AssertionError('cold recovery opened DuckDB')

duckdb.connect = forbidden
root, domain, first_ref, first_key, second_ref, second_key = sys.argv[1:]
catalog = ms.load(workspace_dir=root)
store = SessionStore.open_existing(root)
state = catalog._state
context = J1Context(state.registry, state.sidecar, 'session', store.store_id)
channel = ms.ref.dimension(f'{domain}.order.channel')
node = context.members(ms.ref.entity(f'{domain}.customer')).observe(
    ms.ref.metric(f'{domain}.revenue'),
    during=mv.time_scope(start='2026-08-01', end='2026-09-01'),
    via=ms.ref.relationship(f'{domain}.order_buyer'),
    coordinates=(channel,),
)
for artifact_ref, key, expected in ((first_ref, first_key, 450), (second_ref, second_key, 500)):
    rows = load_j1_artifact(store, 'session', artifact_ref, node, input_binding=key).primary.to_pylist()
    assert next(row['value'] for row in rows if row['member'] == 'A') == expected
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            child,
            str(case.root),
            domain,
            first_record.artifact_ref,
            first_record.execution_key_digest,
            second_record.artifact_ref,
            second_record.execution_key_digest,
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "MARIVO_PROJECT_ROOT": str(case.root)},
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.runtime
def test_j1_fixed_cache_separates_receipt_method_and_protocol_versions(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    store = SessionStore(case.root)
    store.create_session("w4-key-versions", session_ref="session")
    runtime = DatasetRuntime(store, "session")
    observed = _observed(case, store)
    source = runtime.execute_j1(observed, source=lambda: _source(case))
    source_record = store.artifact(source.state.artifact_ref.ref)
    assert source_record is not None
    grouped = observed.group_by(ms.ref.dimension(f"{case.names.domain}.order.channel"))
    fixed = runtime.execute_j1(
        grouped, input_node=observed, input_artifact_ref=source_record.artifact_ref
    )
    fixed_record = store.artifact(fixed.state.artifact_ref.ref)
    assert fixed_record is not None
    retained = runtime._recover(source_record)
    assert isinstance(retained, MaterializedJ1Dataset)
    definition = dsl_j1_runtime._binding(runtime, grouped, retained=retained, live=False)
    key = execution_key.fixed_execution_key(definition, (source_record,))
    assert key == fixed_record.execution_key_digest

    first_part = source_record.descriptor.retained_parts[0]
    changed_hash = "0" * 64 if first_part.storage_receipt.bytes_hash != "0" * 64 else "1" * 64
    changed_part = replace(
        first_part,
        storage_receipt=replace(first_part.storage_receipt, bytes_hash=changed_hash),
    )
    changed_record = replace(
        source_record,
        descriptor=replace(
            source_record.descriptor,
            retained_parts=(changed_part, *source_record.descriptor.retained_parts[1:]),
        ),
    )
    changed_receipt_key = execution_key.fixed_execution_key(definition, (changed_record,))

    method = j1_numeric_method(grouped.root)
    assert method is not None
    upgraded = replace(
        method,
        contract=replace(method.contract, version=method.contract.version + 1),
        implementations=tuple(
            replace(item, version=item.version + 1) for item in method.implementations
        ),
    )
    with monkeypatch.context() as patch:
        patch.setattr(dsl_j1_runtime, "j1_numeric_method", lambda _root: upgraded)
        upgraded_definition = dsl_j1_runtime._binding(
            runtime, grouped, retained=retained, live=False
        )
    changed_method_key = execution_key.fixed_execution_key(upgraded_definition, (source_record,))
    with monkeypatch.context() as patch:
        patch.setattr(execution_key, "_DSL_PROTOCOL", "marivo.dataset_execution_key/v3")
        changed_protocol_key = execution_key.fixed_execution_key(definition, (source_record,))
    for changed in (changed_receipt_key, changed_method_key, changed_protocol_key):
        assert changed != key
        assert store.lookup("session", changed) is None

    original_artifact = SessionStore.artifact

    def changed_artifact(self: SessionStore, artifact_ref: str) -> ArtifactRecord | None:
        if artifact_ref == source_record.artifact_ref:
            return changed_record
        return original_artifact(self, artifact_ref)

    with monkeypatch.context() as patch:
        patch.setattr(SessionStore, "artifact", changed_artifact)
        with pytest.raises(MaterializationError):
            runtime.execute_j1(
                grouped, input_node=observed, input_artifact_ref=source_record.artifact_ref
            )
    assert runtime.last_run_ref is not None
    failed = store.run(runtime.last_run_ref)
    assert failed is not None and failed.lifecycle == "failed"
    assert store.artifact(fixed_record.artifact_ref) == fixed_record


@pytest.mark.runtime
def test_j1_fixed_contender_retries_to_exact_hit(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    store = SessionStore(case.root)
    store.create_session("w4-contenders", session_ref="session")
    observed = _observed(case, store)
    source_runtime = DatasetRuntime(store, "session")
    saved = source_runtime.execute_j1(observed, source=lambda: _source(case))
    grouped = observed.group_by(ms.ref.dimension(f"{case.names.domain}.order.channel"))
    entered = Event()
    release = Event()

    def hold_publication(point: str) -> None:
        if point == "before_commit":
            entered.set()
            assert release.wait(5)

    first_runtime = DatasetRuntime(store, "session", event=hold_publication)
    contender = DatasetRuntime(SessionStore.open_existing(case.root), "session")

    def execute(runtime: DatasetRuntime) -> str:
        result = runtime.execute_j1(
            grouped, input_node=observed, input_artifact_ref=saved.state.artifact_ref.ref
        )
        return result.state.artifact_ref.ref

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute, first_runtime)
        try:
            assert entered.wait(5)
            with pytest.raises(SessionBusyError):
                execute(contender)
        finally:
            release.set()
        first_ref = future.result()
    count_before = len(contender.runs(limit=20).items)
    assert execute(contender) == first_ref
    assert len(contender.runs(limit=20).items) == count_before


@pytest.mark.runtime
def test_j1_runtime_failure_and_uncertain_publication_keep_the_first_artifact(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    case = analysis_dsl_case_factory("j1")
    store = SessionStore(case.root)
    store.create_session("w4-failures", session_ref="session")
    runtime = DatasetRuntime(store, "session")
    observed = _observed(case, store)
    first = runtime.execute_j1(observed, source=lambda: _source(case))
    first_ref = first.state.artifact_ref.ref
    first_record = store.artifact(first_ref)
    assert first_record is not None

    def failed_source() -> AbstractContextManager[tuple[ibis.BaseBackend, dict[str, ir.Table]]]:
        raise RuntimeError("source unavailable before row admission")

    with pytest.raises(RuntimeError, match="source unavailable"):
        runtime.execute_j1(observed, source=failed_source)
    failed_ref = runtime.last_run_ref
    assert failed_ref is not None
    failed = store.run(failed_ref)
    assert failed is not None and failed.lifecycle == "failed"
    assert store.artifact(first_ref) == first_record

    def before_commit(point: str) -> None:
        if point == "before_commit":
            raise RuntimeError("publication stopped")

    failing_runtime = DatasetRuntime(store, "session", event=before_commit)
    with pytest.raises(RuntimeError, match="publication stopped"):
        failing_runtime.execute_j1(observed, source=lambda: _source(case))
    assert store.artifact(first_ref) == first_record
    assert store.run(failing_runtime.last_run_ref or "") is not None

    def after_commit(point: str) -> None:
        if point == "after_commit":
            raise RuntimeError("acknowledgement lost")

    uncertain_runtime = DatasetRuntime(store, "session", event=after_commit)
    recovered = uncertain_runtime.execute_j1(observed, source=lambda: _source(case))
    recovered_record = store.artifact(recovered.state.artifact_ref.ref)
    assert recovered_record is not None
    assert recovered_record.producing_run_ref == uncertain_runtime.last_run_ref
    assert store.artifact(first_ref) == first_record
    assert store.incomplete("session") == ()

    pending = store.admit(
        "session",
        "a" * 64,
        RunDatasetInput(
            observed.root.definition_fingerprint,
            first_record.descriptor.row_contract.shape_id,
            observed.root.row_contract_fingerprint,
            observed.root.row_set_contract_fingerprint,
            (observed.root.operator_id,),
            (),
        ),
        run_ref="run_pending",
    )
    assert pending.lifecycle == "incomplete"
    runtime.execute_j1(observed, source=lambda: _source(case))
    reconciled = store.run("run_pending")
    assert reconciled is not None and reconciled.lifecycle == "failed"
    assert store.artifact(first_ref) == first_record


@pytest.mark.runtime
def test_j1_failed_outcome_resolution_is_reported_without_source_replay(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    store = SessionStore(case.root)
    store.create_session("w4-resolution", session_ref="session")
    runtime = DatasetRuntime(store, "session")
    observed = _observed(case, store)
    opens = 0

    def failed_source() -> AbstractContextManager[tuple[ibis.BaseBackend, dict[str, ir.Table]]]:
        nonlocal opens
        opens += 1
        raise RuntimeError("source unavailable")

    def failed_resolution(*args: object, **kwargs: object) -> None:
        raise RuntimeError("outcome resolution unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(dataset_publication, "resolve_outcome", failed_resolution)
        with pytest.raises(MaterializationError, match="outcome resolution failed") as failure:
            runtime.execute_j1(observed, source=failed_source)
    assert failure.value.stage == "reconciliation"
    assert failure.value.run_ref == runtime.last_run_ref
    assert failure.value.__cause__ is None
    assert opens == 1
    assert runtime.last_run_ref is not None
    pending = store.run(runtime.last_run_ref)
    assert pending is not None and pending.lifecycle == "incomplete"
    assert store.lookup("session", pending.execution_key_digest) is None


@pytest.mark.runtime
def test_j1_runtime_rejects_fixed_member_plus_live_read_before_rows(
    analysis_dsl_case_factory: DslCaseFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = analysis_dsl_case_factory("j1")
    store = SessionStore(case.root)
    store.create_session("w4-mixed", session_ref="session")
    runtime = DatasetRuntime(store, "session")
    state = case.catalog._state
    context = J1Context(state.registry, state.sidecar, "session", store.store_id)
    domain = case.names.domain
    members = context.members(ms.ref.entity(f"{domain}.customer"))
    saved = runtime.execute_j1(members, source=lambda: _source(case))
    read = members.read(ms.ref.dimension(f"{domain}.customer.region"))
    count_before = len(runtime.runs(limit=20).items)
    monkeypatch.setattr(
        "marivo.analysis.materialization.dsl_j1_runtime.load_j1_artifact",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Artifact rows opened")),
    )
    monkeypatch.setattr(
        runtime,
        "_recover",
        lambda _record: (_ for _ in ()).throw(AssertionError("Artifact metadata recovered")),
    )
    with pytest.raises(DatasetConstructionError, match="live source and explicit Artifact"):
        runtime.execute_j1(
            read, input_node=members, input_artifact_ref=saved.state.artifact_ref.ref
        )
    assert len(runtime.runs(limit=20).items) == count_before
