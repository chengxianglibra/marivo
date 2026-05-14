# DEPRECATED: Execution logic will be migrated to ports in Phase 3d+.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from marivo.adapters.server.translation import DefaultQueryTranslator, request_from_compiled_query
from marivo.contracts.errors import ExecutionError
from marivo.core.semantic.compiler import CompiledQuery
from marivo.ports.analytics import AnalyticsEngine
from marivo.runtime.execution.federation import FederationRuntime
from marivo.runtime.semantic.feedback import (
    engine_failure_from_error,
    translation_failure_from_error,
)

_DEFAULT_TRANSLATOR = DefaultQueryTranslator()
_DEFAULT_FEDERATION_RUNTIME = FederationRuntime()


def annotate_sql(sql: str, session_id: str) -> str:
    """Prepend a Marivo actor comment to SQL for engine-level observability."""
    safe_id = session_id.replace("*/", "").replace("/*", "")
    return f"/* actor=marivo, session_id={safe_id} */\n{sql}"


@dataclass(slots=True)
class ExecutionResult:
    rows: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


def execute_compiled(
    engine: AnalyticsEngine,
    compiled_query: CompiledQuery,
    *,
    session_id: str | None = None,
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
            session_id=session_id,
        )
    except ExecutionError:
        raise
    except Exception as error:
        raise engine_failure_from_error(compiled_query, error) from error
    return ExecutionResult(
        rows=execution_result.rows,
        metadata={
            **compiled_query.metadata,
            "session_id": session_id,
            "translated_sql": translation_result.sql,
            "translation": translation_result.to_dict(),
            "federation_plan": execution_result.plan.to_dict(),
        },
    )
