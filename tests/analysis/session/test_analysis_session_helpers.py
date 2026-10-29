"""Repeatable named Session acquisition, exact recovery and read-only discovery."""

import sqlite3
from collections.abc import Callable
from inspect import Parameter, signature
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import SessionNotFoundError, SessionStateError
from marivo.analysis.materialization.contracts import SessionRecord
from marivo.analysis.materialization.errors import SessionBusyError
from marivo.analysis.materialization.store import SessionStore
from marivo.analysis.materialization.terminal_store import admit
from marivo.analysis.materialization.writer_guard import session_writer_guard
from tests.shared_fixtures import DslCaseFactory, terminal_run, terminal_runs


def test_lifecycle_surface_has_one_explicit_entrypoint() -> None:
    assert set(mv.session.__all__) == {"get_or_create", "resume", "recent", "inspect"}
    assert tuple(signature(mv.session.get_or_create).parameters) == (
        "name",
        "question",
        "timezone",
        "domains",
    )
    assert signature(mv.session.get_or_create).parameters["question"].kind is Parameter.KEYWORD_ONLY
    assert tuple(signature(mv.session.resume).parameters) == ("session_id",)
    for retired in ("create", "current", "abandon_run"):
        assert not hasattr(mv.session, retired)


def test_session_metadata_is_frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    original = mv.session.get_or_create(
        "review", question="Why?", timezone="UTC", domains=["sales", "sales"]
    )
    before = original._record()
    assert original.domains == ("sales",)
    assert mv.session.resume(original.id)._record() == before
    assert mv.session.inspect(original.id).question == "Why?"
    for field in ("id", "name", "question", "timezone", "domains", "created_at", "updated_at"):
        with pytest.raises(AttributeError):
            setattr(original, field, "changed")
    assert original._record() == before


def test_get_or_create_retains_frozen_context_without_current_or_source_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.analysis.timezone as timezone_owner

    monkeypatch.chdir(tmp_path)
    original = mv.session.get_or_create(
        "repeatable", question="Why?", timezone="+08:00", domains=["sales", "finance", "sales"]
    )
    before = original._record()
    monkeypatch.setattr(mv.Session, "_sources", lambda _: pytest.fail("recovery loaded semantics"))
    monkeypatch.setattr(
        timezone_owner,
        "resolve_system_timezone",
        lambda: pytest.fail("recovery rechecked the system timezone"),
    )
    assert mv.session.get_or_create("repeatable")._record() == before
    assert (
        mv.session.get_or_create(
            "repeatable", question="Why?", timezone="UTC+08:00", domains=["finance", "sales"]
        )._record()
        == before
    )
    assert original.timezone == "UTC+08:00"
    with sqlite3.connect(original._runtime.store.db_path) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='runtime_state'"
            ).fetchall()
            == []
        )
    assert len(mv.session.recent().items) == 1


@pytest.mark.parametrize(
    ("question", "timezone", "domains", "field"),
    [
        ("Changed", None, None, "question"),
        ("", None, None, "question"),
        (None, "UTC", None, "timezone"),
        (None, None, "finance", "domains"),
    ],
)
def test_get_or_create_rejects_explicit_conflicts_before_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    question: str | None,
    timezone: str | None,
    domains: str | None,
    field: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    original = mv.session.get_or_create(
        "review", question="Why?", timezone="Asia/Shanghai", domains="sales"
    )
    admit(original._runtime.store, original.id, "run_pending", "output_pending")
    before = original._record()
    with pytest.raises(SessionStateError) as caught:
        mv.session.get_or_create("review", question=question, timezone=timezone, domains=domains)
    error = caught.value
    assert error.location == f"mv.session.get_or_create({field}=...)"
    assert error.expected is not None and field in error.expected
    assert error.received is not None and field in error.received
    assert error.repair is not None and error.repair.candidates == (original.id,)
    assert error.repair.help_target.display == "analysis.session.get_or_create"
    assert original._record() == before
    assert terminal_run(original, "run_pending").lifecycle == "incomplete"


def test_get_or_create_recovers_pending_work_and_respects_active_writers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    original = mv.session.get_or_create("recover", timezone="UTC")
    admit(original._runtime.store, original.id, "run_pending", "output_pending")
    with (
        session_writer_guard(original._runtime.store.layout.lock_path(original.id)),
        pytest.raises(SessionBusyError) as caught,
    ):
        mv.session.get_or_create("recover")
    assert caught.value.session_ref == original.id
    assert terminal_run(original, "run_pending").lifecycle == "incomplete"
    recovered = mv.session.get_or_create("recover")
    assert recovered.id == original.id
    assert terminal_run(recovered, "run_pending").lifecycle == "failed"
    assert mv.session.get_or_create("recover")._record() == recovered._record()


def test_get_or_create_recovers_creator_that_wins_after_name_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    original = mv.session.get_or_create("shared", question="Why?", timezone="UTC")
    lookup = SessionStore.session_by_name
    first_lookup = True

    def stale_lookup(store: SessionStore, name: str) -> SessionRecord | None:
        nonlocal first_lookup
        if first_lookup:
            first_lookup = False
            return None  # The other creator's commit was not yet visible to the name probe.
        return lookup(store, name)

    monkeypatch.setattr(SessionStore, "session_by_name", stale_lookup)
    recovered = mv.session.get_or_create("shared", question="Why?", timezone="UTC")
    assert recovered._record() == original._record()
    assert [item.id for item in mv.session.recent().items] == [recovered.id]


@pytest.mark.parametrize("name", ["", " ", " review", "review "])
def test_invalid_name_does_not_create_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(mv.errors.AnalysisError):
        mv.session.get_or_create(name)
    assert not (tmp_path / ".marivo").exists()


def test_exact_id_cannot_be_confused_with_another_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    first = mv.session.get_or_create("first")
    named = mv.session.get_or_create(first.id)
    assert named.id != first.id
    assert mv.session.get_or_create(first.id).id == named.id
    assert mv.session.resume(first.id).id == first.id
    assert mv.session.inspect(first.id).id == first.id
    for read in (mv.session.resume, mv.session.inspect):
        with pytest.raises(SessionNotFoundError):
            read("first")


def test_metadata_reads_and_compute_leave_updated_at_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("stable", timezone="UTC")
    record = session._record()
    mv.session.resume(session.id)
    mv.session.inspect(session.id)
    mv.session.recent()
    session.show()
    session.values(columns={"answer": None}, rows=(((), {"answer": 7}),)).compute()
    assert session._record() == record
    with sqlite3.connect(session._runtime.store.db_path) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='runtime_state'"
            ).fetchall()
            == []
        )


def test_resume_recovers_pending_run_without_loading_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("recover", timezone="UTC")
    admit(session._runtime.store, session.id, "run_pending", "output_pending")
    monkeypatch.setattr(mv.Session, "_sources", lambda _: pytest.fail("recovery loaded semantics"))
    recovered = mv.session.resume(session.id)
    assert terminal_run(recovered, "run_pending").lifecycle == "failed"
    stable = recovered._record()
    assert mv.session.resume(session.id)._record() == stable


def test_recovery_respects_active_writer_and_run_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("guarded")
    other = mv.session.get_or_create("other")
    admit(session._runtime.store, session.id, "run_pending", "output_pending")
    mv.session.resume(other.id)
    assert terminal_run(session, "run_pending").lifecycle == "incomplete"
    with (
        session_writer_guard(
            session._runtime.store.layout.lock_path(session.id), session_ref=session.id
        ),
        pytest.raises(mv.errors.AnalysisError),
    ):
        mv.session.resume(session.id)
    mv.session.resume(session.id)
    assert terminal_run(session, "run_pending").lifecycle == "failed"


@pytest.mark.parametrize(
    "read",
    [lambda: mv.session.recent(limit=0), lambda: mv.session.recent(limit=101)],
)
def test_invalid_page_is_rejected_before_store_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read: Callable[[], object]
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(mv.errors.AnalysisError):
        read()
    assert not (tmp_path / ".marivo").exists()


def test_literal_frame_and_empty_bridge_do_not_load_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("literal", timezone="UTC")
    monkeypatch.setattr(
        mv.Session, "_sources", lambda _: pytest.fail("literal domain loaded semantics")
    )
    unit = mv.coordinates(by=(), keys=((),))
    empty = mv.coordinates(by=(), keys=())
    assert len(session.frame(unit).compute().to_pandas()) == 1
    assert len(session.frame(empty).compute().to_pandas()) == 0
    values = session.values(columns={"answer": None}, rows=(((), {"answer": 7}),))
    bridged = session.population_from(values, on=())
    assert len(session.frame(bridged).compute().to_pandas()) == 1
    assert terminal_runs(session) == ()


def test_members_consumes_resolved_entity_without_original_version_gate(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    import marivo.semantic as ms
    from marivo._definition.relations import PopulationProgram, SourceIdentities
    from marivo.semantic.expressions import _Resolver

    case = analysis_dsl_case_factory("j1")
    original = case.session.catalog
    model = case.root / "models/semantic/sales/models.py"
    model.write_text(
        model.read_text().replace(
            "customer = ms.entity(",
            "customer = ms.entity(ai_context=ms.ai_context(business_definition='updated identity description'), ",
            1,
        )
    )
    updated = ms.load(workspace_dir=case.root)
    assert updated.definition_fingerprint != original.definition_fingerprint
    entity = updated.get("entity:sales.customer")
    assert isinstance(entity, ms.Entity)
    population = case.session.members(entity)
    assert isinstance(population._definition, PopulationProgram)
    assert isinstance(population._definition.root, SourceIdentities)
    assert population._definition.root.entity == _Resolver(original._state).entity_ref(
        "sales.customer"
    )
    assert case.session.frame(population).compute().to_pandas()["customer"].tolist() == [
        "A",
        "B",
        "C",
        "D",
    ]
