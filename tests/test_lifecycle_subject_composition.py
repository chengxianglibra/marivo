"""Lifecycle-backed ``session.select_subjects(..., mv.in_state(...))`` composition."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.analysis.errors import (
    EventCoverageUnknownError,
    ModelStateMismatchError,
    PatternStepMismatchError,
    SubjectSetMismatchError,
    WindowInvalidError,
)
from marivo.analysis.frames.subject import SubjectSet
from tests.shared_fixtures import (
    LIFECYCLE_METRIC_REF,
    LIFECYCLE_MODEL_REF,
    lifecycle_project_files,
    seed_lifecycle_backend,
)

_WINDOW = mv.time_scope(start="2026-07-01T00:00:00Z", end="2026-08-01T00:00:00Z")
_EVENT_PATHS = (
    "commerce.order_created",
    "commerce.payment_captured",
    "commerce.order_closed",
)


def _complete_through_window(*paths: str) -> tuple[mv.CompletenessDeclaration, ...]:
    return (
        mv.declared_complete_through(
            inputs=tuple(ms.ref.event(path) for path in (paths or _EVENT_PATHS)),
            through="2026-08-01T00:00:00Z",
            rationale="The composition fixture is reconciled through the window end.",
        ),
    )


def _composition_session(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    semantic_project_factory(lifecycle_project_files())
    backend = seed_lifecycle_backend()
    return session_attach.get_or_create(
        name=name,
        report_timezone="UTC",
        backends={"warehouse": lambda: backend},
    )


def _history(session: mv.Session, *, complete: bool = False) -> Any:
    return session.lifecycle.replay(
        ms.ref.state_model(LIFECYCLE_MODEL_REF),
        window=_WINDOW,
        seed=mv.from_inception(),
        completeness=_complete_through_window() if complete else (),
    )


def _state(name: str) -> Any:
    return ms.model_state(model=ms.ref.state_model(LIFECYCLE_MODEL_REF), name=name)


def _identities(subjects: SubjectSet) -> list[tuple[object, ...]]:
    return list(subjects.to_pandas()["subject_identity"])


@pytest.mark.parametrize(
    ("state", "as_of", "expected"),
    [
        # window.start is the inclusive lower observation bound.
        ("created", "2026-07-01T00:00:00Z", [("o1",)]),
        # Half-open membership: the created interval ends exactly at the paid entry.
        ("created", "2026-07-05T00:00:00Z", []),
        ("paid", "2026-07-05T00:00:00Z", [("o1",)]),
        # Two subjects occupy different states at the same instant.
        ("created", "2026-07-10T00:00:00Z", [("o2",)]),
        ("paid", "2026-07-10T00:00:00Z", [("o1",)]),
        # The exclusive replay end observes the final open interval per subject.
        ("closed", "2026-08-01T00:00:00Z", [("o1",), ("o2",)]),
    ],
)
def test_in_state_selects_proven_state_membership_at_an_instant(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    as_of: str,
    expected: list[tuple[object, ...]],
) -> None:
    session = _composition_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name=f"lifecycle-select-{state}-{as_of[8:10]}",
    )
    try:
        history = _history(session, complete=True)
        subjects = session.select_subjects(
            history,
            selection=mv.in_state(_state(state), as_of=as_of),
        )

        assert isinstance(subjects, SubjectSet)
        assert _identities(subjects) == expected
        assert subjects.meta.selected_count == len(expected)
        assert subjects.meta.excluded_coverage_censored_count == 0
        assert subjects.meta.coverage_status == "ready"
        assert subjects.meta.subject_entity_ref.path == "commerce.orders"
        assert subjects.meta.subject_identity == ("commerce.orders.order_id",)
        assert subjects.meta.selection.kind == "in_state"
        assert subjects.meta.source.artifact_ref == (history.meta.artifact_id or history.meta.ref)
    finally:
        session.close()
        session_attach._reset_process_state()


def test_in_state_excludes_coverage_censored_truth_and_stays_inspectable(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _composition_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-select-censored",
    )
    try:
        history = _history(session)
        assert history.meta.coverage_basis == "unknown"

        subjects = session.select_subjects(
            history,
            selection=mv.in_state(_state("closed"), as_of="2026-07-25T00:00:00Z"),
        )

        # Both final intervals are coverage_censored, so no truth is admissible.
        assert _identities(subjects) == []
        assert subjects.meta.excluded_coverage_censored_count == 2
        assert subjects.meta.coverage_status == "coverage_censored"
        # A censored SubjectSet remains inspectable...
        assert "coverage=coverage_censored" in repr(subjects)
        assert subjects.show() is None
        # ...but advertises no typed cohort consumer.
        assert subjects.contract().affordances == ()

        # ...but it is inadmissible to every typed consumer.
        with pytest.raises(EventCoverageUnknownError) as exc_info:
            session.lifecycle.replay(
                ms.ref.state_model(LIFECYCLE_MODEL_REF),
                window=_WINDOW,
                seed=mv.from_inception(),
                cohort=subjects,
            )
        assert exc_info.value.location == "session.lifecycle.replay.cohort"
    finally:
        session.close()
        session_attach._reset_process_state()


def test_in_state_selects_proven_intervals_before_a_censored_tail(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _composition_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-select-proven-prefix",
    )
    try:
        history = _history(session)
        subjects = session.select_subjects(
            history,
            selection=mv.in_state(_state("paid"), as_of="2026-07-10T00:00:00Z"),
        )

        # The paid interval closed on an observed transition, so it is proven
        # even though this replay ends with unknown coverage.
        assert _identities(subjects) == [("o1",)]
        assert subjects.meta.excluded_coverage_censored_count == 0
        assert subjects.meta.coverage_status == "ready"
    finally:
        session.close()
        session_attach._reset_process_state()


@pytest.mark.parametrize(
    ("case", "expected_error", "location"),
    [
        ("selection_type", ModelStateMismatchError, "session.select_subjects.selection"),
        ("foreign_model", ModelStateMismatchError, "session.select_subjects.selection.state"),
        ("unknown_state", ModelStateMismatchError, "session.select_subjects.selection.state"),
        ("as_of_naive", WindowInvalidError, "session.select_subjects.selection.as_of"),
        ("as_of_before", WindowInvalidError, "session.select_subjects.selection.as_of"),
        ("as_of_after", WindowInvalidError, "session.select_subjects.selection.as_of"),
        ("reducer_source", SubjectSetMismatchError, "session.select_subjects.artifact"),
    ],
)
def test_in_state_rejects_invalid_state_or_instant_inputs(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_error: type[Exception],
    location: str,
) -> None:
    session = _composition_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name=f"lifecycle-select-invalid-{case}",
    )
    try:
        history = _history(session, complete=True)
        artifact: Any = history
        selection: Any = mv.in_state(_state("paid"), as_of="2026-07-10T00:00:00Z")
        if case == "selection_type":
            selection = mv.dropped_before(
                step=mv.step(
                    participant=ms.participant_role(
                        event=ms.ref.event("commerce.order_closed"),
                        name="order",
                    ),
                    key="closed",
                )
            )
        elif case == "foreign_model":
            selection = mv.in_state(
                ms.model_state(
                    model=ms.ref.state_model("commerce.other_lifecycle"),
                    name="paid",
                ),
                as_of="2026-07-10T00:00:00Z",
            )
        elif case == "unknown_state":
            selection = mv.in_state(_state("refunded"), as_of="2026-07-10T00:00:00Z")
        elif case == "as_of_naive":
            selection = mv.in_state(_state("paid"), as_of="2026-07-10T00:00:00")
        elif case == "as_of_before":
            selection = mv.in_state(_state("paid"), as_of="2026-06-30T23:59:59Z")
        elif case == "as_of_after":
            selection = mv.in_state(_state("paid"), as_of="2026-08-01T00:00:01Z")
        elif case == "reducer_source":
            artifact = session.lifecycle.transitions(history)

        with pytest.raises(expected_error) as exc_info:
            session.select_subjects(artifact, selection=selection)

        error = exc_info.value
        assert error.location == location
        assert error.expected
        assert error.received
        assert error.repair is not None
        assert error.repair.action
        assert error.repair.help_target.canonical_id == "select_subjects"
    finally:
        session.close()
        session_attach._reset_process_state()


def test_in_state_rejects_a_state_handle_after_the_state_model_changed(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _composition_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-select-stale-model",
    )
    try:
        history = _history(session, complete=True)
        stale = history.meta.model_copy(update={"state_model_fingerprint": "sha256:stale"})
        drifted = type(history)(
            _df=history._dataframe_copy(),
            meta=stale,
            _auxiliary_frames=dict(history._auxiliary_frames),
        )

        with pytest.raises(ModelStateMismatchError) as exc_info:
            session.select_subjects(
                drifted,
                selection=mv.in_state(_state("paid"), as_of="2026-07-10T00:00:00Z"),
            )

        assert exc_info.value.received == "state_model_definition_changed"
        assert exc_info.value.location == "session.select_subjects.artifact"
    finally:
        session.close()
        session_attach._reset_process_state()


def test_bare_lifecycle_selection_receives_in_state_repair(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _composition_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-select-bare-repair",
    )
    try:
        history = _history(session, complete=True)

        with pytest.raises(ModelStateMismatchError) as exc_info:
            session.select_subjects(history, selection="paid")  # type: ignore[arg-type]

        error = exc_info.value
        assert error.location == "session.select_subjects.selection"
        assert error.expected == (
            "mv.in_state(state=<ModelStateHandle>, as_of=<timezone-aware instant>)"
        )
        assert error.received == "str"
        assert error.repair is not None
        assert error.repair.kind == "user_choice"
        assert "mv.in_state" in error.repair.action
        assert error.repair.help_target.canonical_id == "select_subjects"
    finally:
        session.close()
        session_attach._reset_process_state()


def test_in_state_journey_pairs_stay_closed_across_artifact_families(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _composition_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-select-closed-pairs",
    )
    try:
        journeys = session.events.match(
            pattern=mv.sequence(
                mv.step(
                    participant=ms.participant_role(
                        event=ms.ref.event("commerce.order_created"),
                        name="order",
                    ),
                    key="created",
                ),
                mv.step(
                    participant=ms.participant_role(
                        event=ms.ref.event("commerce.payment_captured"),
                        name="order",
                    ),
                    key="paid",
                ),
            ),
            cohort_window=mv.time_scope(
                start="2026-07-01T00:00:00Z",
                end="2026-08-01T00:00:00Z",
            ),
            completion_through="2026-08-01T00:00:00Z",
            matching=mv.first_per_subject(),
            completeness=_complete_through_window(
                "commerce.order_created",
                "commerce.payment_captured",
            ),
        )

        # An in_state selection is not valid against a journey source.
        with pytest.raises(PatternStepMismatchError) as journey_error:
            session.select_subjects(
                journeys,
                selection=mv.in_state(_state("paid"), as_of="2026-07-10T00:00:00Z"),
            )
        assert journey_error.value.location == "session.select_subjects.selection"
    finally:
        session.close()
        session_attach._reset_process_state()


def test_in_state_subject_set_cold_recovers_with_identical_rows_and_selection(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _composition_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-select-cold",
    )
    try:
        history = _history(session, complete=True)
        warm = session.select_subjects(
            history,
            selection=mv.in_state(_state("paid"), as_of="2026-07-10T00:00:00Z"),
        )
        artifact_id = warm.meta.artifact_id or warm.meta.ref
        warm_rows = warm.to_pandas()
        warm_fingerprint = warm.meta.selection_fingerprint
    finally:
        session.close()
        session_attach._reset_process_state()

    monkeypatch.chdir(tmp_path)
    backend = seed_lifecycle_backend()
    reopened = session_attach.get_or_create(
        name="lifecycle-select-cold",
        report_timezone="UTC",
        backends={"warehouse": lambda: backend},
    )
    try:
        cold = reopened.get_frame(artifact_id)

        assert isinstance(cold, SubjectSet)
        assert cold.to_pandas().equals(warm_rows)
        assert cold.meta.selection.kind == "in_state"
        assert cold.meta.selection.state.name == "paid"
        assert cold.meta.selection.state.model.path == LIFECYCLE_MODEL_REF
        assert cold.meta.selection_fingerprint == warm_fingerprint
        assert cold.meta.coverage_status == "ready"
    finally:
        reopened.close()
        session_attach._reset_process_state()


def test_ready_lifecycle_subject_set_is_reusable_by_typed_consumers(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _composition_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        name="lifecycle-select-reuse",
    )
    try:
        history = _history(session, complete=True)
        subjects = session.select_subjects(
            history,
            selection=mv.in_state(_state("paid"), as_of="2026-07-10T00:00:00Z"),
        )
        assert subjects.meta.coverage_status == "ready"
        assert _identities(subjects) == [("o1",)]
        assert {item.capability_id for item in subjects.contract().affordances} == {
            "observe",
            "events.match",
            "lifecycle.replay",
        }
        binding = subjects.meta.cohort_binding()

        metrics = session.observe(
            ms.ref.metric(LIFECYCLE_METRIC_REF),
            time_scope=mv.time_scope(start="2026-06-01", end="2026-08-01"),
            grain=mv.grain("month"),
            cohort=subjects,
        )
        assert metrics.meta.cohort == binding
        assert metrics.to_pandas()["order_count"].sum() == 1

        journeys = session.events.match(
            pattern=mv.sequence(
                mv.step(
                    participant=ms.participant_role(
                        event=ms.ref.event("commerce.order_closed"),
                        name="order",
                    ),
                    key="closed",
                ),
            ),
            cohort_window=mv.time_scope(
                start="2026-07-01T00:00:00Z",
                end="2026-08-01T00:00:00Z",
            ),
            completion_through="2026-08-01T00:00:00Z",
            matching=mv.first_per_subject(),
            completeness=_complete_through_window("commerce.order_closed"),
            cohort=subjects,
        )
        assert journeys.meta.cohort == binding
        assert set(journeys.to_pandas()["subject_identity"]) == {("o1",)}

        scoped = session.lifecycle.replay(
            ms.ref.state_model(LIFECYCLE_MODEL_REF),
            window=_WINDOW,
            seed=mv.from_inception(),
            completeness=_complete_through_window(),
            cohort=subjects,
        )
        assert scoped.meta.cohort == binding
        assert scoped.meta.population_count == 1
        assert set(scoped.to_pandas()["subject_identity"]) == {("o1",)}
    finally:
        session.close()
        session_attach._reset_process_state()
