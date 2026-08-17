"""Issue #84: exact Event/StateModel occurrence-bound inspection."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo._compat import UTC
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.errors import EventIdentityError, SemanticKindMismatchError
from tests.shared_fixtures import (
    LIFECYCLE_BASE_EVENTS,
    LIFECYCLE_MODEL_REF,
    lifecycle_project_files,
    rendered_help,
    seed_lifecycle_backend,
)

_CREATED = "commerce.order_created"


def _bounds_session(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: tuple[tuple[str, ...], ...] = LIFECYCLE_BASE_EVENTS,
    project_files: dict[str, str] | None = None,
    backend_sql: tuple[str, ...] = (),
) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    semantic_project_factory(lifecycle_project_files() if project_files is None else project_files)
    backend = seed_lifecycle_backend(events=events)
    for statement in backend_sql:
        backend.raw_sql(statement)
    return session_attach.get_or_create(
        name="event-occurrence-bounds-entry",
        report_timezone="UTC",
        backends={"warehouse": lambda: backend},
    )


def test_occurrence_bounds_queries_one_exact_event(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = (
        *LIFECYCLE_BASE_EVENTS,
        ("future", "o2", "unrelated", "2099-01-01 00:00:00"),
    )
    session = _bounds_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        events=events,
    )

    bounds = session.events.occurrence_bounds(ms.ref.event(_CREATED))

    assert isinstance(bounds, mv.EventOccurrenceBounds)
    assert bounds.target_ref == ms.ref.event(_CREATED)
    assert bounds.event_refs == (ms.ref.event(_CREATED),)
    assert bounds.earliest_occurrence_at == datetime(2026, 6, 15, tzinfo=UTC)
    assert bounds.latest_occurrence_at == datetime(2026, 7, 10, tzinfo=UTC)
    assert bounds.observed_at.tzinfo is not None


def test_occurrence_bounds_infers_state_model_events(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _bounds_session(semantic_project_factory, tmp_path, monkeypatch)
    model = session.catalog.state_models.get(LIFECYCLE_MODEL_REF)

    bounds = session.events.occurrence_bounds(model)

    assert bounds.target_ref == model.ref
    assert bounds.event_refs == (
        ms.ref.event("commerce.order_created"),
        ms.ref.event("commerce.payment_captured"),
        ms.ref.event("commerce.order_closed"),
    )
    assert bounds.earliest_occurrence_at == datetime(2026, 6, 15, tzinfo=UTC)
    assert bounds.latest_occurrence_at == datetime(2026, 7, 25, tzinfo=UTC)
    assert "events=3" in bounds.render()
    assert "completeness" not in bounds.render().lower()


def test_occurrence_bounds_reports_empty_event_without_none_fallback(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _bounds_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        events=(("other", "o1", "unrelated", "2026-07-01 00:00:00"),),
    )

    bounds = session.events.occurrence_bounds(ms.ref.event(_CREATED))

    assert bounds.earliest_occurrence_at is None
    assert bounds.latest_occurrence_at is None
    assert "empty: no matching Event occurrences" in bounds.render()


def test_occurrence_bounds_rejects_duplicate_event_identity(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _bounds_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        backend_sql=(
            "INSERT INTO event_log VALUES ('e1', 'o1', 'created', TIMESTAMP '2099-01-01 00:00:00')",
        ),
    )

    with pytest.raises(EventIdentityError, match="declared identity is not unique") as raised:
        session.events.occurrence_bounds(ms.ref.event(_CREATED))

    assert raised.value.location.endswith(".identity")
    assert raised.value.repair is not None
    assert raised.value.repair.help_target.canonical_id == "events.occurrence_bounds"


@pytest.mark.parametrize("identity_sql", ("NULL", "''"))
def test_occurrence_bounds_rejects_empty_event_identity(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity_sql: str,
) -> None:
    session = _bounds_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        backend_sql=(
            "INSERT INTO event_log VALUES "
            f"({identity_sql}, 'o1', 'created', TIMESTAMP '2026-07-11 00:00:00')",
        ),
    )

    with pytest.raises(EventIdentityError, match="empty identity component") as raised:
        session.events.occurrence_bounds(ms.ref.event(_CREATED))

    assert raised.value.location.endswith(".identity")


def test_occurrence_bounds_rejects_null_occurred_at(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _bounds_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        backend_sql=("INSERT INTO event_log VALUES ('null-created', 'o1', 'created', NULL)",),
    )

    with pytest.raises(EventIdentityError, match="invalid occurred_at value") as raised:
        session.events.occurrence_bounds(ms.ref.event(_CREATED))

    assert raised.value.location.endswith(".occurred_at")
    assert raised.value.repair is not None


def test_occurrence_bounds_returns_typed_empty_for_triggerless_state_model(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_files = lifecycle_project_files()
    project_files["commerce/objects.py"] = project_files["commerce/objects.py"].replace(
        """    transitions=(
        ms.inception(on=order_created),
        ms.transition(from_state=created, on=payment_captured, to_state=paid),
        ms.transition(from_state=created, on=order_closed, to_state=closed),
        ms.transition(from_state=paid, on=order_closed, to_state=closed),
    ),
""",
        "    transitions=(),\n",
    )
    session = _bounds_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        project_files=project_files,
    )
    model = session.catalog.state_models.get(LIFECYCLE_MODEL_REF)

    bounds = session.events.occurrence_bounds(model)

    assert bounds.target_ref == model.ref
    assert bounds.event_refs == ()
    assert bounds.earliest_occurrence_at is None
    assert bounds.latest_occurrence_at is None
    assert "empty: StateModel declares no Event triggers" in bounds.render()


def test_occurrence_bounds_rejects_non_event_or_model_target(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _bounds_session(semantic_project_factory, tmp_path, monkeypatch)

    with pytest.raises(SemanticKindMismatchError) as raised:
        session.events.occurrence_bounds(  # type: ignore[arg-type]
            ms.ref.datasource("warehouse")
        )

    assert raised.value.location == "session.events.occurrence_bounds.event_or_model"
    assert raised.value.repair is not None
    assert raised.value.repair.help_target.canonical_id == "events.occurrence_bounds"


def test_occurrence_bounds_is_registered_as_public_events_read() -> None:
    descriptor = REGISTRY.by_help_target("events.occurrence_bounds")

    assert descriptor.public_entrypoint == "session.events.occurrence_bounds(...)"
    assert descriptor.callable_path == (
        "marivo.analysis.session.core.SessionEvents.occurrence_bounds"
    )
    assert "events.occurrence_bounds" in REGISTRY.capability_ids
    assert "occurrence_bounds" in REGISTRY.public_member_names("SessionEvents")
    assert "occurrence_bounds" not in REGISTRY.public_member_names("Session")


def test_occurrence_bounds_help_resolves_method_result_and_boundary() -> None:
    method_help = rendered_help("events.occurrence_bounds", owner="analysis")
    result_help = rendered_help(mv.EventOccurrenceBounds, owner="analysis")

    assert "session.events.occurrence_bounds" in method_help
    assert "EventOccurrenceBounds" in method_help
    assert "StateModel" in method_help
    assert "does not prove completeness" in " ".join(method_help.split())
    assert "latest_occurrence_at" in method_help
    assert result_help.startswith("EventOccurrenceBounds\n")

    replay_help = rendered_help("lifecycle.replay", owner="analysis")
    assert "session.events.occurrence_bounds(model)" in replay_help


def test_source_extent_provider_models_are_not_public() -> None:
    assert "SourceExtentRequest" not in mv.__all__
    assert "SourceExtentReceipt" not in mv.__all__
