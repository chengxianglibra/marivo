"""Certified occurrence discovery, exact coverage and agent-facing facts."""

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import marivo
import marivo.semantic as ms
from marivo._temporal import certify_temporal_set
from marivo.semantic.catalog import SemanticCatalog, _normalize_occurrence_filters
from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.reader import SemanticProject
from tests.semantic.preview_helpers import certified_project, default_scope


@pytest.fixture
def temporal_catalog(
    tmp_path: Path,
    semantic_project_factory: Callable[[dict[str, str]], SemanticProject],
) -> SemanticCatalog:
    catalog = certified_project(
        tmp_path=tmp_path, semantic_project_factory=semantic_project_factory
    )
    catalog.preview(ms.ref.temporal_set("sales.named_campaigns"), scope=default_scope())
    return catalog


def test_occurrence_page_discloses_original_bounds_category_and_continuation(
    temporal_catalog: SemanticCatalog,
) -> None:
    occurrences = temporal_catalog.temporal_sets.get("sales.named_campaigns")
    page = occurrences.occurrences(start=date(2026, 3, 2), end=date(2026, 3, 4), limit=1)
    first = page.items[0]
    assert (first.key, first.start, first.end, first.occurrence_category) == (
        "spring",
        date(2026, 3, 1),
        date(2026, 3, 4),
        "promotion",
    )
    rendered = page.render()
    for fact in (
        "temporal_set:sales.named_campaigns",
        "key",
        "start",
        "end",
        "category",
        "2026-03-01",
        "2026-03-04",
        "promotion",
        "UTC",
    ):
        assert fact in rendered
    assert "has_more: True" in rendered
    assert page.next_cursor is not None
    following = occurrences.occurrences(
        start=date(2026, 3, 2), end=date(2026, 3, 4), limit=1, cursor=page.next_cursor
    )
    assert [item.key for item in following.items] == ["incident"]
    assert following.next_cursor is None
    assert "has_more: False" in following.render()
    with pytest.raises(SemanticRuntimeError) as caught:
        occurrences.occurrences(category="promotion", cursor=page.next_cursor)
    assert caught.value.details["operation"] == "occurrences"


def test_covered_filters_preserve_empty_results_categories_and_exclusive_end(
    temporal_catalog: SemanticCatalog,
) -> None:
    occurrences = temporal_catalog.temporal_sets.get("sales.named_campaigns")
    assert len(occurrences.occurrences().items) == 2
    assert len(occurrences.occurrences(start=date(2026, 1, 1)).items) == 2
    assert len(occurrences.occurrences(end=date(2027, 1, 1)).items) == 2
    assert occurrences.occurrences(start=date(2027, 1, 1)).items == ()
    assert occurrences.occurrences(end=date(2026, 1, 1)).items == ()
    assert [item.key for item in occurrences.occurrences(category="promotion").items] == ["spring"]
    empty = occurrences.occurrences(start=date(2026, 3, 5), end=date(2027, 1, 1))
    assert empty.items == ()
    assert empty.next_cursor is None
    assert "has_more: False" in empty.render()
    assert occurrences.occurrences(end=date(2026, 3, 1)).items == ()


@pytest.mark.parametrize(
    ("start", "end"),
    (
        (date(2025, 12, 31), None),
        (None, date(2027, 1, 2)),
        (date(2030, 1, 1), date(2030, 2, 1)),
    ),
)
def test_uncovered_occurrence_filters_raise_structured_repairs(
    temporal_catalog: SemanticCatalog, start: date | None, end: date | None
) -> None:
    occurrences = temporal_catalog.temporal_sets.get("sales.named_campaigns")
    with pytest.raises(SemanticRuntimeError) as caught:
        occurrences.occurrences(start=start, end=end)
    error = caught.value
    assert "coverage" in error.message
    assert error.details["operation"] == "occurrences"
    assert error.repair is not None
    assert error.repair.help_target is not None
    assert error.repair.help_target.canonical_id == "temporal_set.occurrences"
    assert "coverage" in error.repair.action


def test_timestamp_filters_use_certified_midnights_across_dst() -> None:
    snapshot = certify_temporal_set(
        temporal_set_ref=ms.ref.temporal_set("sales.events"),
        boundary_timezone="America/New_York",
        coverage=(date(2026, 3, 8), date(2026, 3, 10)),
        rows=(
            {
                "id": "event",
                "start": datetime(2026, 3, 8, 6, tzinfo=timezone.utc),
                "end": datetime(2026, 3, 8, 7, tzinfo=timezone.utc),
            },
        ),
        occurrence_id="id",
        start="start",
        end="end",
    )
    start = datetime(2026, 3, 8, 5, tzinfo=timezone.utc)
    end = datetime(2026, 3, 10, 4, tzinfo=timezone.utc)
    assert "boundary_timezone=America/New_York" in snapshot.occurrence_scope("event").render()
    assert _normalize_occurrence_filters(
        snapshot, start=start.astimezone(timezone(timedelta(hours=8))), end=end
    ) == (start, end)
    for invalid_start, invalid_end in (
        (start - timedelta(microseconds=1), end),
        (start, end + timedelta(microseconds=1)),
    ):
        with pytest.raises(ValueError, match="coverage"):
            _normalize_occurrence_filters(snapshot, start=invalid_start, end=invalid_end)


def test_occurrence_scope_display_discloses_boundary_timezone(
    temporal_catalog: SemanticCatalog,
) -> None:
    occurrence = temporal_catalog.temporal_sets.get("sales.named_campaigns").occurrence("spring")
    assert "boundary_timezone=UTC" in occurrence.render()


def test_occurrence_help_explains_filters_coverage_and_pagination(
    capsys: pytest.CaptureFixture[str],
) -> None:
    marivo.help("analysis.catalog.temporal_sets.occurrences")
    text = capsys.readouterr().out
    for fact in ("date", "datetime", "coverage", "overlap", "clip", "category", "next_cursor"):
        assert fact in text
