"""Translate contribution clocks into captured time metadata."""

from __future__ import annotations

from marivo.semantic._deferred_facts import MetricFacts
from marivo.semantic._formula_display import FormulaDisplay
from marivo.semantic.catalog_entries import (
    MetricContributionClockBinding,
    MetricNoContributionClockBinding,
)
from marivo.semantic.expressions.metric_time import metric_time_occurrences


def metric_time_bindings(
    metric: MetricFacts, display: FormulaDisplay | None = None
) -> tuple[MetricContributionClockBinding | MetricNoContributionClockBinding, ...]:
    """Project each captured contribution occurrence without consulting a Catalog."""
    display = display or FormulaDisplay()
    bindings: list[MetricContributionClockBinding | MetricNoContributionClockBinding] = []
    for occurrence in metric_time_occurrences(metric.node, dict(metric.declared)):
        branch = display.formula(occurrence.node)
        if occurrence.path:
            branch = f"{' / '.join(occurrence.path)}: {branch}"
        time = occurrence.binding
        if time is None:
            assert occurrence.reason is not None
            bindings.append(
                MetricNoContributionClockBinding(
                    branch,
                    occurrence.root.ref.key if occurrence.root is not None else None,
                    occurrence.reason,
                )
            )
        else:
            bindings.append(
                MetricContributionClockBinding(
                    branch,
                    time.field.ref.key,
                    tuple(item.ref.key for item in time.path),
                    source=time.source,
                    anchor=time.at,
                )
            )
    return tuple(bindings)
