from __future__ import annotations

import pytest

from tests.test_analysis_report_artifact_validation import _valid_artifact


def _artifact_with_kpi_block_update(block_update, artifact=None):
    artifact = artifact or _valid_artifact()
    exec_section = artifact.report_spec.sections[0]
    kpi_block = exec_section.blocks[1].model_copy(update=block_update)
    exec_section = exec_section.model_copy(update={"blocks": (exec_section.blocks[0], kpi_block)})
    return artifact.model_copy(
        update={
            "report_spec": artifact.report_spec.model_copy(
                update={"sections": (exec_section, artifact.report_spec.sections[1])}
            )
        }
    )


def _artifact_with_metric_spec(*, artifact=None):
    from marivo.analysis.publish import ReportMetric

    return _artifact_with_kpi_block_update(
        {
            "title": "Headline Revenue",
            "subtitle": "Revenue for the reviewed window.",
            "metrics": (
                ReportMetric(
                    label="Revenue",
                    value_ref="headline_metrics[0].value",
                    format="currency",
                    signed=False,
                ),
            ),
        },
        artifact=artifact,
    )


def _artifact_with_metric_ref(value_ref: str, *, artifact=None):
    from marivo.analysis.publish import ReportMetric

    return _artifact_with_kpi_block_update(
        {
            "metrics": (
                ReportMetric(
                    label="Revenue",
                    value_ref=value_ref,
                    format="currency",
                    signed=False,
                ),
            ),
        },
        artifact=artifact,
    )


def _artifact_with_chart_and_table(*, sql_available: bool = True):
    from marivo.analysis.publish import (
        Dataset,
        ReportBlock,
        ReportChartSpec,
        ReportColumn,
        ReportSection,
    )

    artifact = _valid_artifact()
    headline_dataset = artifact.datasets["headline_metrics"]
    source_provenance_update = (
        {
            "query_summary": "Daily revenue trend by date.",
            "datasource_refs": ("sales.orders",),
            "sql_status": "available",
            "sql": "select date, revenue, orders from trend_rows",
            "sql_reason": None,
        }
        if sql_available
        else {
            "query_summary": "Daily revenue trend by date.",
            "datasource_refs": ("sales.orders",),
            "sql_status": "not_applicable",
            "sql": None,
            "sql_reason": "Typed intent did not expose SQL.",
        }
    )
    trend_dataset = Dataset(
        dataset_id="trend_rows",
        metadata=headline_dataset.metadata.model_copy(
            update={
                "dataset_id": "trend_rows",
                "grain": "daily",
                "row_count": 2,
                "source_artifacts": ("artifact_observe_1",),
                "source_provenance": headline_dataset.metadata.source_provenance.model_copy(
                    update=source_provenance_update
                ),
            }
        ),
        rows=(
            {"date": "2026-06-01", "revenue": 100.0, "orders": 10},
            {"date": "2026-06-02", "revenue": 125.0, "orders": 12},
        ),
    )
    finding_section = ReportSection(
        section_id="trend",
        section_type="finding",
        title="Revenue Trend",
        blocks=(
            ReportBlock(
                block_id="trend_text",
                block_type="markdown",
                text="Daily revenue rose from 100 to 125.",
            ),
            ReportBlock(
                block_id="trend_chart",
                block_type="chart",
                title="Revenue by Date",
                subtitle="Daily revenue rose from 100 to 125.",
                dataset_id="trend_rows",
                narrative_ref="trend_text",
                chart=ReportChartSpec(
                    type="line",
                    fields={"x": "date", "y": "revenue"},
                    options={"showLegend": False},
                ),
            ),
            ReportBlock(
                block_id="trend_table",
                block_type="table",
                title="Daily Revenue Detail",
                dataset_id="trend_rows",
                narrative_ref="trend_text",
                columns=(
                    ReportColumn(key="date", label="Date", type="date"),
                    ReportColumn(
                        key="revenue",
                        label="Revenue",
                        type="number",
                        format="currency",
                    ),
                    ReportColumn(
                        key="orders",
                        label="Orders",
                        type="number",
                        format="number",
                    ),
                ),
            ),
        ),
    )
    return artifact.model_copy(
        update={
            "datasets": {
                **artifact.datasets,
                "trend_rows": trend_dataset,
            },
            "report_spec": artifact.report_spec.model_copy(
                update={
                    "sections": (
                        artifact.report_spec.sections[0],
                        finding_section,
                        artifact.report_spec.sections[1],
                    )
                }
            ),
        }
    )


def test_to_mcp_artifact_payload_maps_title_snapshot_and_sources() -> None:
    from marivo.analysis.publish import to_mcp_artifact_payload

    artifact = _artifact_with_chart_and_table()
    payload = to_mcp_artifact_payload(artifact)

    assert payload["manifest"]["version"] == 1
    assert payload["manifest"]["surface"] == "report"
    assert payload["manifest"]["title"] == "Revenue Review"
    assert payload["manifest"]["blocks"][0] == {
        "id": "title",
        "type": "markdown",
        "body": "# Revenue Review",
    }
    assert payload["snapshot"]["version"] == 1
    assert payload["snapshot"]["status"] == "ready"
    assert payload["snapshot"]["datasets"]["headline_metrics"] == [
        {"metric": "revenue", "value": 125.0}
    ]
    assert payload["snapshot"]["datasets"]["trend_rows"][1]["revenue"] == 125.0
    assert payload["sources"][0]["id"] == "headline_metrics"
    assert payload["sources"][0]["query"]["description"] == (
        "Observed revenue for the requested window."
    )
    assert "sql" not in payload["sources"][0]["query"]
    assert payload["package_info"]["source_report_id"] == "revenue_review"


def test_to_mcp_artifact_payload_rejects_invalid_report_artifact() -> None:
    from marivo.analysis.publish import to_mcp_artifact_payload

    artifact = _valid_artifact().model_copy(
        update={"report_spec": _valid_artifact().report_spec.model_copy(update={"sections": ()})}
    )

    with pytest.raises(ValueError, match="required_sections"):
        to_mcp_artifact_payload(artifact)


def test_to_mcp_artifact_payload_rejects_chartless_report_artifact() -> None:
    from marivo.analysis.publish import to_mcp_artifact_payload

    with pytest.raises(ValueError, match="requires at least one native chart block"):
        to_mcp_artifact_payload(_valid_artifact())


def test_to_mcp_artifact_payload_maps_markdown_sections_and_metric_strip() -> None:
    from marivo.analysis.publish import to_mcp_artifact_payload

    payload = to_mcp_artifact_payload(
        _artifact_with_metric_spec(artifact=_artifact_with_chart_and_table())
    )

    blocks = payload["manifest"]["blocks"]
    assert blocks[1] == {
        "id": "section-exec",
        "type": "markdown",
        "body": "## Executive Summary",
    }
    assert blocks[2] == {
        "id": "exec_text",
        "type": "markdown",
        "body": "Revenue is up in the reviewed window.",
    }
    assert blocks[3] == {
        "id": "kpis",
        "type": "metric-strip",
        "cardIds": ["kpis-card-0"],
    }
    assert payload["manifest"]["cards"] == [
        {
            "id": "kpis-card-0",
            "description": "Revenue for the reviewed window.",
            "dataset": "headline_metrics",
            "metrics": [
                {
                    "label": "Revenue",
                    "field": "value",
                    "format": "currency",
                    "signed": False,
                }
            ],
        }
    ]


def test_to_mcp_artifact_payload_rejects_cross_dataset_metric_refs() -> None:
    from marivo.analysis.publish import to_mcp_artifact_payload

    artifact = _valid_artifact()
    headline_dataset = artifact.datasets["headline_metrics"]
    other_dataset = headline_dataset.model_copy(
        update={
            "dataset_id": "other_metrics",
            "metadata": headline_dataset.metadata.model_copy(
                update={"dataset_id": "other_metrics"}
            ),
        }
    )
    artifact = artifact.model_copy(
        update={"datasets": {"headline_metrics": headline_dataset, "other_metrics": other_dataset}}
    )

    with pytest.raises(ValueError, match="must reference block dataset"):
        to_mcp_artifact_payload(
            _artifact_with_metric_ref("other_metrics[0].value", artifact=artifact)
        )


def test_to_mcp_artifact_payload_rejects_nonzero_row_metric_refs() -> None:
    from marivo.analysis.publish import to_mcp_artifact_payload

    artifact = _valid_artifact()
    headline_dataset = artifact.datasets["headline_metrics"]
    headline_dataset = headline_dataset.model_copy(
        update={
            "metadata": headline_dataset.metadata.model_copy(update={"row_count": 2}),
            "rows": (
                {"metric": "revenue", "value": 125.0},
                {"metric": "revenue", "value": 150.0},
            ),
        }
    )
    artifact = artifact.model_copy(
        update={"datasets": {**artifact.datasets, "headline_metrics": headline_dataset}}
    )

    with pytest.raises(ValueError, match="row 0"):
        to_mcp_artifact_payload(
            _artifact_with_metric_ref("headline_metrics[1].value", artifact=artifact)
        )


def test_to_mcp_artifact_payload_preserves_multiple_legacy_value_refs() -> None:
    from marivo.analysis.publish import to_mcp_artifact_payload

    artifact = _artifact_with_chart_and_table()
    headline_dataset = artifact.datasets["headline_metrics"]
    headline_dataset = headline_dataset.model_copy(
        update={
            "rows": ({"metric": "revenue", "value": 125.0, "order_count": 12},),
        }
    )
    artifact = artifact.model_copy(
        update={"datasets": {**artifact.datasets, "headline_metrics": headline_dataset}}
    )
    artifact = _artifact_with_kpi_block_update(
        {
            "title": "Headline Revenue",
            "value_refs": (
                "headline_metrics[0].value",
                "headline_metrics[0].order_count",
            ),
        },
        artifact=artifact,
    )

    payload = to_mcp_artifact_payload(artifact)

    assert payload["manifest"]["cards"] == [
        {
            "id": "kpis-card-0",
            "description": None,
            "dataset": "headline_metrics",
            "metrics": [
                {
                    "label": "Headline Revenue",
                    "field": "value",
                    "format": "compact",
                    "signed": False,
                },
                {
                    "label": "order_count",
                    "field": "order_count",
                    "format": "compact",
                    "signed": False,
                },
            ],
        }
    ]


def test_to_mcp_artifact_payload_maps_native_chart_and_table() -> None:
    from marivo.analysis.publish import to_mcp_artifact_payload

    payload = to_mcp_artifact_payload(_artifact_with_chart_and_table())

    assert payload["manifest"]["charts"] == [
        {
            "id": "trend_chart",
            "title": "Revenue by Date",
            "subtitle": "Daily revenue rose from 100 to 125.",
            "type": "line",
            "dataset": "trend_rows",
            "sourceId": "trend_rows",
            "encodings": {
                "x": {"field": "date"},
                "y": {"field": "revenue"},
            },
            "surface": {"options": {"showLegend": False}},
        }
    ]
    assert payload["manifest"]["tables"] == [
        {
            "id": "trend_table",
            "title": "Daily Revenue Detail",
            "subtitle": None,
            "dataset": "trend_rows",
            "sourceId": "trend_rows",
            "columns": [
                {"field": "date", "label": "Date", "type": "date", "format": None},
                {
                    "field": "revenue",
                    "label": "Revenue",
                    "type": "number",
                    "format": "currency",
                },
                {
                    "field": "orders",
                    "label": "Orders",
                    "type": "number",
                    "format": "number",
                },
            ],
        }
    ]
    assert {"id": "trend_chart", "type": "chart", "chartId": "trend_chart"} in payload["manifest"][
        "blocks"
    ]
    assert {"id": "trend_table", "type": "table", "tableId": "trend_table"} in payload["manifest"][
        "blocks"
    ]
    assert payload["snapshot"]["datasets"]["trend_rows"][1]["revenue"] == 125.0
    assert _source_by_id(payload["sources"], "trend_rows")["query"]["sql"] == (
        "select date, revenue, orders from trend_rows"
    )


def test_to_mcp_artifact_payload_avoids_generated_block_id_collisions() -> None:
    from marivo.analysis.publish import ReportBlock, to_mcp_artifact_payload

    artifact = _artifact_with_chart_and_table()
    exec_section = artifact.report_spec.sections[0]
    artifact = artifact.model_copy(
        update={
            "report_spec": artifact.report_spec.model_copy(
                update={
                    "sections": (
                        exec_section.model_copy(
                            update={
                                "blocks": (
                                    ReportBlock(
                                        block_id="title",
                                        block_type="markdown",
                                        text="Authored title block.",
                                    ),
                                    ReportBlock(
                                        block_id="section-exec",
                                        block_type="markdown",
                                        text="Authored section block.",
                                    ),
                                    *exec_section.blocks,
                                )
                            }
                        ),
                        *artifact.report_spec.sections[1:],
                    )
                }
            )
        }
    )

    payload = to_mcp_artifact_payload(artifact)

    block_ids = [block["id"] for block in payload["manifest"]["blocks"]]
    assert block_ids[0] == "title-1"
    assert block_ids[1] == "section-exec-1"
    assert "title" in block_ids
    assert "section-exec" in block_ids
    assert len(block_ids) == len(set(block_ids))


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"y": "revenue"}, r"chart block 'trend_chart'.*x/y encodings"),
        ({"x": "date"}, r"chart block 'trend_chart'.*x/y encodings"),
    ],
)
def test_to_mcp_artifact_payload_rejects_missing_chart_xy_encodings(
    fields: dict[str, str],
    match: str,
) -> None:
    from marivo.analysis.publish import ReportChartSpec, to_mcp_artifact_payload

    artifact = _artifact_with_chart_and_table()
    trend_section = artifact.report_spec.sections[1]
    chart_block = trend_section.blocks[1]
    trend_section = trend_section.model_copy(
        update={
            "blocks": (
                trend_section.blocks[0],
                chart_block.model_copy(
                    update={
                        "chart": ReportChartSpec(
                            type="line",
                            fields=fields,
                        )
                    }
                ),
                trend_section.blocks[2],
            )
        }
    )
    artifact = artifact.model_copy(
        update={
            "report_spec": artifact.report_spec.model_copy(
                update={
                    "sections": (
                        artifact.report_spec.sections[0],
                        trend_section,
                        artifact.report_spec.sections[2],
                    )
                }
            )
        }
    )

    with pytest.raises(ValueError, match=match):
        to_mcp_artifact_payload(artifact)


def _source_by_id(sources, source_id: str):
    return next(source for source in sources if source["id"] == source_id)


def test_to_mcp_artifact_payload_rejects_native_visual_blocks_without_sql() -> None:
    from marivo.analysis.publish import to_mcp_artifact_payload

    with pytest.raises(
        ValueError,
        match=(
            r"chart block 'trend_chart'.*native MCP chart/table blocks require "
            r"available SQL provenance"
        ),
    ):
        to_mcp_artifact_payload(_artifact_with_chart_and_table(sql_available=False))


def test_to_mcp_artifact_payload_maps_partial_status_and_visible_caveat() -> None:
    from marivo.analysis.publish import to_mcp_artifact_payload

    artifact = _artifact_with_chart_and_table()
    artifact = artifact.model_copy(
        update={
            "manifest": artifact.manifest.model_copy(update={"evidence_status": "partial"}),
            "flow": artifact.flow.model_copy(
                update={
                    "steps": (
                        artifact.flow.steps[0].model_copy(update={"evidence_status": "partial"}),
                    )
                }
            ),
        }
    )

    payload = to_mcp_artifact_payload(artifact)

    blocks = payload["manifest"]["blocks"]
    assert payload["snapshot"]["status"] == "partial"
    assert blocks[0]["id"] == "title"
    assert blocks[1]["id"] == "evidence-status"
    assert blocks[2]["id"] == "section-exec"
    assert {
        "id": "evidence-status",
        "type": "markdown",
        "body": "## Evidence Status\nThis Marivo report has `partial` evidence. Review caveats and source details before acting on the recommendations.",
    } in blocks


def test_to_mcp_artifact_payload_avoids_evidence_status_id_collision() -> None:
    from marivo.analysis.publish import ReportBlock, to_mcp_artifact_payload

    artifact = _artifact_with_chart_and_table()
    exec_section = artifact.report_spec.sections[0]
    artifact = artifact.model_copy(
        update={
            "manifest": artifact.manifest.model_copy(update={"evidence_status": "partial"}),
            "flow": artifact.flow.model_copy(
                update={
                    "steps": (
                        artifact.flow.steps[0].model_copy(update={"evidence_status": "partial"}),
                    )
                }
            ),
            "report_spec": artifact.report_spec.model_copy(
                update={
                    "sections": (
                        exec_section.model_copy(
                            update={
                                "blocks": (
                                    ReportBlock(
                                        block_id="evidence-status",
                                        block_type="markdown",
                                        text="Authored evidence status details.",
                                    ),
                                    *exec_section.blocks,
                                )
                            }
                        ),
                        *artifact.report_spec.sections[1:],
                    )
                }
            ),
        }
    )

    payload = to_mcp_artifact_payload(artifact)

    blocks = payload["manifest"]["blocks"]
    block_ids = [block["id"] for block in blocks]
    assert blocks[1]["id"] == "adapter-evidence-status"
    assert len(block_ids) == len(set(block_ids))


def test_to_mcp_artifact_payload_preserves_available_sql_source() -> None:
    from marivo.analysis.publish import Dataset, SourceProvenance, to_mcp_artifact_payload

    artifact = _artifact_with_chart_and_table()
    artifact = artifact.model_copy(
        update={
            "flow": artifact.flow.model_copy(
                update={
                    "steps": (
                        *artifact.flow.steps,
                        artifact.flow.steps[0].model_copy(
                            update={
                                "step_id": "step_explore",
                                "order": 2,
                                "kind": "explore_ibis",
                                "output_artifacts": ("artifact_explore_1",),
                            }
                        ),
                    )
                }
            )
        }
    )
    dataset = artifact.datasets["headline_metrics"]
    dataset = Dataset(
        dataset_id=dataset.dataset_id,
        metadata=dataset.metadata.model_copy(
            update={
                "source_artifacts": ("artifact_explore_1",),
                "source_provenance": SourceProvenance(
                    generated_from="explore_ibis",
                    query_summary="Revenue query executed in DuckDB.",
                    datasource_refs=("duckdb.main.fact_orders",),
                    semantic_refs=("sales.revenue",),
                    sql_status="available",
                    sql="select sum(order_amount) as value from fact_orders",
                ),
            }
        ),
        rows=dataset.rows,
    )
    artifact = artifact.model_copy(
        update={"datasets": {**artifact.datasets, "headline_metrics": dataset}}
    )

    payload = to_mcp_artifact_payload(artifact)

    source = payload["manifest"]["sources"][0]
    assert source["query"]["language"] == "sql"
    assert source["query"]["sql"] == "select sum(order_amount) as value from fact_orders"
    assert source["query"]["tables_used"] == ["duckdb.main.fact_orders"]


def test_materialize_mcp_adapter_writes_payload_and_updates_manifest(tmp_path) -> None:
    import json

    from marivo.analysis.publish import load_report_artifact, materialize_mcp_adapter

    artifact = _artifact_with_chart_and_table()
    updated = materialize_mcp_adapter(
        artifact,
        tmp_path,
        target_schema="data-analytics-artifact-v1-test",
    )

    manifest_path = tmp_path / "adapters" / "mcp" / "manifest.json"
    snapshot_path = tmp_path / "adapters" / "mcp" / "snapshot.json"
    package_info_path = tmp_path / "adapters" / "mcp" / "package_info.json"
    sources_path = tmp_path / "adapters" / "mcp" / "sources.json"

    assert manifest_path.is_file()
    assert snapshot_path.is_file()
    assert package_info_path.is_file()
    assert sources_path.is_file()
    assert updated.manifest.adapter_mcp.materialized is True
    assert updated.manifest.adapter_mcp.target_schema == "data-analytics-artifact-v1-test"
    assert updated.manifest.adapter_mcp.manifest == "adapters/mcp/manifest.json"
    assert updated.manifest.adapter_mcp.snapshot == "adapters/mcp/snapshot.json"

    restored = load_report_artifact(tmp_path)
    assert restored.manifest.adapter_mcp.materialized is True
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["title"] == "Revenue Review"
    assert json.loads(snapshot_path.read_text(encoding="utf-8"))["status"] == "ready"


def test_materialize_mcp_adapter_does_not_write_adapter_payload_if_package_write_fails(
    tmp_path,
) -> None:
    from marivo.analysis.publish import materialize_mcp_adapter

    artifact = _artifact_with_chart_and_table().model_copy(
        update={"evidence": {"../escape": {"summary": "bad"}}}
    )

    with pytest.raises(ValueError):
        materialize_mcp_adapter(artifact, tmp_path)

    assert not (tmp_path / "adapters" / "mcp" / "manifest.json").exists()


def test_materialize_mcp_adapter_keeps_manifest_unmaterialized_if_adapter_write_fails(
    monkeypatch,
    tmp_path,
) -> None:
    import marivo.analysis.publish.report_mcp_adapter as report_mcp_adapter
    from marivo.analysis.publish import load_report_artifact, materialize_mcp_adapter

    original_write_json = report_mcp_adapter._write_json

    def fail_on_snapshot(path, payload):
        if path.name == "snapshot.json":
            raise OSError("adapter write failed")
        original_write_json(path, payload)

    monkeypatch.setattr(report_mcp_adapter, "_write_json", fail_on_snapshot)

    with pytest.raises(OSError, match="adapter write failed"):
        materialize_mcp_adapter(_artifact_with_chart_and_table(), tmp_path)

    restored = load_report_artifact(tmp_path)
    assert restored.manifest.adapter_mcp.materialized is False
    assert restored.manifest.adapter_mcp.target_schema is None
    assert (tmp_path / "adapters" / "mcp" / "manifest.json").is_file()
    assert not (tmp_path / "adapters" / "mcp" / "snapshot.json").exists()
