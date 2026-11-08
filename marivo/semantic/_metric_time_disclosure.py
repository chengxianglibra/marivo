"""Bounded display of the captured contribution-time facts of a Metric."""

from __future__ import annotations

from marivo.render import Card
from marivo.semantic.catalog_entries import (
    MetricNoTimeBinding,
    MetricTimeBinding,
    MetricTimeFieldBinding,
)


def metric_time_binding_text(binding: MetricTimeBinding) -> str:
    if isinstance(binding, MetricTimeFieldBinding):
        return f"{binding.field} ({binding.source})"
    if isinstance(binding, MetricNoTimeBinding):
        return (
            f"{binding.root} (report independent: {binding.reason})"
            if binding.root is not None
            else "fixed Population.count (report independent)"
        )
    return "(none)"


def add_metric_time(
    card: Card,
    bindings: tuple[MetricTimeFieldBinding | MetricNoTimeBinding, ...],
    *,
    recovery: str,
) -> None:
    """Add the same branch facts to named and anonymous Metric cards."""
    card.field(
        "report_time",
        "clocked branches follow report time; independent branches retain their scope",
    )
    rows = [(item.branch, metric_time_binding_text(item)) for item in bindings]
    card.table(
        ("branch", "time_binding"),
        rows,
        row_count=len(rows),
        label="time_bindings",
        show_omission_counts=True,
        bounded_row_limit=6,
        recovery=recovery,
    )
