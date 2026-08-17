"""Issue #99: converge the observed-watermark entry under ``session.events``.

``lifecycle.replay`` and ``events.match`` guidance tell callers to "prefer an
observed watermark". Historically that entry lived on the top-level ``Session``
as ``session.observe_watermark(event, through=...)``. Issue #99 moves it into
the ``session.events`` namespace as ``session.events.watermark(event,
through=...)``, keeping the catalog-fact resolution, backend watermark provider
call, and ``EventWatermarkReceipt`` (or ``None``) contract unchanged. These
tests lock the new governed entry and assert the old top-level name is gone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.errors import (
    InvalidCompletenessDeclarationError,
    SemanticKindMismatchError,
)
from marivo.analysis.event import EventWatermarkReceipt
from tests.shared_fixtures import (
    lifecycle_project_files,
    rendered_help,
    seed_lifecycle_backend,
)

_EVENT = "commerce.order_created"
_THROUGH = "2026-08-01T00:00:00Z"


def _watermark_session(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    watermark_events: frozenset[str] = frozenset(),
) -> mv.Session:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TZ", "UTC")
    semantic_project_factory(lifecycle_project_files())
    backend = seed_lifecycle_backend(watermark_events=watermark_events)
    return session_attach.get_or_create(
        name="watermark-entry",
        report_timezone="UTC",
        backends={"warehouse": lambda: backend},
    )


def test_events_watermark_returns_receipt_from_provider(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _watermark_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        watermark_events=frozenset({_EVENT}),
    )

    receipt = session.events.watermark(ms.ref.event(_EVENT), through=_THROUGH)

    assert isinstance(receipt, EventWatermarkReceipt)
    assert receipt.complete_through == "2026-08-01T00:00:00Z"
    assert receipt.authority == "warehouse_reconciliation"
    assert receipt.source_revision == "fixture-v1"


def test_events_watermark_accepts_catalog_entry(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _watermark_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        watermark_events=frozenset({_EVENT}),
    )
    entry = session.catalog.events.get(_EVENT)

    receipt = session.events.watermark(entry, through=_THROUGH)

    assert isinstance(receipt, EventWatermarkReceipt)


def test_events_watermark_returns_none_without_provider(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _watermark_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        watermark_events=frozenset(),
    )

    receipt = session.events.watermark(ms.ref.event(_EVENT), through=_THROUGH)

    assert receipt is None


def test_events_watermark_returns_none_for_uncovered_event(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _watermark_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        watermark_events=frozenset({"commerce.payment_captured"}),
    )

    receipt = session.events.watermark(ms.ref.event(_EVENT), through=_THROUGH)

    assert receipt is None


def test_events_watermark_rejects_non_event_input(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _watermark_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        watermark_events=frozenset({_EVENT}),
    )

    with pytest.raises(SemanticKindMismatchError):
        session.events.watermark(ms.ref.metric("sales.not_an_event"), through=_THROUGH)  # type: ignore[arg-type]


def test_events_watermark_rejects_empty_through(
    semantic_project_factory: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _watermark_session(
        semantic_project_factory,
        tmp_path,
        monkeypatch,
        watermark_events=frozenset({_EVENT}),
    )

    for empty in ("", "   "):
        with pytest.raises(InvalidCompletenessDeclarationError):
            session.events.watermark(ms.ref.event(_EVENT), through=empty)


def test_events_watermark_is_registered_as_public_session_events_read() -> None:
    descriptor = REGISTRY.by_help_target("events.watermark")

    assert descriptor.public_entrypoint == "session.events.watermark(...)"
    assert descriptor.callable_path == "marivo.analysis.session.core.SessionEvents.watermark"
    assert "events.watermark" in REGISTRY.capability_ids
    assert "watermark" in REGISTRY.public_member_names("SessionEvents")


def test_observe_watermark_is_removed_from_top_level_session() -> None:
    # Issue #99 is a breaking rename: the top-level Session entry is removed,
    # not aliased, so exactly one public call path remains.
    assert "observe_watermark" not in REGISTRY.capability_ids
    assert "observe_watermark" not in REGISTRY.public_member_names("Session")
    assert "watermark" not in REGISTRY.public_member_names("Session")


def test_events_watermark_help_resolves_and_points_to_entry() -> None:
    text = rendered_help("events.watermark", owner="analysis")

    assert "session.events.watermark" in text
    assert "EventWatermarkReceipt" in text


def test_replay_and_match_guidance_points_to_events_watermark() -> None:
    replay_guidance = " ".join(rendered_help("lifecycle.replay", owner="analysis").split())
    match_guidance = " ".join(rendered_help("events.match", owner="analysis").split())

    assert "session.events.watermark" in replay_guidance
    assert "session.events.watermark" in match_guidance
