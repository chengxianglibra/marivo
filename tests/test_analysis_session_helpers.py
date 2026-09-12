"""Facade-based session lifecycle tests.

All tests use ``import marivo.analysis as mv`` only; no direct imports
from ``attach``, ``active``, or ``persistence``.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from inspect import signature
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_type_hints

import pytest

import marivo.analysis as mv

# ---------------------------------------------------------------------------
# __all__ and surface checks
# ---------------------------------------------------------------------------


def test_session_all_exports_exactly_six_names() -> None:
    assert mv.session.__all__ == [
        "abandon_run",
        "current",
        "get_or_create",
        "inspect",
        "recent",
        "resume",
    ]


def test_session_acquisition_has_concrete_return_annotations() -> None:
    assert get_type_hints(mv.session.abandon_run)["return"] is type(None)
    assert get_type_hints(mv.session.current)["return"] == mv.Session | None
    assert get_type_hints(mv.session.get_or_create)["return"] is mv.Session
    assert get_type_hints(mv.session.resume)["return"] is mv.Session
    assert signature(mv.session.current).return_annotation != Any
    assert signature(mv.session.get_or_create).return_annotation != Any
    assert signature(mv.session.resume).return_annotation != Any
    assert tuple(signature(mv.session.abandon_run).parameters) == ("session_id", "run_id")
    assert all(
        parameter.kind.name == "KEYWORD_ONLY"
        for parameter in signature(mv.session.abandon_run).parameters.values()
    )
    assert tuple(signature(mv.session.resume).parameters) == (
        "identity",
        "by",
    )


# ---------------------------------------------------------------------------
# current()
# ---------------------------------------------------------------------------


def test_current_returns_none_when_no_process_or_store_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    assert mv.session.current() is None


# ---------------------------------------------------------------------------
# get_or_create()
# ---------------------------------------------------------------------------


def test_get_or_create_creates_and_marks_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    s = mv.session.get_or_create(name="s")
    assert s.name == "s"
    current = mv.session.current()
    assert current is not None
    assert current.id == s.id


def test_get_or_create_resumes_same_id_and_marks_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    s1 = mv.session.get_or_create(name="s")
    s2 = mv.session.get_or_create(name="s")
    assert s1.id == s2.id
    assert mv.session.current() is not None
    assert mv.session.current().id == s1.id


def test_explicit_question_updates_existing_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    original = mv.session.get_or_create(name="s", question="why?")
    before = mv.session.inspect("s").summary
    store = original._runtime.store

    updated = mv.session.get_or_create(name="s", question="different?")

    current = mv.session.current()
    inspection = mv.session.inspect("s").summary
    record = store.session(updated.id)
    assert record is not None
    assert updated.id == original.id
    assert updated.created_at == original.created_at
    assert updated.question == "different?"
    assert current is not None
    assert current.id == original.id
    assert current.question == "different?"
    assert inspection.question == "different?"
    assert inspection.updated_at > before.updated_at
    assert record.question == "different?"


def test_get_or_create_same_or_omitted_question_preserves_current_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    mv.session.get_or_create(name="s", question="why?")

    matching = mv.session.get_or_create(name="s", question="why?")
    omitted = mv.session.get_or_create(name="s")

    assert matching.id == omitted.id
    assert matching.question == "why?"
    assert omitted.question == "why?"
    assert mv.session.inspect("s").summary.question == "why?"


def test_get_or_create_binds_question_to_existing_unbound_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    existing = mv.session.get_or_create(name="s")

    updated = mv.session.get_or_create(name="s", question="new question")

    assert updated.id == existing.id
    assert updated.question == "new question"
    assert mv.session.inspect("s").summary.question == "new question"


def test_question_update_activates_historical_session_and_syncs_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    historical = mv.session.get_or_create(name="historical", question="original")
    store = historical._runtime.store
    mv.session.get_or_create(name="active")

    updated = mv.session.get_or_create(name="historical", question="replacement")

    current = mv.session.current()
    record = store.session(updated.id)
    assert record is not None
    assert current is not None
    assert updated.id == historical.id
    assert current.id == historical.id
    assert current.question == "replacement"
    assert mv.session.inspect("historical").summary.question == "replacement"
    assert record.question == "replacement"


def test_get_or_create_explicit_empty_question_clears_current_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    original = mv.session.get_or_create(name="s", question="why?")

    updated = mv.session.get_or_create(name="s", question="")

    store = original._runtime.store
    assert updated.id == original.id
    assert updated.question == ""
    assert mv.session.inspect("s").summary.question == ""
    assert store.session(original.id).question == ""


def test_question_and_current_activation_roll_back_together_on_store_failure(
    tmp_path, monkeypatch
) -> None:
    import sqlite3

    monkeypatch.chdir(tmp_path)
    original = mv.session.get_or_create("historical", question="original")
    active = mv.session.get_or_create("active")
    store = original._runtime.store
    before = store.session(original.id)
    with store._write() as connection:
        connection.execute(
            "CREATE TRIGGER fail_question BEFORE UPDATE OF question ON sessions BEGIN SELECT RAISE(ABORT, 'activation failed'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="activation failed"):
        mv.session.get_or_create("historical", question="replacement")
    assert store.session(original.id) == before
    assert mv.session.current().id == active.id
    assert mv.session.inspect("historical").summary.question == "original"


def test_concurrent_question_update_rejects_busy_writer_without_partial_activation(
    tmp_path, monkeypatch
) -> None:
    from marivo.analysis.materialization.errors import SessionBusyError
    from marivo.analysis.materialization.store import SessionStore

    monkeypatch.chdir(tmp_path)
    original = mv.session.get_or_create("s", question="original")
    entered = threading.Event()
    release = threading.Event()
    activate = SessionStore.activate

    def paused(store, session_ref, *, question=None):
        if question == "question-a":
            entered.set()
            assert release.wait(10)
        return activate(store, session_ref, question=question)

    monkeypatch.setattr(SessionStore, "activate", paused)
    with ThreadPoolExecutor(max_workers=1) as executor:
        winner = executor.submit(mv.session.get_or_create, "s", "question-a")
        try:
            assert entered.wait(10)
            with pytest.raises(SessionBusyError):
                mv.session.get_or_create("s", question="question-b")
            assert original.question == "original"
        finally:
            release.set()
        assert winner.result().id == original.id
    assert mv.session.inspect("s").summary.question == "question-a"
    assert mv.session.current().question == "question-a"
    assert mv.session.get_or_create("s", question="question-b").question == "question-b"


def test_resume_by_id_restores_session_and_marks_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    historical = mv.session.get_or_create(name="historical", question="why?")
    mv.session.get_or_create(name="active")

    resumed = mv.session.resume(historical.id)

    assert resumed.id == historical.id
    assert resumed.name == "historical"
    assert resumed.question == "why?"
    current = mv.session.current()
    assert current is not None
    assert current.id == historical.id


def test_resume_by_name_restores_without_creating_or_changing_persisted_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    historical = mv.session.get_or_create(
        name="historical",
        question="why?",
        report_timezone="UTC",
    )
    mv.session.get_or_create(name="active")

    resumed = mv.session.resume("historical")

    assert resumed.id == historical.id
    assert resumed.name == "historical"
    assert resumed.question == "why?"
    assert resumed.report_tz_name == "UTC"
    assert {item.name for item in mv.session.recent(limit=10).items} == {
        "active",
        "historical",
    }
    current = mv.session.current()
    assert current is not None
    assert current.id == historical.id


def test_resume_missing_identity_raises_typed_error_with_real_name_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    known = mv.session.get_or_create(name="known")

    with pytest.raises(mv.errors.SessionNotFoundError) as exc_info:
        mv.session.resume("sess_missing")

    error = exc_info.value
    assert error.expected == "an existing project session name or id from mv.session.recent().items"
    assert error.received == "sess_missing"
    assert error.location == "mv.session.resume(identity)"
    assert error.repair is not None
    assert error.repair.kind == "inspect"
    assert error.repair.help_target.canonical_id == "session.recent"
    assert error.repair.candidates == (known.name,)
    current = mv.session.current()
    assert current is not None
    assert current.id == known.id


def test_resume_rejects_identity_that_matches_different_id_and_name_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    store_module = import_module("marivo.analysis.materialization.admission")
    shared_identity = "session_" + "a" * 24
    generated_ids = iter((shared_identity, "session_" + "b" * 24))
    monkeypatch.setattr(
        store_module,
        "uuid4",
        lambda: SimpleNamespace(hex=next(generated_ids).removeprefix("session_")),
    )
    id_match = mv.session.get_or_create(name="id-owner")
    name_match = mv.session.get_or_create(name=shared_identity)

    with pytest.raises(mv.errors.SessionIdentityAmbiguousError) as exc_info:
        mv.session.resume(shared_identity)

    error = exc_info.value
    assert error.expected == "one session matched by exact name or id"
    assert error.received == shared_identity
    assert error.location == "mv.session.resume(identity)"
    assert error.repair is not None
    assert error.repair.kind == "user_choice"
    assert error.repair.help_target.canonical_id == "session.resume"
    assert error.repair.candidates == (
        f'by="id" -> name={id_match.name!r}',
        f'by="name" -> id={name_match.id!r}',
    )

    resumed_by_id = mv.session.resume(shared_identity, by="id")
    resumed_by_name = mv.session.resume(shared_identity, by="name")

    assert resumed_by_id.id == id_match.id
    assert resumed_by_name.id == name_match.id
    current = mv.session.current()
    assert current is not None
    assert current.id == name_match.id


def test_resume_explicit_selector_breaks_two_identity_collision_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    store_module = import_module("marivo.analysis.materialization.admission")
    left_identity = "session_" + "a" * 24
    right_identity = "session_" + "b" * 24
    generated_ids = iter((left_identity, right_identity))
    monkeypatch.setattr(
        store_module,
        "uuid4",
        lambda: SimpleNamespace(hex=next(generated_ids).removeprefix("session_")),
    )
    left = mv.session.get_or_create(name=right_identity)
    right = mv.session.get_or_create(name=left_identity)

    for identity in (left_identity, right_identity):
        with pytest.raises(mv.errors.SessionIdentityAmbiguousError):
            mv.session.resume(identity)

    assert mv.session.resume(left_identity, by="id").id == left.id
    assert mv.session.resume(left_identity, by="name").id == right.id
    assert mv.session.resume(right_identity, by="id").id == right.id
    assert mv.session.resume(right_identity, by="name").id == left.id


def test_resume_accepts_identity_matching_same_row_by_id_and_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    store_module = import_module("marivo.analysis.materialization.admission")
    monkeypatch.setattr(store_module, "uuid4", lambda: SimpleNamespace(hex="same"))
    session = mv.session.get_or_create(name="session_same")

    resumed = mv.session.resume("session_same")

    assert resumed.id == session.id == "session_same"


@pytest.mark.parametrize(
    ("by", "expected", "candidate_attribute"),
    (
        ("name", "an existing project session name", "name"),
        ("id", "an existing project session id", "id"),
    ),
)
def test_resume_explicit_selector_missing_identity_uses_matching_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    by: str,
    expected: str,
    candidate_attribute: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    known = mv.session.get_or_create(name="known")
    resume_call = {"by": by}

    with pytest.raises(mv.errors.SessionNotFoundError) as exc_info:
        mv.session.resume("missing", **resume_call)

    error = exc_info.value
    assert error.expected == f"{expected} from mv.session.recent().items"
    assert error.repair is not None
    assert error.repair.candidates == (getattr(known, candidate_attribute),)


def test_resume_rejects_unknown_identity_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    invalid_selector_call = {"by": "prefix"}

    with pytest.raises(mv.errors.SessionStateError) as exc_info:
        mv.session.resume("s", **invalid_selector_call)

    error = exc_info.value
    assert error.expected == 'by="name", by="id", or omitted by'
    assert error.received == "prefix"
    assert error.location == "mv.session.resume(by=...)"
    assert error.repair is not None
    assert error.repair.candidates == ("name", "id")


def test_resume_rejects_removed_session_id_keyword(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    session = mv.session.get_or_create(name="s")

    removed_keyword_call = {"session_id": session.id}
    with pytest.raises(TypeError):
        mv.session.resume(**removed_keyword_call)


def test_resume_rejects_backends_and_backend_factory_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    session = mv.session.get_or_create(name="s")

    with pytest.raises(TypeError):
        mv.session.resume(
            session.id,
            backends={"w": lambda: None},
            backend_factory=lambda _name: None,
        )


def test_session_has_no_default_calendar_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    session = mv.session.get_or_create(name="s")
    assert not hasattr(session, "default_calendar")
    with pytest.raises(TypeError):
        mv.session.get_or_create(name="s", default_calendar="fiscal")


def test_backends_and_backend_factory_both_raises_session_state_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    with pytest.raises(TypeError):
        mv.session.get_or_create(
            name="s",
            backends={"w": lambda: None},
            backend_factory=lambda name: None,
        )


# ---------------------------------------------------------------------------
# recent() and inspect()
# ---------------------------------------------------------------------------


def test_recent_is_bounded_newest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    first = mv.session.get_or_create(name="first")
    second = mv.session.get_or_create(name="second")

    page = mv.session.recent(limit=1)
    assert [item.name for item in page.items] == ["second"]
    assert page.has_more is True
    assert page.next_cursor is not None
    next_page = mv.session.recent(limit=1, cursor=page.next_cursor)
    assert [item.name for item in next_page.items] == ["first"]
    assert {item.id for item in (*page.items, *next_page.items)} == {first.id, second.id}


def test_inspect_returns_bounded_snapshot_without_touching_session(tmp_path, monkeypatch) -> None:
    import marivo.semantic.catalog as catalog_module
    from tests.lazy_runtime_read_fixtures import input_value

    monkeypatch.chdir(tmp_path)
    historical = mv.session.get_or_create("historical", question="Why did revenue drop?")
    store = historical._runtime.store
    store.admit(historical.id, "pending-key", input_value(), run_ref="run_1")
    active = mv.session.get_or_create("active")
    before = store.session(historical.id)
    monkeypatch.setattr(
        catalog_module, "load", lambda **kwargs: pytest.fail("inspect loaded semantics")
    )
    snapshot = mv.session.inspect("historical", run_limit=1)
    assert store.session(historical.id) == before
    assert mv.session.current().id == active.id
    assert snapshot.summary.question == "Why did revenue drop?"
    assert snapshot.runs.items[0].run_id == "run_1"


def test_inspect_missing_session_raises_typed_error_with_real_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    mv.session.get_or_create(name="known")

    with pytest.raises(mv.errors.SessionNotFoundError) as exc_info:
        mv.session.inspect("missing")

    repair = exc_info.value.repair
    assert repair is not None
    assert repair.kind == "inspect"
    assert repair.help_target.canonical_id == "session.recent"
    assert repair.candidates == ("known",)


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: mv.session.recent(limit=0), "session.recent.limit"),
        (lambda: mv.session.inspect("x", run_limit=0), "session.inspect.run_limit"),
        (lambda: mv.session.inspect("x", run_limit=101), "session.inspect.run_limit"),
    ],
)
def test_history_limits_fail_before_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    call,
    message: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    with pytest.raises(mv.errors.SessionStateError, match=message):
        call()


# ---------------------------------------------------------------------------
# delete()
# ---------------------------------------------------------------------------
