"""Public names for captured Metric contribution-time roles."""

from __future__ import annotations

import pytest

import marivo.semantic as ms
from marivo._help.model import MarivoHelpTargetError
from marivo._help.render import render_help_text


@pytest.mark.parametrize(
    ("name", "target", "retired_name", "retired_target"),
    (
        (
            "MetricContributionTimeRole",
            "semantic.metric_contribution_time_role",
            "MetricTimeBinding",
            "semantic.metric_time_binding",
        ),
        (
            "MetricContributionClockBinding",
            "semantic.metric_contribution_clock_binding",
            "MetricTimeFieldBinding",
            "semantic.metric_time_field_binding",
        ),
        (
            "MetricNoContributionClockBinding",
            "semantic.metric_no_contribution_clock_binding",
            "MetricNoTimeBinding",
            "semantic.metric_no_time_binding",
        ),
    ),
)
def test_contribution_time_public_names_and_help(
    name: str, target: str, retired_name: str, retired_target: str
) -> None:
    assert name in ms.__all__ and isinstance(getattr(ms, name), type)
    assert retired_name not in ms.__all__ and not hasattr(ms, retired_name)
    text, _, _ = render_help_text(target)
    assert name in text
    with pytest.raises(MarivoHelpTargetError):
        render_help_text(retired_target)
