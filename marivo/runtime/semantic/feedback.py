from __future__ import annotations

from typing import TYPE_CHECKING, Any

from marivo.contracts.errors import ExecutionError
from marivo.core.semantic.compiler import (
    CompiledQuery,
    SemanticCompilerError,
    SemanticRequestCompatibilityError,
)
from marivo.core.semantic.ir import AnalysisStepIR
from marivo.routing import RoutingResolutionError
from marivo.runtime.errors import SemanticRuntimeNotReadyError
from marivo.runtime_contracts import ExecutionFeedback

if TYPE_CHECKING:
    from marivo.runtime.execution.federation import FederationPlan


def routing_feedback_from_error(
    error: Exception,
    *,
    table_names: list[str],
    fallback_candidates: list[str] | None = None,
) -> ExecutionFeedback:
    message = str(error)
    detail: dict[str, Any] = {"table_names": list(table_names)}
    if isinstance(error, RoutingResolutionError):
        code = {
            "routing_table_not_found": "routing_table_not_found",
            "routing_table_ambiguous": "routing_table_ambiguous",
            "routing_no_common_engine": "routing_no_common_engine",
            "routing_no_tables": "routing_no_tables",
            "routing_source_unmapped": "routing_source_unmapped",
            "routing_source_unavailable": "routing_source_unavailable",
            "routing_projection_failed": "routing_projection_failed",
        }.get(error.code, "routing_resolution_failed")
        detail["routing_detail"] = error.routing_detail
    elif isinstance(error, KeyError):
        code = "routing_table_not_found"
    else:
        code = "routing_resolution_failed"

    return ExecutionFeedback(
        code=code,
        category="routing",
        message=message,
        replan_candidate=True,
        fallback_candidates=list(fallback_candidates or ["use_default_analytics_engine"]),
        detail=detail,
    )


def compile_failure_from_error(
    step: AnalysisStepIR,
    error: Exception,
    *,
    semantic_context: dict[str, Any] | None = None,
) -> ExecutionError:
    if isinstance(error, SemanticRuntimeNotReadyError):
        detail = error.detail_payload()
        return ExecutionError(
            code=str(detail["code"]),
            category="readiness",
            message=str(detail["message"]),
            replan_candidate=False,
            fallback_candidates=[],
            detail={
                "step_type": step.step_type,
                "step_index": step.index,
                "semantic_context_keys": sorted((semantic_context or {}).keys()),
                "readiness_error": detail,
            },
        )
    if isinstance(error, SemanticRequestCompatibilityError):
        detail = dict(error.detail)
        return ExecutionError(
            code=str(detail["code"]),
            category="compatibility",
            message=str(detail["message"]),
            replan_candidate=False,
            fallback_candidates=[],
            detail={
                "step_type": step.step_type,
                "step_index": step.index,
                "semantic_context_keys": sorted((semantic_context or {}).keys()),
                "compatibility_error": detail,
            },
        )

    compile_error = error.compile_error if isinstance(error, SemanticCompilerError) else None
    message = compile_error["message"] if compile_error is not None else str(error)
    normalized = message.lower()
    if compile_error is not None:
        code = str(compile_error["error_code"]).lower()
    elif "requires" in normalized:
        code = "capability_mismatch"
    elif "unsupported compilation step type" in normalized:
        code = "translation_error"
    else:
        code = "compile_failure"

    return ExecutionError(
        code=code,
        category="compiler",
        message=message,
        replan_candidate=True,
        fallback_candidates=["prefer_profile_table", "prefer_aggregate_path"],
        detail={
            "step_type": step.step_type,
            "step_index": step.index,
            "semantic_context_keys": sorted((semantic_context or {}).keys()),
            "compile_error": compile_error,
        },
    )


def translation_failure_from_error(
    compiled_query: CompiledQuery,
    error: Exception,
) -> ExecutionError:
    return ExecutionError(
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


def federation_failure_from_plan(
    plan: FederationPlan,
    *,
    message: str | None = None,
) -> ExecutionError:
    return ExecutionError(
        code="federation_not_implemented",
        category="federation",
        message=message
        or (
            "Federated execution requires staged handoff, but only the skeleton contract is implemented"
        ),
        replan_candidate=True,
        fallback_candidates=["prefer_single_engine_route", "materialize_inputs_before_merge"],
        detail={
            "mode": plan.mode,
            "stage_count": len(plan.stages),
            "merge_required": plan.merge is not None,
            "plan": plan.to_dict(),
        },
    )


def engine_failure_from_error(
    compiled_query: CompiledQuery,
    error: Exception,
) -> ExecutionError:
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

    return ExecutionError(
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
