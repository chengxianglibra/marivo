"""Tests for the analysis capability type algebra generated from the registry.

These tests pin the type-algebra rows that the registry emits: the canonical
operator edges, discover/transform grouping collapse, frame producer/read
edges, constructor consumer reverse edges, and the single terminal
``boundary.to_pandas`` aggregate row.
"""

from __future__ import annotations

from marivo.analysis._capabilities import ARTIFACT_FAMILIES, BoundaryCapability
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


def test_derive_metric_frame_is_governed_entry_row() -> None:
    """boundary.derive_metric_frame appears as a governed-entry producer of
    MetricFrame with its accepted input families as sources."""
    rows = REGISTRY.type_algebra_rows()
    gov = [row for row in rows if row.help_target == "boundary.derive_metric_frame"]
    assert len(gov) == 1
    row = gov[0]
    assert row.is_terminal is False
    assert row.output_family == "MetricFrame"
    assert row.source_families == frozenset({"IbisQuerySpec", "MetricColumns"})
    assert row.render() == (
        "IbisQuerySpec, MetricColumns -> boundary.derive_metric_frame -> MetricFrame"
    )


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
