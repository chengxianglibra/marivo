from __future__ import annotations

from typing import Any

from app.analysis_core.compiler import CompiledQuery
from app.analysis_core.ir import AnalysisStepIR
from app.execution.errors import ExecutionFailure
from app.runtime_contracts import ExecutionFeedback


def routing_feedback_from_error(
    error: Exception,
    *,
    table_names: list[str],
    fallback_candidates: list[str] | None = None,
) -> ExecutionFeedback:
    message = str(error)
    normalized = message.lower()

    if isinstance(error, KeyError):
        code = "routing_table_not_found"
    elif "no common engine" in normalized:
        code = "routing_no_common_engine"
    elif "no active engine bindings" in normalized:
        code = "engine_unavailable"
    elif "no table names provided" in normalized:
        code = "routing_no_tables"
    else:
        code = "routing_resolution_failed"

    return ExecutionFeedback(
        code=code,
        category="routing",
        message=message,
        replan_candidate=True,
        fallback_candidates=list(fallback_candidates or ["use_default_analytics_engine"]),
        detail={"table_names": list(table_names)},
    )


def compile_failure_from_error(
    step: AnalysisStepIR,
    error: Exception,
    *,
    semantic_context: dict[str, Any] | None = None,
) -> ExecutionFailure:
    message = str(error)
    normalized = message.lower()
    if "requires" in normalized:
        code = "capability_mismatch"
    elif "unsupported compilation step type" in normalized:
        code = "translation_error"
    else:
        code = "compile_failure"

    return ExecutionFailure(
        code=code,
        category="compiler",
        message=message,
        replan_candidate=True,
        fallback_candidates=["prefer_profile_table", "prefer_aggregate_path"],
        detail={
            "step_type": step.step_type,
            "step_index": step.index,
            "semantic_context_keys": sorted((semantic_context or {}).keys()),
        },
    )


def translation_failure_from_error(
    compiled_query: CompiledQuery,
    error: Exception,
) -> ExecutionFailure:
    return ExecutionFailure(
        code="translation_error",
        category="translator",
        message=str(error),
        replan_candidate=True,
        fallback_candidates=["prefer_default_engine", "prefer_aggregate_path"],
        detail={
            "step_type": compiled_query.metadata.get("step_type"),
            "engine_type": compiled_query.metadata.get("engine_type"),
        },
    )


def engine_failure_from_error(
    compiled_query: CompiledQuery,
    error: Exception,
) -> ExecutionFailure:
    message = str(error)
    normalized = message.lower()
    if "timeout" in normalized:
        code = "timeout"
        retryable = True
    elif "no such table" in normalized or "not found" in normalized:
        code = "partial_result"
        retryable = False
    else:
        code = "engine_query_failed"
        retryable = False

    return ExecutionFailure(
        code=code,
        category="executor",
        message=message,
        retryable=retryable,
        replan_candidate=True,
        fallback_candidates=["prefer_profile_table", "prefer_aggregate_path"],
        detail={
            "step_type": compiled_query.metadata.get("step_type"),
            "engine_type": compiled_query.metadata.get("engine_type"),
            "table_name": compiled_query.metadata.get("table_name"),
        },
    )
