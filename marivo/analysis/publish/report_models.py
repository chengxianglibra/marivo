"""Pydantic models for Marivo analysis report artifacts."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EvidenceStatus = Literal["complete", "partial", "unavailable"]
ReportSectionType = Literal[
    "executive_summary",
    "scope",
    "finding",
    "analysis_step",
    "candidate_review",
    "caveat",
    "next_step",
    "source_detail",
]
ReportBlockType = Literal[
    "markdown",
    "metric_strip",
    "chart",
    "table",
    "step_trace",
    "claim_evidence",
    "source_code",
]
FlowStepKind = Literal[
    "intent",
    "explore_ibis",
    "pandas_scratch",
    "promotion",
    "transform",
    "quality_assessment",
    "agent_decision",
]
GroundingType = Literal["evidence_backed", "derived_from_flow", "commentary"]
SqlStatus = Literal["available", "not_applicable", "unavailable", "redacted"]
DataInclusion = Literal["omitted", "included"]
ReportValueFormat = Literal["number", "compact", "percent", "currency", "text"]
ReportColumnType = Literal["text", "number", "percent", "currency", "date"]
ReportChartType = Literal[
    "line",
    "area",
    "stackedArea",
    "bar",
    "histogram",
    "scatter",
    "heatmap",
    "pie",
    "leaderboard",
    "sparkline",
    "funnel",
    "waterfall",
    "boxPlot",
]

JsonScalar = str | int | float | bool | None
JsonRow = dict[str, JsonScalar]


def _validate_json_row_scalars_are_finite(rows: tuple[JsonRow, ...]) -> tuple[JsonRow, ...]:
    for row_index, row in enumerate(rows):
        for column, value in row.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(
                    f"dataset row value must be finite at rows[{row_index}][{column!r}]"
                )
    return rows


class _ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DataPolicy(_ReportModel):
    row_level_data: DataInclusion = "omitted"
    frame_snapshots: DataInclusion = "omitted"
    authority: Literal["manifest_default", "dataset_override"] = "manifest_default"


class SourceProvenance(_ReportModel):
    generated_from: FlowStepKind = "intent"
    query_summary: str = ""
    semantic_refs: tuple[str, ...] = ()
    datasource_refs: tuple[str, ...] = ()
    sql_status: SqlStatus = Field(
        default="not_applicable",
        description=(
            "SQL availability. 'available' requires sql text; "
            "'not_applicable'/'unavailable'/'redacted' require sql_reason."
        ),
    )
    sql: str | None = Field(
        default=None,
        description="SQL text. Required when sql_status='available'; forbidden otherwise.",
    )
    sql_reason: str | None = Field(
        default=None,
        description="Reason sql is unavailable. Auto-populated if omitted for non-available sql_status.",
    )
    script_ref: str | None = None
    promotion_ref: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_sql_reason_for_non_available(cls, data: Any) -> Any:
        if isinstance(data, dict):
            sql_status = data.get("sql_status", "not_applicable")
            sql_reason = data.get("sql_reason")
            if sql_status != "available" and not sql_reason:
                data = {**data, "sql_reason": "No SQL was generated for this source."}
        return data

    @model_validator(mode="after")
    def validate_sql_status(self) -> SourceProvenance:
        if self.sql_status == "available" and not self.sql:
            raise ValueError("sql_status='available' requires sql")
        if self.sql_status != "available" and self.sql is not None:
            raise ValueError("non-available sql_status must not include sql")
        if self.sql_status != "available" and not self.sql_reason:
            raise ValueError("non-available sql_status requires sql_reason")
        return self


class DatasetMetadata(_ReportModel):
    dataset_id: str | None = None
    grain: str = "overall"
    row_count: int | None = None
    truncated: bool = False
    columns: tuple[str, ...] = ()
    source_artifacts: tuple[str, ...] = ()
    source_provenance: SourceProvenance = Field(default_factory=SourceProvenance)
    metric_definitions: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    data_policy: DataPolicy = Field(default_factory=DataPolicy)


class Dataset(_ReportModel):
    dataset_id: str
    metadata: DatasetMetadata
    rows: tuple[JsonRow, ...]

    @field_validator("rows")
    @classmethod
    def validate_rows_are_strict_json(cls, value: tuple[JsonRow, ...]) -> tuple[JsonRow, ...]:
        return _validate_json_row_scalars_are_finite(value)

    @model_validator(mode="before")
    @classmethod
    def _auto_fill_metadata_identity(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        dataset_id = data.get("dataset_id")
        metadata = data.get("metadata")
        if dataset_id is None or metadata is None:
            return data
        if isinstance(metadata, DatasetMetadata):
            metadata = metadata.model_dump(mode="python")
        if isinstance(metadata, dict):
            updates: dict[str, Any] = {}
            if metadata.get("dataset_id") is None:
                updates["dataset_id"] = dataset_id
            if metadata.get("row_count") is None:
                rows = data.get("rows")
                updates["row_count"] = len(rows) if rows is not None else 0
            if updates:
                metadata = {**metadata, **updates}
                data = {**data, "metadata": metadata}
        return data

    @model_validator(mode="after")
    def validate_dataset_identity(self) -> Dataset:
        if self.metadata.dataset_id != self.dataset_id:
            raise ValueError("dataset metadata id must match dataset_id")
        if self.metadata.row_count != len(self.rows):
            raise ValueError("dataset metadata row_count must match rows length")
        return self

    @classmethod
    def from_rows(
        cls,
        *,
        dataset_id: str,
        rows: tuple[JsonRow, ...],
        metadata: DatasetMetadata | None = None,
    ) -> Dataset:
        """Construct a Dataset with auto-derived metadata defaults.

        dataset_id and row_count are auto-filled on the metadata.
        columns defaults to the keys of the first row when metadata is not provided.
        All other DatasetMetadata fields use their defaults.
        """
        if metadata is None:
            columns = tuple(rows[0].keys()) if rows else ()
            metadata = DatasetMetadata(columns=columns)
        return cls(dataset_id=dataset_id, metadata=metadata, rows=rows)


class ReportMetric(_ReportModel):
    label: str
    value_ref: str
    format: ReportValueFormat = "compact"
    signed: bool = False


class ReportColumn(_ReportModel):
    key: str
    label: str | None = None
    type: ReportColumnType = "text"
    format: ReportValueFormat | None = None


class ReportChartSpec(_ReportModel):
    type: ReportChartType
    fields: dict[str, str]
    options: dict[str, Any] = Field(default_factory=dict)


class ReportBlock(_ReportModel):
    block_id: str
    block_type: ReportBlockType
    title: str | None = None
    subtitle: str | None = None
    text: str | None = None
    dataset_id: str | None = None
    value_refs: tuple[str, ...] = ()
    claim_refs: tuple[str, ...] = ()
    step_refs: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    narrative_ref: str | None = None
    metrics: tuple[ReportMetric, ...] = ()
    columns: tuple[ReportColumn, ...] = ()
    chart: ReportChartSpec | None = None
    collapsed_by_default: bool = False


class ReportSection(_ReportModel):
    section_id: str
    section_type: ReportSectionType
    title: str
    blocks: tuple[ReportBlock, ...]


class ReportSpec(_ReportModel):
    title: str
    sections: tuple[ReportSection, ...]


class FlowStep(_ReportModel):
    step_id: str
    order: int
    kind: FlowStepKind
    description: str
    input_artifacts: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    semantic_refs: tuple[str, ...] = ()
    source_queries: tuple[str, ...] = ()
    script_ref: str | None = None
    evidence_status: EvidenceStatus
    query_summary: str | None = None
    links: dict[str, str] = Field(default_factory=dict)


class Flow(_ReportModel):
    steps: tuple[FlowStep, ...]


class GroundedClaim(_ReportModel):
    claim_id: str
    text_template: str
    value_refs: tuple[str, ...] = ()
    section_id: str
    grounding_type: GroundingType
    evidence_status: EvidenceStatus
    supporting_artifacts: tuple[str, ...] = ()
    supporting_steps: tuple[str, ...] = ()
    supporting_datasets: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    risk_refs: tuple[str, ...] = ()
    confidence_scope: str


class Grounding(_ReportModel):
    claims: tuple[GroundedClaim, ...]


class McpAdapterMetadata(_ReportModel):
    materialized: bool = False
    target_schema: str | None = None
    manifest: str | None = None
    snapshot: str | None = None


class ReportManifest(_ReportModel):
    kind: Literal["marivo_analysis_report"] = "marivo_analysis_report"
    manifest_version: Literal[1] = 1
    report_id: str
    export_id: str
    title: str
    created_at: str
    marivo_version: str
    exported_by: str | None = None
    exported_at: str | None = None
    content_hash: str | None = None
    entrypoints: dict[str, str] = Field(default_factory=lambda: {"html": "index.html"})
    adapter_mcp: McpAdapterMetadata = Field(default_factory=McpAdapterMetadata)
    artifact_count: int
    evidence_status: EvidenceStatus
    data_policy: DataPolicy = Field(default_factory=DataPolicy)


class MarivoReportArtifact(_ReportModel):
    manifest: ReportManifest
    report_spec: ReportSpec
    flow: Flow
    grounding: Grounding
    datasets: dict[str, Dataset]
    evidence: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("datasets")
    @classmethod
    def validate_dataset_keys(cls, value: dict[str, Dataset]) -> dict[str, Dataset]:
        for key, dataset in value.items():
            if key != dataset.dataset_id:
                raise ValueError("datasets keys must match dataset_id")
        return value


class ReportPackageValidationIssue(_ReportModel):
    severity: Literal["error", "warning"]
    check: str
    message: str
    location: str | None = None


class ReportPackageValidationResult(_ReportModel):
    ok: bool
    issues: tuple[ReportPackageValidationIssue, ...] = ()


def export_report_json_schema(path: str | Path) -> None:
    """Write the public report artifact JSON Schema to ``path``."""
    schema = MarivoReportArtifact.model_json_schema()
    Path(path).write_text(
        json.dumps(schema, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
