from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError


def test_report_artifact_models_round_trip_json() -> None:
    from marivo.analysis.publish import (
        DataPolicy,
        Dataset,
        DatasetMetadata,
        Flow,
        FlowStep,
        GroundedClaim,
        Grounding,
        MarivoReportArtifact,
        ReportBlock,
        ReportManifest,
        ReportSection,
        ReportSpec,
        SourceProvenance,
    )

    dataset = Dataset(
        dataset_id="headline_metrics",
        metadata=DatasetMetadata(
            dataset_id="headline_metrics",
            grain="overall",
            row_count=1,
            truncated=False,
            source_artifacts=("artifact_observe_1",),
            source_provenance=SourceProvenance(
                generated_from="intent",
                query_summary="Observed revenue for the requested window.",
                semantic_refs=("sales.revenue",),
                sql_status="not_applicable",
                sql_reason="The source was a typed intent with no exposed SQL.",
            ),
            metric_definitions=("sales.revenue = sum(order_amount)",),
            filters=("region = 'west'",),
            data_policy=DataPolicy(),
        ),
        rows=({"metric": "revenue", "value": 125.0},),
    )
    artifact = MarivoReportArtifact(
        manifest=ReportManifest(
            report_id="revenue_review",
            export_id="exp_20260605_120000",
            title="Revenue Review",
            created_at="2026-06-05T12:00:00Z",
            marivo_version="0.0.test",
            artifact_count=1,
            evidence_status="complete",
            data_policy=DataPolicy(),
        ),
        report_spec=ReportSpec(
            title="Revenue Review",
            sections=(
                ReportSection(
                    section_id="exec",
                    section_type="executive_summary",
                    title="Executive Summary",
                    blocks=(
                        ReportBlock(
                            block_id="exec_text",
                            block_type="markdown",
                            text="Revenue is up in the reviewed window.",
                        ),
                        ReportBlock(
                            block_id="kpis",
                            block_type="metric_strip",
                            dataset_id="headline_metrics",
                            value_refs=("headline_metrics[0].value",),
                            narrative_ref="exec_text",
                        ),
                    ),
                ),
            ),
        ),
        flow=Flow(
            steps=(
                FlowStep(
                    step_id="step_observe",
                    order=1,
                    kind="intent",
                    description="Observe revenue for the requested window.",
                    output_artifacts=("artifact_observe_1",),
                    semantic_refs=("sales.revenue",),
                    evidence_status="complete",
                    query_summary="Observed revenue for the requested window.",
                ),
            ),
        ),
        grounding=Grounding(
            claims=(
                GroundedClaim(
                    claim_id="claim_revenue_up",
                    text_template="Revenue is {value}.",
                    value_refs=("headline_metrics[0].value",),
                    section_id="exec",
                    grounding_type="evidence_backed",
                    evidence_status="complete",
                    supporting_artifacts=("artifact_observe_1",),
                    supporting_steps=("step_observe",),
                    supporting_datasets=("headline_metrics",),
                    source_refs=("sales.revenue",),
                    confidence_scope="Requested window only.",
                ),
            ),
        ),
        datasets={"headline_metrics": dataset},
        evidence={"artifact_observe_1": {"summary": "Revenue observation."}},
    )

    payload = artifact.model_dump(mode="json")
    restored = MarivoReportArtifact.model_validate(json.loads(json.dumps(payload)))

    assert restored.manifest.kind == "marivo_analysis_report"
    assert restored.report_spec.sections[0].blocks[1].dataset_id == "headline_metrics"
    assert restored.datasets["headline_metrics"].rows[0]["value"] == 125.0


def test_export_report_json_schema_writes_schema(tmp_path) -> None:
    from marivo.analysis.publish import export_report_json_schema

    output = tmp_path / "marivo-report-artifact.schema.json"
    export_report_json_schema(output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["title"] == "MarivoReportArtifact"
    assert "$defs" in payload
    assert "ReportManifest" in payload["$defs"]


@pytest.mark.parametrize(
    "non_finite_value",
    [math.nan, math.inf, -math.inf],
)
def test_dataset_rejects_non_finite_float_row_values(non_finite_value: float) -> None:
    from marivo.analysis.publish import Dataset, DatasetMetadata, SourceProvenance

    metadata = DatasetMetadata(
        dataset_id="headline_metrics",
        grain="overall",
        row_count=1,
        truncated=False,
        source_provenance=SourceProvenance(
            generated_from="intent",
            query_summary="Observed revenue for the requested window.",
            sql_status="not_applicable",
            sql_reason="The source was a typed intent with no exposed SQL.",
        ),
    )

    with pytest.raises(ValidationError):
        Dataset(
            dataset_id="headline_metrics",
            metadata=metadata,
            rows=({"metric": "revenue", "value": non_finite_value},),
        )


def test_dataset_accepts_json_scalar_row_values() -> None:
    from marivo.analysis.publish import Dataset, DatasetMetadata, SourceProvenance

    metadata = DatasetMetadata(
        dataset_id="headline_metrics",
        grain="overall",
        row_count=1,
        truncated=False,
        source_provenance=SourceProvenance(
            generated_from="intent",
            query_summary="Observed revenue for the requested window.",
            sql_status="not_applicable",
            sql_reason="The source was a typed intent with no exposed SQL.",
        ),
    )

    dataset = Dataset(
        dataset_id="headline_metrics",
        metadata=metadata,
        rows=(
            {
                "name": "revenue",
                "count": 1,
                "value": 125.0,
                "visible": True,
                "note": None,
            },
        ),
    )

    assert dataset.rows[0]["visible"] is True


def test_report_block_supports_renderer_agnostic_visual_hints() -> None:
    from marivo.analysis.publish import (
        ReportBlock,
        ReportChartSpec,
        ReportColumn,
        ReportMetric,
    )

    block = ReportBlock(
        block_id="trend",
        block_type="chart",
        title="Revenue Trend",
        subtitle="Revenue increased over the reviewed window.",
        dataset_id="trend_rows",
        narrative_ref="trend_text",
        chart=ReportChartSpec(
            type="line",
            fields={"x": "date", "y": "revenue"},
            options={"showLegend": False},
        ),
        columns=(
            ReportColumn(key="date", label="Date", type="date"),
            ReportColumn(key="revenue", label="Revenue", type="number", format="currency"),
        ),
        metrics=(
            ReportMetric(
                label="Revenue",
                value_ref="headline_metrics[0].value",
                format="currency",
                signed=False,
            ),
        ),
    )

    payload = block.model_dump(mode="json")

    assert payload["title"] == "Revenue Trend"
    assert payload["chart"]["type"] == "line"
    assert payload["columns"][1]["format"] == "currency"
    assert payload["metrics"][0]["value_ref"] == "headline_metrics[0].value"


@pytest.mark.parametrize(
    "sql_status",
    ["not_applicable", "unavailable", "redacted"],
)
def test_source_provenance_rejects_sql_for_non_available_statuses(sql_status: str) -> None:
    from marivo.analysis.publish import SourceProvenance

    with pytest.raises(ValidationError):
        SourceProvenance(
            generated_from="intent",
            query_summary="Observed revenue for the requested window.",
            sql_status=sql_status,
            sql="select revenue from sales",
            sql_reason="SQL is not publishable.",
        )


def test_source_provenance_auto_populates_sql_reason_for_non_available() -> None:
    from marivo.analysis.publish import SourceProvenance

    source = SourceProvenance(
        generated_from="intent",
        query_summary="Observed revenue for the requested window.",
    )
    assert source.sql_status == "not_applicable"
    assert source.sql_reason == "No SQL was generated for this source."


@pytest.mark.parametrize("sql_status", ["not_applicable", "unavailable", "redacted"])
def test_source_provenance_auto_populates_sql_reason_for_explicit_non_available(
    sql_status: str,
) -> None:
    from marivo.analysis.publish import SourceProvenance

    source = SourceProvenance(
        generated_from="intent",
        query_summary="Observed revenue for the requested window.",
        sql_status=sql_status,
    )
    assert source.sql_reason == "No SQL was generated for this source."


def test_source_provenance_preserves_explicit_sql_reason() -> None:
    from marivo.analysis.publish import SourceProvenance

    source = SourceProvenance(
        generated_from="intent",
        query_summary="Observed revenue for the requested window.",
        sql_status="unavailable",
        sql_reason="SQL query timed out.",
    )
    assert source.sql_reason == "SQL query timed out."


def test_source_provenance_available_status_keeps_sql_reason_none() -> None:
    from marivo.analysis.publish import SourceProvenance

    source = SourceProvenance(
        generated_from="explore_ibis",
        query_summary="Revenue query.",
        sql_status="available",
        sql="select sum(revenue) from sales",
    )
    assert source.sql_reason is None


def test_source_provenance_sql_status_field_documents_constraints() -> None:
    from marivo.analysis.publish import SourceProvenance

    schema = SourceProvenance.model_json_schema()
    sql_status_prop = schema["properties"]["sql_status"]
    assert "available" in sql_status_prop["description"]
    assert "sql_reason" in sql_status_prop["description"]
