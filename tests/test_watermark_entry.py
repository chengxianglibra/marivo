"""Issue #82: public observed-watermark entry for ``Session``.

Before this fix, ``lifecycle.replay`` and ``events.match`` guidance told callers
to "prefer an observed watermark" but exposed no SDK entry to obtain one;
``marivo.help("analysis.watermark")`` failed with ``MarivoHelpTargetError``.
These tests lock ``session.observe_watermark(event, through=...)`` as the
governed entry that resolves one Event's catalog facts, asks the backend
watermark provider, and returns an ``EventWatermarkReceipt`` (or ``None``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
import marivo.semantic as ms
from marivo.analysis._capabilities.registry import REGISTRY
from marivo.analysis.errors import SemanticKindMismatchError
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


def test_observe_watermark_returns_receipt_from_provider(
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

    receipt = session.observe_watermark(ms.ref.event(_EVENT), through=_THROUGH)

    assert isinstance(receipt, EventWatermarkReceipt)
    assert receipt.complete_through == "2026-08-01T00:00:00Z"
    assert receipt.authority == "warehouse_reconciliation"
    assert receipt.source_revision == "fixture-v1"


def test_observe_watermark_accepts_catalog_entry(
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

    receipt = session.observe_watermark(entry, through=_THROUGH)

    assert isinstance(receipt, EventWatermarkReceipt)


def test_observe_watermark_returns_none_without_provider(
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

    receipt = session.observe_watermark(ms.ref.event(_EVENT), through=_THROUGH)

    assert receipt is None


def test_observe_watermark_returns_none_for_uncovered_event(
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

    receipt = session.observe_watermark(ms.ref.event(_EVENT), through=_THROUGH)

    assert receipt is None


def test_observe_watermark_rejects_non_event_input(
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
        session.observe_watermark(ms.ref.metric("sales.not_an_event"), through=_THROUGH)  # type: ignore[arg-type]


def test_observe_watermark_is_registered_as_public_session_read() -> None:
    descriptor = REGISTRY.by_help_target("observe_watermark")

    assert descriptor.public_entrypoint == "session.observe_watermark(...)"
    assert descriptor.callable_path == "marivo.analysis.session.core.Session.observe_watermark"
    assert "observe_watermark" in REGISTRY.capability_ids
    assert "observe_watermark" in REGISTRY.public_member_names("Session")


def test_observe_watermark_help_resolves_and_points_to_entry() -> None:
    text = rendered_help("observe_watermark", owner="analysis")

    assert "session.observe_watermark" in text
    assert "EventWatermarkReceipt" in text


def test_replay_and_match_guidance_points_to_observe_watermark() -> None:
    replay_guidance = " ".join(rendered_help("lifecycle.replay", owner="analysis").split())
    match_guidance = " ".join(rendered_help("events.match", owner="analysis").split())

    assert "session.observe_watermark" in replay_guidance
    assert "session.observe_watermark" in match_guidance
