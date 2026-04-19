from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from app.analysis_core.compiler import CompiledQuery
from app.dialect import translate as translate_sql
from app.execution.federation import FederationPlan, FederationPlanner


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(slots=True)
class TranslationRequest:
    sql: str
    params: list[Any] = field(default_factory=list)
    target_engine_type: str = "duckdb"
    source_dialect: str = "duckdb"
    step_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TranslationResult:
    sql: str
    params: list[Any] = field(default_factory=list)
    target_engine_type: str = "duckdb"
    source_dialect: str = "duckdb"
    strategy: str = "direct_sql"
    detail: dict[str, Any] = field(default_factory=dict)
    federation_plan: FederationPlan | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["federation_plan"] = (
            self.federation_plan.to_dict() if self.federation_plan is not None else None
        )
        return payload


class QueryTranslator(Protocol):
    def translate(self, request: TranslationRequest) -> TranslationResult: ...


def request_from_compiled_query(compiled_query: CompiledQuery) -> TranslationRequest:
    metadata = dict(compiled_query.metadata)
    return TranslationRequest(
        sql=compiled_query.sql,
        params=list(compiled_query.params),
        target_engine_type=str(metadata.get("engine_type", "duckdb")),
        source_dialect=str(metadata.get("source_dialect", "duckdb")),
        step_type=_optional_str(metadata.get("step_type")),
        metadata=metadata,
    )


class DefaultQueryTranslator:
    """Translate SQL while attaching a future-proof federation handoff contract."""

    def __init__(self, *, federation_planner: FederationPlanner | None = None) -> None:
        self.federation_planner = federation_planner or FederationPlanner()

    def translate(self, request: TranslationRequest) -> TranslationResult:
        translated_sql = translate_sql(request.sql, request.target_engine_type)
        federation_plan = self.federation_planner.build_plan(
            translated_sql=translated_sql,
            params=request.params,
            target_engine_type=request.target_engine_type,
            metadata=request.metadata,
        )
        strategy = "federated_handoff" if federation_plan.mode != "single_engine" else "direct_sql"
        detail = {
            "target_engine_type": request.target_engine_type,
            "source_dialect": request.source_dialect,
            "step_type": request.step_type,
            "translation_strategy": strategy,
            "federation_mode": federation_plan.mode,
            "stage_count": len(federation_plan.stages),
        }
        if federation_plan.merge is not None:
            detail["merge_engine_type"] = federation_plan.merge.merge_engine_type
            detail["merge_strategy"] = federation_plan.merge.strategy
        return TranslationResult(
            sql=translated_sql,
            params=list(request.params),
            target_engine_type=request.target_engine_type,
            source_dialect=request.source_dialect,
            strategy=strategy,
            detail=detail,
            federation_plan=federation_plan,
        )
