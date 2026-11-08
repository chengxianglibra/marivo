"""Natural time values retain exact identities through captured input transport."""

import pytest
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


def test_retired_semantic_grain_payload_does_not_acquire_execution_authority() -> None:
    scope = exact_scope("2026-01-01", "2026-02-01", "UTC")
    with pytest.raises(ValueError, match="unsupported Grain"):
        CapturedTime.model_validate(
            {
                "scope": scope,
                "grain": {
                    "schema": "grain/v2",
                    "kind": "semantic",
                    "calendar": "sales.fiscal",
                    "level": "month",
                    "snapshot": None,
                },
            }
        )
