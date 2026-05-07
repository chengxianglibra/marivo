from __future__ import annotations

from app.core.engine import CoreEngine


def test_core_engine_no_svc_required() -> None:
    """After 4b-1, CoreEngine takes no svc argument."""
    engine = CoreEngine()
    assert engine is not None


def test_core_engine_pure_methods_work() -> None:
    """Pure computation methods work without svc."""
    engine = CoreEngine()
    assert engine.normalize_intent_metric_ref("test") == "metric.test"
    assert engine.metric_name_from_ref("metric.test") == "test"


def test_build_step_semantic_metadata_delegates() -> None:
    """build_step_semantic_metadata is now a pure function in core/semantic/step_metadata."""
    from unittest.mock import MagicMock

    from app.core.semantic.step_metadata import build_step_semantic_metadata

    compiled = MagicMock()
    compiled.metadata = {"resolved_metric_ref": "metric.test"}
    result = build_step_semantic_metadata(compiled)
    assert result is not None
    assert result["typed_inputs"]["metric_ref"] == "metric.test"
