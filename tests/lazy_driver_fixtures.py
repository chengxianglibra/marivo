"""Pure complete Delta side states for local driver numerical tests."""

from typing import Literal

import pandas as pd

from marivo.analysis.datasets.handles import LogicalRootHandle
from marivo.analysis.operators.driver_contracts import DriverCandidatePayload, DriverCandidateSpecV1
from marivo.analysis.operators.row import PartFrame
from marivo.refs import ref
from tests.lazy_attribute_fixtures import CHANNEL, REGION, inputs
from tests.lazy_observation_fixtures import make_sources


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
    candidate = metric.compare(metric).discover.driver_axes(
        search_space=axes if search == "all" else axes[:1], limit=limit
    )
    assert isinstance(candidate._root, LogicalRootHandle)
    assert isinstance(candidate._root.payload, DriverCandidatePayload)
    return frame, candidate._root.payload.spec, parts
