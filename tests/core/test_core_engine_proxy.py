from __future__ import annotations

from unittest.mock import MagicMock

from app.core.engine import CoreEngine


def test_core_engine_holds_reference_to_service() -> None:
    svc = MagicMock()
    engine = CoreEngine(svc)
    assert engine._svc is svc


def test_resolve_metric_execution_context_delegates() -> None:
    svc = MagicMock()
    svc._resolve_metric_execution_context.return_value = "resolved"
    engine = CoreEngine(svc)

    result = engine.resolve_metric_execution_context("arg1", key="val")

    svc._resolve_metric_execution_context.assert_called_once_with("arg1", key="val")
    assert result == "resolved"


def test_compile_step_delegates() -> None:
    svc = MagicMock()
    svc._compile_step_with_feedback.return_value = "compiled"
    engine = CoreEngine(svc)

    result = engine.compile_step("step", mode="fast")

    svc._compile_step_with_feedback.assert_called_once_with("step", mode="fast")
    assert result == "compiled"


def test_build_step_semantic_metadata_delegates() -> None:
    svc = MagicMock()
    svc.build_step_semantic_metadata.return_value = "metadata"
    engine = CoreEngine(svc)

    result = engine.build_step_semantic_metadata("query")

    svc.build_step_semantic_metadata.assert_called_once_with("query")
    assert result == "metadata"
