# DEPRECATED: Execution logic will be migrated to ports in Phase 3d+.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from marivo.adapters.server.translation import DefaultQueryTranslator, request_from_compiled_query
from marivo.contracts.errors import ExecutionError
from marivo.core.semantic.compiler import CompiledQuery
from marivo.runtime.execution.federation import FederationRuntime
from marivo.runtime.semantic.feedback import (
    engine_failure_from_error,
    translation_failure_from_error,
)
from marivo.storage.analytics import AnalyticsEngine

_DEFAULT_TRANSLATOR = DefaultQueryTranslator()
_DEFAULT_FEDERATION_RUNTIME = FederationRuntime()


@dataclass(slots=True)
class ExecutionResult:
    rows: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


def execute_compiled(
    engine: AnalyticsEngine,
    compiled_query: CompiledQuery,
) -> ExecutionResult:
    """Execute a compiled query through the analytics engine abstraction."""

    try:
        translation_result = _DEFAULT_TRANSLATOR.translate(
            request_from_compiled_query(compiled_query)
        )
    except Exception as error:
        raise translation_failure_from_error(compiled_query, error) from error
    try:
        execution_result = _DEFAULT_FEDERATION_RUNTIME.execute(
            engine,
            translated_sql=translation_result.sql,
            params=translation_result.params,
            plan=translation_result.federation_plan,
        )
    except ExecutionError:
        raise
    except Exception as error:
        raise engine_failure_from_error(compiled_query, error) from error
    return ExecutionResult(
        rows=execution_result.rows,
        metadata={
            **compiled_query.metadata,
            "translated_sql": translation_result.sql,
            "translation": translation_result.to_dict(),
            "federation_plan": execution_result.plan.to_dict(),
        },
    )
