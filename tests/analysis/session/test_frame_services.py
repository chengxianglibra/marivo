"""Local Frame authority retains the original Store without production bindings."""

from __future__ import annotations

import gc
import weakref
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pandas as pd
import pytest

import marivo.analysis as mv
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
from marivo.analysis.materialization.graph_reading import ExportRead, OutputRead, PreviewRead
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.writer_guard import session_writer_guard
from marivo.analysis.session.core import Session
from marivo.datasource import credentials as cr
from marivo.semantic import catalog


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("local-services", report_timezone="UTC")


def _forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("local service reached a source, mutation, reopen or payload boundary")


def _publish(session: Session) -> mv.Artifact:
    return session.values(by=(), columns={"answer": None}, rows=(((), {"answer": 7}),)).execute()


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
    with pytest.raises(FrozenInstanceError):
        local.session_ref = "another-session"
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
    monkeypatch.setattr("marivo.analysis.materialization.admission.reconcile_session", _forbidden)
    monkeypatch.setattr(
        "marivo.analysis.materialization.admission.session_writer_guard", _forbidden
    )

    assert LocalFrameServices(local.store, local.session_ref).owner == local.owner
    assert session.name == "local-services"
    reopened = session.artifact(artifact.id)
    assert reopened._manifest == artifact._manifest
    assert local.artifact(artifact.id)._manifest == reopened._manifest
    assert local.runs() == session.runs()
    assert local.get_run(artifact._manifest.run_id) == session.get_run(artifact._manifest.run_id)
    assert local.graph() == session.graph()
    assert local.read(artifact._manifest, PreviewRead(0)).empty


def test_local_data_reads_use_receipts_and_do_not_cache_results(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _publish(session)
    local = session._runtime.local
    monkeypatch.setattr(catalog, "load", _forbidden)
    monkeypatch.setattr(cr, "check_binding", _forbidden)
    monkeypatch.setattr(DatasetRuntime, "connection_service", _forbidden)
    monkeypatch.setattr(SessionStore, "_write", _forbidden)
    result = local.read(artifact._manifest, ExportRead())
    pd.testing.assert_frame_equal(result, artifact.to_pandas())
    assert int(result.iloc[0, 0]) == 7
    result.iloc[:, 0] = 99
    result.attrs.clear()
    again = local.read(artifact._manifest, ExportRead())
    assert int(again.iloc[0, 0]) == 7
    assert again.attrs
    reference = weakref.ref(again)
    del again
    gc.collect()
    assert reference() is None

    # A second read consumes the actual backing again, rather than a cached table.
    payload = local.store.project_root / artifact._manifest.data.rows.receipt.project_relative_path
    (payload / artifact._manifest.data.rows.receipt.file_manifest[0].relative_path).unlink()
    with pytest.raises(MaterializationError, match="FileNotFoundError"):
        local.read(artifact._manifest, ExportRead())


def test_local_reads_reject_foreign_session_and_store(session: Session, tmp_path: Path) -> None:
    artifact = _publish(session)
    other_session = mv.session.get_or_create("foreign", report_timezone="UTC")
    local = other_session._runtime.local
    with pytest.raises(ArtifactNotFoundError):
        local.artifact(artifact.id)
    with pytest.raises(ArtifactNotFoundError):
        local.read(artifact._manifest, ExportRead())
    with pytest.raises(ArtifactNotFoundError):
        session._runtime.local.artifact("missing-artifact")

    other_store = SessionStore(tmp_path / "other-project")
    other_store.create_session("same-id", session_ref=session.id)
    same_id = LocalFrameServices(other_store, session.id)
    assert same_id.owner != session._runtime.local.owner
    with pytest.raises(ArtifactNotFoundError):
        same_id.artifact(artifact.id)
    with pytest.raises(IntegrityError):
        same_id.read(artifact._manifest, ExportRead())


@pytest.mark.parametrize("n", [-1, True])
def test_invalid_preview_precedes_metadata_or_payload_reads(
    session: Session, monkeypatch: pytest.MonkeyPatch, n: int
) -> None:
    artifact = _publish(session)
    local = session._runtime.local
    monkeypatch.setattr(SessionStore, "_read", _forbidden)
    monkeypatch.setattr(OutputRead, "_open", _forbidden)
    with pytest.raises(CoreRuleError, match="nonnegative preview"):
        local.read(replace(artifact._manifest, session_id="foreign"), PreviewRead(n))


class _Resolver:
    def resolve(self, request: cr.CredentialRequest) -> cr.SecretValue:
        raise AssertionError("local service resolved source credentials")


class _SessionHook:
    def __init__(self) -> None:
        self.session: Session | None = None

    def __call__(self, point: str) -> None:
        pass


def test_retained_local_service_does_not_keep_runtime_session_hook_or_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = _Resolver()
    hook = _SessionHook()
    with cr.bind_resolver(resolver):
        runtime = DatasetRuntime.create(tmp_path, "lifetime", report_timezone="UTC", event=hook)
    session = Session._from_runtime(runtime)
    hook.session = session
    artifact = _publish(session)
    local = runtime.local
    original_store = runtime.store
    runtime_ref, hook_ref, resolver_ref = (
        weakref.ref(runtime),
        weakref.ref(hook),
        weakref.ref(resolver),
    )
    del runtime, session, hook, resolver
    gc.collect()
    assert runtime_ref() is hook_ref() is resolver_ref() is None
    assert local.store is original_store

    monkeypatch.setattr(SessionStore, "open_existing", _forbidden)
    monkeypatch.setattr(DatasetRuntime, "open", _forbidden)
    monkeypatch.setattr(DatasetRuntime, "create", _forbidden)
    assert local.artifact(artifact.id).id == artifact.id
    assert int(local.read(artifact._manifest, ExportRead()).iloc[0, 0]) == 7


def test_retained_authority_uses_original_writer_exclusion(session: Session) -> None:
    local = session._runtime.local
    before = session.runs()
    with (
        session_writer_guard(
            local.store.layout.lock_path(local.session_ref), session_ref=local.session_ref
        ),
        pytest.raises(SessionBusyError),
    ):
        _publish(session)
    assert session.runs() == before
    assert local.store.resources(local.session_ref) == ()
    assert _publish(session).session_id == local.session_ref
