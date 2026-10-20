"""Strict Session creation, exact recovery and read-only discovery."""

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from inspect import Parameter, signature
from pathlib import Path

import pytest

import marivo.analysis as mv
from marivo.analysis.errors import SessionAlreadyExistsError, SessionNotFoundError
from marivo.analysis.materialization.terminal_store import admit
from marivo.analysis.materialization.writer_guard import session_writer_guard
from tests.shared_fixtures import DslCaseFactory


def test_lifecycle_surface_has_one_explicit_entrypoint() -> None:
    assert set(mv.session.__all__) == {"create", "resume", "recent", "inspect", "reconcile_run"}
    assert tuple(signature(mv.session.create).parameters) == (
        "name",
        "question",
        "timezone",
        "domains",
    )
    assert signature(mv.session.create).parameters["question"].kind is Parameter.KEYWORD_ONLY
    assert tuple(signature(mv.session.resume).parameters) == ("session_id",)
    for retired in ("get_or_create", "current", "abandon_run"):
        assert not hasattr(mv.session, retired)


def test_create_is_strict_and_preserves_frozen_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    original = mv.session.create(
        "review", question="Why?", timezone="UTC", domains=["sales", "sales"]
    )
    before = original._record()
    with pytest.raises(SessionAlreadyExistsError) as caught:
        mv.session.create(
            "review", question="Replacement", timezone="Asia/Shanghai", domains="finance"
        )
    assert caught.value._context["session_id"] == original.id
    assert caught.value.repair is not None
    assert caught.value.repair.candidates == (original.id,)
    assert original.id in str(caught.value)
    assert original._record() == before
    assert original.domains == ("sales",)
    assert mv.session.resume(original.id)._record() == before
    assert mv.session.inspect(original.id).summary.question == "Why?"
    for field in ("id", "name", "question", "timezone", "domains", "created_at", "updated_at"):
        with pytest.raises(AttributeError):
            setattr(original, field, "changed")


@pytest.mark.parametrize("name", ["", " ", " review", "review "])
def test_invalid_name_does_not_create_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(mv.errors.AnalysisError):
        mv.session.create(name)
    assert not (tmp_path / ".marivo").exists()


def test_competing_create_has_one_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    # Initialize the Store so this test isolates the unique-name transaction.
    mv.session.create("initializer", timezone="UTC")

    def create() -> str:
        try:
            return mv.session.create("shared", timezone="UTC").id
        except SessionAlreadyExistsError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create(), range(2)))
    assert results.count("duplicate") == 1
    assert len([item for item in mv.session.recent().items if item.name == "shared"]) == 1


def test_exact_id_cannot_be_confused_with_another_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    first = mv.session.create("first")
    mv.session.create(first.id)
    assert mv.session.resume(first.id).id == first.id
    assert mv.session.inspect(first.id).summary.id == first.id
    for read in (mv.session.resume, mv.session.inspect):
        with pytest.raises(SessionNotFoundError):
            read("first")


def test_metadata_reads_and_compute_leave_updated_at_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("stable", timezone="UTC")
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
    session = mv.session.create("recover", timezone="UTC")
    admit(session._runtime.store, session.id, "run_pending", "output_pending")
    monkeypatch.setattr(mv.Session, "_sources", lambda _: pytest.fail("recovery loaded semantics"))
    recovered = mv.session.resume(session.id)
    assert recovered.get_run("run_pending").lifecycle == "failed"
    stable = recovered._record()
    assert mv.session.resume(session.id)._record() == stable


def test_recovery_respects_active_writer_and_run_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("guarded")
    other = mv.session.create("other")
    admit(session._runtime.store, session.id, "run_pending", "output_pending")
    with pytest.raises(mv.errors.AnalysisError):
        mv.session.reconcile_run(session_id=other.id, run_id="run_pending")
    with (
        session_writer_guard(
            session._runtime.store.layout.lock_path(session.id), session_ref=session.id
        ),
        pytest.raises(mv.errors.AnalysisError),
    ):
        mv.session.resume(session.id)
    mv.session.reconcile_run(session_id=session.id, run_id="run_pending")
    assert session.get_run("run_pending").lifecycle == "failed"


@pytest.mark.parametrize(
    "read",
    [lambda: mv.session.recent(limit=0), lambda: mv.session.inspect("missing", run_limit=101)],
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
    session = mv.session.create("literal", timezone="UTC")
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
    assert session.runs().items == ()


def test_members_consumes_resolved_entity_without_original_version_gate(
    analysis_dsl_case_factory: DslCaseFactory,
) -> None:
    from dataclasses import replace

    from marivo._definition.relations import PopulationProgram, SourceIdentities
    from marivo._definition.types import ExactRef

    case = analysis_dsl_case_factory("j1")
    entity = case.session.catalog.entities.get("sales.customer")
    older = replace(entity, ref=ExactRef(entity.ref.ref, "previous-definition"))
    population = case.session.members(older)
    assert isinstance(population._definition, PopulationProgram)
    assert isinstance(population._definition.root, SourceIdentities)
    assert population._definition.root.entity == older.ref
    assert case.session.frame(population).compute().to_pandas()[
        ("coordinate", "axis.0")
    ].tolist() == ["A", "B", "C", "D"]
    with pytest.raises(mv.errors.AnalysisError, match="complete primary key"):
        case.session.members(replace(entity, primary_key=("wrong",)))
