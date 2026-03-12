from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analysis_core.compiler import CompiledQuery
from app.execution.feedback import engine_failure_from_error, translation_failure_from_error
from app.dialect import translate
from app.storage.analytics import AnalyticsEngine


@dataclass(slots=True)
class ExecutionResult:
    rows: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


def execute_compiled(engine: AnalyticsEngine, compiled_query: CompiledQuery) -> ExecutionResult:
    """Execute a compiled query through the analytics engine abstraction."""

    engine_type = str(compiled_query.metadata.get("engine_type", "duckdb"))
    try:
        translated_sql = translate(compiled_query.sql, engine_type)
    except Exception as error:
        raise translation_failure_from_error(compiled_query, error) from error
    try:
        rows = engine.query_rows(translated_sql, compiled_query.params)
    except Exception as error:
        raise engine_failure_from_error(compiled_query, error) from error
    return ExecutionResult(
        rows=rows,
        metadata={**compiled_query.metadata, "translated_sql": translated_sql},
    )
