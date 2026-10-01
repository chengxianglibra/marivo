"""Pure complete Delta side states for local driver numerical tests."""

from typing import Literal

import pandas as pd

from marivo.analysis.operators.driver_contracts import DriverCandidateSpecV1
from marivo.analysis.operators.row import PartFrame
from marivo.refs import ref
from tests.lazy_attribute_fixtures import CHANNEL, REGION, inputs
from tests.lazy_observation_fixtures import make_sources
from tests.r8_arithmetic_fixtures import comparison_for_metric, driver_for_spec


def driver_inputs(
    keys: list[tuple[str | None, ...]],
    current: list[tuple[float, int]],
    baseline: list[tuple[float, int]],
    *,
    name: str = "revenue",
    search: Literal["all", "first"] = "all",
    limit: int = 50,
    current_absent: tuple[int, ...] = (),
) -> tuple[pd.DataFrame, DriverCandidateSpecV1, tuple[PartFrame, ...]]:
    frame, _, parts = inputs(name, keys, current, baseline, current_absent=current_absent)
    axes = (REGION,) if len(keys[0]) == 1 else (REGION, CHANNEL)
    metric = make_sources().observe(ref.metric(f"sales.{name}")).with_dimensions(*axes).aggregate()
    spec = driver_for_spec(
        comparison_for_metric(metric), metric, axes if search == "all" else axes[:1], limit=limit
    )
    return frame, spec, parts
