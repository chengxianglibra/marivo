"""Local Frame authority retains the original Store without production bindings."""

from __future__ import annotations

import gc
import weakref
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.types import Owner
from marivo.analysis.core.model import CoreRuleError
from marivo.analysis.errors import ArtifactNotFoundError
from marivo.analysis.materialization.admission import DatasetRuntime
from marivo.analysis.materialization.errors import (
    IntegrityError,
    MaterializationError,
    SessionBusyError,
)
from marivo.analysis.materialization.frame_services import LocalFrameServices
from marivo.analysis.materialization.graph_reading import OutputRead
from marivo.analysis.materialization.pandas_export import export_frame
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.session.core import Session
from marivo.datasource import credentials as cr
from marivo.semantic import catalog


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.chdir(tmp_path)
    return mv.session.create("local-services", timezone="UTC")


def _forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("local service reached a source, mutation, reopen or payload boundary")


def _publish(session: Session) -> mv.Artifact:
    return (
        session.values(by=(), columns={"answer": None}, rows=(((), {"answer": 7}),))
        .persist()
        .artifact
    )


def test_local_identity_preserves_exact_store_and_has_no_callback_or_result_fields(
    session: Session,
) -> None:
    runtime = session._runtime
    local = runtime.local
    assert local is runtime.local
    assert local.store is runtime.store
    assert local.session_ref == runtime.session_ref == session.id
    assert local.owner == Owner(session.id, runtime.store.store_id)
    assert local.record() == session._record()
    assert tuple(field.name for field in fields(local)) == ("store", "session_ref")
    for name in ("session_ref", "store"):
        with pytest.raises(FrozenInstanceError):
            setattr(local, name, None)
    with pytest.raises(IntegrityError, match="Session is absent"):
        LocalFrameServices(local.store, "missing-session")


def test_local_metadata_reads_never_prepare_sources_mutate_or_read_payload(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _publish(session)
    local = session._runtime.local
    monkeypatch.setattr(Session, "_sources", _forbidden)
    monkeypatch.setattr(catalog, "load", _forbidden)
    monkeypatch.setattr(cr, "current_resolver", _forbidden)
    monkeypatch.setattr(cr, "check_binding", _forbidden)
    monkeypatch.setattr(DatasetRuntime, "connection_service", _forbidden)
    monkeypatch.setattr(SessionStore, "_write", _forbidden)
    monkeypatch.setattr(SessionStore, "open_existing", _forbidden)
    monkeypatch.setattr(OutputRead, "_open", _forbidden)
    monkeypatch.setattr("marivo.analysis.session.reconcile_session", _forbidden)
    monkeypatch.setattr(
        "marivo.analysis.materialization.admission.session_writer_guard", _forbidden
    )

    assert LocalFrameServices(local.store, local.session_ref).owner == local.owner
    assert session.name == "local-services"
    saved = session.persisted_frame(artifact.id)
    reopened = saved.artifact
    assert reopened._manifest == artifact._manifest
    assert local.artifact(artifact.id)._manifest == reopened._manifest
    assert local.runs() == session.runs()
    assert local.get_run(artifact._manifest.run_id) == session.get_run(artifact._manifest.run_id)
    assert local.runs() == session.runs()
    backing = local.open_frame(artifact.id)
    assert local.preview(backing, 0).rows.empty
    saved.show(n=0)
    reopened.show()
    assert reopened.findings().items == ()
    assert reopened.evidence_digest() == artifact.evidence_digest()
    assert saved.persist() is saved


def test_local_data_reads_use_receipts_and_do_not_cache_results(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _publish(session)
    local = session._runtime.local
    monkeypatch.setattr(catalog, "load", _forbidden)
    monkeypatch.setattr(cr, "check_binding", _forbidden)
    monkeypatch.setattr(DatasetRuntime, "connection_service", _forbidden)
    monkeypatch.setattr(SessionStore, "_write", _forbidden)
    backing = local.open_frame(artifact.id)
    completed = local.read_complete(backing)
    result = export_frame(
        completed.data.schema, completed.data.copy_rows(), completed.data.copy_attachments()
    )
    expected = session.persisted_frame(artifact.id).to_pandas()
    pd.testing.assert_frame_equal(result, expected)
    assert result.attrs == expected.attrs
    assert result.iloc[0, 0] == 7
    result.iloc[:, 0] = 99
    result.attrs.clear()
    second = local.read_complete(backing)
    again = export_frame(
        second.data.schema, second.data.copy_rows(), second.data.copy_attachments()
    )
    assert again.iloc[0, 0] == 7
    assert again.attrs
    pd.testing.assert_frame_equal(again, expected)
    assert again.attrs == expected.attrs
    reference = weakref.ref(again)
    del again
    gc.collect()
    assert reference() is None

    # A second read consumes the actual backing again, rather than a cached table.
    payload = local.store.project_root / artifact._manifest.data.rows.receipt.project_relative_path
    (payload / artifact._manifest.data.rows.receipt.file_manifest[0].relative_path).unlink()
    with pytest.raises(MaterializationError, match="FileNotFoundError"):
        local.read_complete(backing)


def test_local_reads_reject_foreign_session_and_store(session: Session, tmp_path: Path) -> None:
    artifact = _publish(session)
    backing = session._runtime.local.open_frame(artifact.id)
    other_session = mv.session.create("foreign", timezone="UTC")
    local = other_session._runtime.local
    with pytest.raises(ArtifactNotFoundError):
        local.artifact(artifact.id)
    with pytest.raises(ArtifactNotFoundError):
        local.read_complete(backing)
    with pytest.raises(ArtifactNotFoundError):
        session._runtime.local.artifact("missing-artifact")

    other_store = SessionStore(tmp_path / "other-project")
    other_store.create_session("same-id", session_ref=session.id)
    same_id = LocalFrameServices(other_store, session.id)
    assert same_id.owner != session._runtime.local.owner
    with pytest.raises(ArtifactNotFoundError):
        same_id.artifact(artifact.id)
    with pytest.raises(ArtifactNotFoundError):
        same_id.read_complete(backing)


@pytest.mark.parametrize("n", [-1, True])
def test_invalid_preview_precedes_metadata_or_payload_reads(
    session: Session, monkeypatch: pytest.MonkeyPatch, n: int
) -> None:
    artifact = _publish(session)
    local = session._runtime.local
    backing = local.open_frame(artifact.id)
    foreign = mv.session.create("foreign-preview")._runtime.local
    monkeypatch.setattr(SessionStore, "_read", _forbidden)
    monkeypatch.setattr(OutputRead, "_open", _forbidden)
    with pytest.raises(CoreRuleError, match="nonnegative integer prefix"):
        foreign.preview(backing, n)


class _Resolver:
    def resolve(self, request: cr.CredentialRequest) -> cr.SecretValue:
        raise AssertionError("local service resolved source credentials")


class _SessionHook:
    def __init__(self) -> None:
        self.session: Session | None = None

    def __call__(self, point: str) -> None:
        pass


def test_retained_local_service_does_not_keep_runtime_session_hook_or_resolver(
    authoring_evidence_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = _Resolver()
    hook = _SessionHook()
    with cr.bind_resolver(resolver):
        runtime = DatasetRuntime.create(
            authoring_evidence_project, "lifetime", timezone="UTC", event=hook
        )
    session = Session._from_runtime(runtime)
    hook.session = session
    artifact = _publish(session)
    region = session.catalog.require(ms.ref.dimension("sales.orders.region"))
    memory = session.values(
        by=(region,), columns={"answer": None}, rows=((("north",), {"answer": 7}),)
    ).compute()
    del region
    derived = memory.select({"copy": memory["answer"]})
    column = memory["answer"]
    axis = memory.axes[0]
    memory_ref = weakref.ref(memory)
    local = runtime.local
    original_store = runtime.store
    runtime_ref, hook_ref, resolver_ref = (
        weakref.ref(runtime),
        weakref.ref(hook),
        weakref.ref(resolver),
    )
    del runtime, session, hook, resolver, memory
    gc.collect()
    assert runtime_ref() is None and hook_ref() is None and resolver_ref() is None
    assert local.store is original_store

    monkeypatch.setattr(SessionStore, "open_existing", _forbidden)
    monkeypatch.setattr(DatasetRuntime, "open", _forbidden)
    monkeypatch.setattr(DatasetRuntime, "create", _forbidden)
    assert local.artifact(artifact.id).id == artifact.id
    backing = local.open_frame(artifact.id)
    assert local.read_complete(backing).data.copy_rows().iloc[0, 0] == 7
    monkeypatch.setattr(Session, "_sources", _forbidden)
    monkeypatch.setattr(catalog, "load", _forbidden)
    monkeypatch.setattr(DatasetRuntime, "connection_service", _forbidden)
    computed = derived.compute()
    assert computed.to_pandas()[("value", "copy")].tolist() == [7]
    assert computed.persist().artifact.session_id == local.session_ref
    column.show()
    axis.show()
    assert memory_ref() is not None
    del derived, column
    gc.collect()
    assert memory_ref() is not None
    retained = memory_ref()
    assert retained is not None
    column = retained["answer"]
    del retained, axis
    gc.collect()
    assert memory_ref() is not None
    column.show()
    del column
    gc.collect()
    assert memory_ref() is None


def test_retained_authority_uses_original_writer_exclusion(session: Session) -> None:
    local = session._runtime.local
    before = session.runs()
    with (
        session_writer_guard(
            local.store.layout.lock_path(local.session_ref), session_ref=local.session_ref
        ),
        pytest.raises(SessionBusyError),
    ):
        # Pure memory evaluation remains independent of the writer lock.
        memory = session.values(
            by=(), columns={"answer": None}, rows=(((), {"answer": 7}),)
        ).compute()
        assert memory.to_pandas()[("value", "answer")].tolist() == [7]
        memory.persist()
    assert session.runs() == before
    assert local.store.resources(local.session_ref) == ()
    assert _publish(session).session_id == local.session_ref
