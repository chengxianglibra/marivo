from __future__ import annotations

import importlib
import sys

import pytest


def test_ir_has_no_io_imports() -> None:
    """Verify app.core.semantic.ir imports nothing from I/O modules."""
    mod_name = "marivo.core.semantic.ir"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    mod = importlib.import_module(mod_name)

    # Standard I/O modules that core must not depend on
    forbidden = {
        "sqlalchemy",
        "asyncio",
        "httpx",
        "aiohttp",
        "requests",
        "marivo.service",
        "marivo.infrastructure",
        "marivo.analysis_core",
    }

    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        module = getattr(obj, "__module__", None)
        if module and any(module.startswith(f) or module == f for f in forbidden):
            pytest.fail(
                f"marivo.core.semantic.ir references I/O module {module!r} via attribute {attr_name!r}"
            )


def test_ir_imports_from_core_intent_primitives() -> None:
    """Verify that ir.py uses the core intent primitives, not analysis_core."""
    # step_category_for should come from core, not analysis_core
    from marivo.core.intent.primitives import step_category_for
    from marivo.core.semantic import ir

    assert ir.step_category_for is step_category_for


def test_analysis_request_defaults() -> None:
    from marivo.core.semantic.ir import AnalysisRequest

    req = AnalysisRequest()
    assert req.goal == ""
    assert req.session_id is None
    assert req.constraints == {}
    assert req.requested_step_types == []
    assert req.requested_metrics == []
    assert req.requested_tables == []


def test_analysis_step_ir_table_name() -> None:
    from marivo.core.semantic.ir import AnalysisStepIR

    step = AnalysisStepIR(index=0, step_type="metric_query", params={"table": "my_table"})
    assert step.table_name() == "my_table"
    assert step.routing_table_name() == "my_table"


def test_analysis_step_ir_table_name_dotted() -> None:
    from marivo.core.semantic.ir import AnalysisStepIR

    step = AnalysisStepIR(index=0, step_type="metric_query", params={"table": "schema.my_table"})
    assert step.table_name() == "schema.my_table"
    assert step.routing_table_name() == "my_table"


def test_execution_plan_ir_semantic_resolution() -> None:
    from marivo.core.semantic.ir import ExecutionPlanIR, SemanticResolutionIR

    plan = ExecutionPlanIR()
    assert plan.semantic_resolution_for_step(0) is None

    resolution = SemanticResolutionIR(step_index=0)
    plan.semantic_resolutions.append(resolution)
    assert plan.semantic_resolution_for_step(0) is resolution


def test_step_ir_from_mapping() -> None:
    from marivo.core.semantic.ir import AnalysisStepIR, step_ir_from_mapping

    step = step_ir_from_mapping(
        0,
        {
            "step_type": "metric_query",
            "params": {"metric": "revenue", "table": "orders"},
            "dependencies": [1],
        },
    )
    assert isinstance(step, AnalysisStepIR)
    assert step.step_type == "metric_query"
    assert step.step_category == "primitive"
    assert step.params["metric"] == "revenue"
    assert step.semantic_intent is not None
    assert step.semantic_intent.metrics == ["revenue"]


def test_request_from_session_payload() -> None:
    from marivo.core.semantic.ir import AnalysisRequest, request_from_session_payload

    session = {"goal": "test", "session_id": "s1", "constraints": {"max": 5}}
    req = request_from_session_payload(session)
    assert isinstance(req, AnalysisRequest)
    assert req.goal == "test"
    assert req.session_id == "s1"


def test_typed_dict_classes_are_importable() -> None:
    """Spot-check that key TypedDict classes are available."""
    from marivo.core.semantic.ir import (
        IrBundle,
        IrPlan,
        IrPlanHeader,
    )

    # TypedDict classes should have __annotations__
    assert "ir_version" in IrPlanHeader.__annotations__
    assert "header" in IrPlan.__annotations__
    assert "plan" in IrBundle.__annotations__
