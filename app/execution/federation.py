from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.execution.feedback import federation_failure_from_plan
from app.storage.analytics import AnalyticsEngine


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(slots=True)
class FederationStage:
    stage_id: str
    engine_id: str | None = None
    engine_type: str | None = None
    sql: str = ""
    params: list[Any] = field(default_factory=list)
    source_tables: list[str] = field(default_factory=list)
    output_name: str | None = None
    purpose: str = "direct_query"
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FederatedMergePlan:
    merge_engine_type: str
    strategy: str = "staged_merge"
    merge_sql: str | None = None
    input_aliases: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FederationPlan:
    mode: str = "single_engine"
    stages: list[FederationStage] = field(default_factory=list)
    merge: FederatedMergePlan | None = None
    audit: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "stages": [stage.to_dict() for stage in self.stages],
            "merge": self.merge.to_dict() if self.merge is not None else None,
            "audit": dict(self.audit),
            "provenance": dict(self.provenance),
        }


@dataclass(slots=True)
class FederatedExecutionResult:
    rows: list[dict[str, Any]]
    plan: FederationPlan
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": list(self.rows),
            "plan": self.plan.to_dict(),
            "metadata": dict(self.metadata),
        }


class FederationPlanner:
    """Build a stable staged-handoff contract for future multi-engine execution."""

    def build_plan(
        self,
        *,
        translated_sql: str,
        params: list[Any] | None = None,
        target_engine_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> FederationPlan:
        normalized_metadata = dict(metadata or {})
        federation = dict(normalized_metadata.get("federation") or {})
        step_type = _optional_str(normalized_metadata.get("step_type"))
        source_tables = self._source_tables(normalized_metadata)
        sources = self._normalize_sources(federation, source_tables, target_engine_type)
        distinct_sources = {
            (
                _optional_str(source.get("engine_id")),
                _optional_str(source.get("engine_type")),
                tuple(str(name) for name in source.get("table_names", [])),
            )
            for source in sources
        }
        requires_federation = (
            bool(federation.get("required"))
            or bool(federation.get("merge_sql"))
            or (federation.get("merge_strategy") is not None)
            or len(distinct_sources) > 1
        )

        if not requires_federation:
            return FederationPlan(
                mode="single_engine",
                stages=[
                    FederationStage(
                        stage_id="stage_0",
                        engine_id=_optional_str(normalized_metadata.get("engine_id")),
                        engine_type=target_engine_type,
                        sql=translated_sql,
                        params=list(params or []),
                        source_tables=source_tables,
                        output_name="result",
                        purpose="direct_query",
                        detail={"step_type": step_type},
                    )
                ],
                audit={
                    "requires_federation": False,
                    "stage_count": 1,
                    "merge_required": False,
                },
                provenance={
                    "step_type": step_type,
                    "target_engine_type": target_engine_type,
                },
            )

        stages: list[FederationStage] = []
        for index, source in enumerate(sources):
            stage_tables = [str(name) for name in source.get("table_names", [])]
            placeholder_source = ", ".join(stage_tables or source_tables or ["unknown_source"])
            stages.append(
                FederationStage(
                    stage_id=f"stage_{index}",
                    engine_id=_optional_str(source.get("engine_id")),
                    engine_type=_optional_str(source.get("engine_type")) or target_engine_type,
                    sql=str(source.get("sql") or f"-- staged handoff for {placeholder_source}"),
                    params=list(source.get("params", [])),
                    source_tables=stage_tables or source_tables,
                    output_name=_optional_str(source.get("output_name")) or f"stage_{index}_output",
                    purpose=str(source.get("purpose") or "stage_extract"),
                    detail={
                        "label": _optional_str(source.get("label")),
                        "step_type": step_type,
                    },
                )
            )

        merge = FederatedMergePlan(
            merge_engine_type=str(federation.get("merge_engine_type") or target_engine_type),
            strategy=str(federation.get("merge_strategy") or "staged_merge"),
            merge_sql=_optional_str(federation.get("merge_sql")),
            input_aliases=[stage.output_name or stage.stage_id for stage in stages],
            detail={
                "step_type": step_type,
                "target_engine_type": target_engine_type,
            },
        )
        return FederationPlan(
            mode="staged_handoff",
            stages=stages,
            merge=merge,
            audit={
                "requires_federation": True,
                "stage_count": len(stages),
                "merge_required": True,
                "source_engines": [
                    {
                        "engine_id": stage.engine_id,
                        "engine_type": stage.engine_type,
                    }
                    for stage in stages
                ],
            },
            provenance={
                "step_type": step_type,
                "target_engine_type": target_engine_type,
                "federation_reason": _optional_str(federation.get("reason"))
                or "staged_handoff_requested",
            },
        )

    def _source_tables(self, metadata: dict[str, Any]) -> list[str]:
        if isinstance(metadata.get("table_names"), list):
            return [str(name) for name in metadata["table_names"]]
        table_name = _optional_str(metadata.get("table_name"))
        return [table_name] if table_name is not None else []

    def _normalize_sources(
        self,
        federation: dict[str, Any],
        source_tables: list[str],
        target_engine_type: str,
    ) -> list[dict[str, Any]]:
        raw_sources = federation.get("sources")
        if isinstance(raw_sources, list) and raw_sources:
            return [dict(source) for source in raw_sources if isinstance(source, dict)]
        raw_source_engines = federation.get("source_engines")
        if isinstance(raw_source_engines, list) and raw_source_engines:
            normalized_sources: list[dict[str, Any]] = []
            for source in raw_source_engines:
                if isinstance(source, dict):
                    normalized_sources.append(
                        {
                            "engine_id": _optional_str(source.get("engine_id")),
                            "engine_type": _optional_str(source.get("engine_type"))
                            or target_engine_type,
                            "table_names": list(source.get("table_names", source_tables)),
                            "label": _optional_str(source.get("label")),
                        }
                    )
                    continue
                normalized_sources.append(
                    {
                        "engine_id": _optional_str(source),
                        "engine_type": target_engine_type,
                        "table_names": source_tables,
                    }
                )
            return normalized_sources
        return [
            {
                "engine_type": target_engine_type,
                "table_names": source_tables,
            }
        ]


class FederationRuntime:
    """Execute the single-engine path and fail honestly on staged federation."""

    def execute(
        self,
        engine: AnalyticsEngine,
        *,
        translated_sql: str,
        params: list[Any] | None = None,
        plan: FederationPlan | None = None,
    ) -> FederatedExecutionResult:
        effective_plan = plan or FederationPlan(
            mode="single_engine",
            stages=[
                FederationStage(
                    stage_id="stage_0",
                    sql=translated_sql,
                    params=list(params or []),
                    output_name="result",
                )
            ],
            audit={"requires_federation": False, "stage_count": 1, "merge_required": False},
        )
        if effective_plan.mode != "single_engine":
            raise federation_failure_from_plan(effective_plan)

        stage = effective_plan.stages[0]
        rows = engine.query_rows(stage.sql or translated_sql, stage.params or list(params or []))
        return FederatedExecutionResult(
            rows=rows,
            plan=effective_plan,
            metadata={
                "mode": effective_plan.mode,
                "executed_stage": stage.stage_id,
            },
        )
