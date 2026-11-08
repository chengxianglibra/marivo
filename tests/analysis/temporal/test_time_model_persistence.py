"""Natural time values retain exact identities through captured input transport."""

from pydantic import BaseModel

import marivo.analysis as mv
from marivo._temporal import Grain, TimeScope
from marivo.analysis.dsl.temporal import exact_scope


class CapturedTime(BaseModel):
    grain: Grain
    scope: TimeScope


def test_natural_time_transport_preserves_exact_values() -> None:
    scope = exact_scope("2026-11-01", "2026-11-02", "America/New_York")
    captured = CapturedTime(grain=mv.grain("hour"), scope=scope)
    restored = CapturedTime.model_validate_json(captured.model_dump_json())
    assert restored.grain == mv.grain("hour")
    assert restored.scope == scope
    assert restored.scope.boundary_timezone == "America/New_York"
    assert (scope.end - scope.start).total_seconds() == 25 * 3600
