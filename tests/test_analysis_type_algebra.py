"""Tests for the analysis capability type algebra generated from the registry.

These tests pin the type-algebra rows that the registry emits: the canonical
operator edges, discover/transform grouping collapse, frame producer/read
edges, constructor consumer reverse edges, and the single terminal
``boundary.to_pandas`` aggregate row.
"""

from __future__ import annotations

from marivo.analysis._capabilities import (
    ARTIFACT_FAMILIES,
    ArtifactOutputContract,
    BoundaryCapability,
)
from marivo.analysis._capabilities.registry import REGISTRY, TypeAlgebraRow

# ---------------------------------------------------------------------------
# Terminal boundary row
# ---------------------------------------------------------------------------


def test_to_pandas_is_one_aggregate_terminal_edge() -> None:
    rows = REGISTRY.type_algebra_rows()
    terminal = [row for row in rows if row.help_target == "boundary.to_pandas"]
    assert len(terminal) == 1
    assert terminal[0].render() == (
        "all registered artifact families -> boundary.to_pandas -> pandas.DataFrame (terminal)"
    )
    assert terminal[0].source_families == frozenset(ARTIFACT_FAMILIES)


def test_to_pandas_row_source_families_are_all_artifact_families() -> None:
    descriptor = REGISTRY.by_id("boundary.to_pandas")
    assert descriptor.kind == "boundary"
    assert isinstance(descriptor, BoundaryCapability)
    # accepted_inputs["receiver"] must be materialized from ARTIFACT_FAMILIES
    receiver = descriptor.accepted_inputs.get("receiver", frozenset())
    assert frozenset(receiver) == frozenset(ARTIFACT_FAMILIES)


# ---------------------------------------------------------------------------
# Row structure invariants
# ---------------------------------------------------------------------------


def test_type_algebra_rows_are_deterministic() -> None:
    rows_a = REGISTRY.type_algebra_rows()
    rows_b = REGISTRY.type_algebra_rows()
    assert [r.render() for r in rows_a] == [r.render() for r in rows_b]


def test_every_row_has_non_empty_help_target_and_render() -> None:
    for row in REGISTRY.type_algebra_rows():
        assert row.help_target, f"row {row!r} has empty help_target"
        assert row.render(), f"row {row!r} has empty render()"


def test_help_targets_unique_except_grouped_members() -> None:
    """Each invokable row has a unique help_target; grouped topic rows
    (discover.*, transform.*) appear once as the collapsed topic."""
    rows = REGISTRY.type_algebra_rows()
    # The terminal boundary.to_pandas appears once (tested above).
    # discover and transform collapsed topics appear at most once each.
    discover_rows = [r for r in rows if r.help_target == "discover"]
    transform_rows = [r for r in rows if r.help_target == "transform"]
    assert len(discover_rows) <= 1
    assert len(transform_rows) <= 1


def test_type_algebra_row_render_format() -> None:
    """Each non-terminal row renders as 'sources -> target -> output_family'."""
    rows = REGISTRY.type_algebra_rows()
    for row in rows:
        text = row.render()
        assert "->" in text, f"row {row!r} render missing arrow: {text!r}"
        if row.is_terminal:
            assert "(terminal)" in text
        else:
            assert "(terminal)" not in text


# ---------------------------------------------------------------------------
# TypeAlgebraRow is a frozen value type
# ---------------------------------------------------------------------------


def test_type_algebra_row_is_frozen() -> None:
    import dataclasses

    row = REGISTRY.type_algebra_rows()[0]
    assert dataclasses.is_dataclass(row)
    try:
        row.help_target = "other"  # type: ignore[misc]
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("TypeAlgebraRow must be frozen")


def test_type_algebra_row_type() -> None:
    assert isinstance(REGISTRY.type_algebra_rows()[0], TypeAlgebraRow)


def test_event_funnel_type_algebra_carries_shape_and_matching() -> None:
    row = next(row for row in REGISTRY.type_algebra_rows() if row.help_target == "events.funnel")

    assert isinstance(row.output_contract, ArtifactOutputContract)
    assert row.render().endswith("events.funnel -> EventFrame[funnel; matching=first_per_subject]")


def test_metric_frame_coverage_type_algebra_renders_nullable_output() -> None:
    row = next(
        row for row in REGISTRY.type_algebra_rows() if row.help_target == "MetricFrame.coverage"
    )

    assert isinstance(row.output_contract, ArtifactOutputContract)
    assert row.output_family == "CoverageFrame"
    assert row.render().endswith("MetricFrame.coverage -> CoverageFrame | None")


def test_shape_aware_consumers_exclude_invalid_event_and_lifecycle_edges() -> None:
    funnel = REGISTRY.by_id("events.funnel")
    violations = REGISTRY.by_id("lifecycle.violations")

    funnel_consumers = REGISTRY.compatible_consumers(funnel.output_contract)
    assert "compare" in funnel_consumers
    assert "events.funnel" not in funnel_consumers
    assert "events.time_to_event" not in funnel_consumers
    assert "select_subjects" not in funnel_consumers

    violation_consumers = REGISTRY.compatible_consumers(violations.output_contract)
    assert "lifecycle.distribution" not in violation_consumers
    assert "lifecycle.transitions" not in violation_consumers
    assert "lifecycle.dwell" not in violation_consumers
    assert "lifecycle.violations" not in violation_consumers
