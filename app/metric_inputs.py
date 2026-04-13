from __future__ import annotations

from typing import Final

_REQUIRED_METRIC_INPUT_SLOTS: Final[dict[str, tuple[str, ...]]] = {
    "count_metric": ("count_target",),
    "sum_metric": ("measure",),
    "rate_metric": ("numerator", "denominator"),
    "average_metric": ("numerator", "denominator"),
    "distribution_metric": ("value_component",),
    "score_metric": ("score_source",),
    "survival_metric": (),
}


def required_metric_input_slots(metric_family: str | None) -> tuple[str, ...]:
    normalized = str(metric_family or "").strip()
    return _REQUIRED_METRIC_INPUT_SLOTS.get(normalized, ())
