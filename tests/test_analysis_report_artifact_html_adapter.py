from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from marivo.analysis.publish import MarivoReportArtifact
from tests.test_analysis_report_artifact_mcp_adapter import (
    _artifact_with_chart_and_table,
    _artifact_with_metric_spec,
)
from tests.test_analysis_report_artifact_validation import _valid_artifact


def _html_artifact():
    artifact = _artifact_with_chart_and_table()
    updated = _artifact_with_metric_spec(artifact=artifact)
    if len(updated.report_spec.sections) == len(artifact.report_spec.sections):
        return updated
    return updated.model_copy(
        update={
            "report_spec": updated.report_spec.model_copy(
                update={
                    "sections": (
                        *updated.report_spec.sections,
                        *artifact.report_spec.sections[len(updated.report_spec.sections) :],
                    )
                }
            )
        }
    )


def test_to_html_report_payload_maps_report_structure_and_frozen_data() -> None:
    from marivo.analysis.publish import to_html_report_payload

    payload = to_html_report_payload(_html_artifact())

    assert payload["title"] == "Revenue Review"
    assert payload["report_id"] == "revenue_review"
    assert payload["evidence_status"] == "complete"
    assert payload["sections"][0]["id"] == "exec"
    assert payload["sections"][0]["blocks"][0]["text"] == ("Revenue is up in the reviewed window.")
    assert payload["sections"][0]["blocks"][1]["metrics"] == [
        {
            "label": "Revenue",
            "value_ref": "headline_metrics[0].value",
            "value": 125.0,
            "formatted_value": "$125",
            "format": "currency",
            "signed": False,
        }
    ]
    assert payload["sections"][1]["blocks"][1]["chart"] == {
        "type": "line",
        "fields": {"x": "date", "y": "revenue"},
        "options": {"showLegend": False},
    }
    assert payload["datasets"]["trend_rows"]["rows"][1]["revenue"] == 125.0
    assert payload["datasets"]["trend_rows"]["metadata"]["grain"] == "daily"
    assert payload["claims"][0]["id"] == "claim_revenue_up"
    assert payload["claims"][0]["supporting_steps"] == ["step_observe"]
    assert payload["flow_steps"][0]["id"] == "step_observe"
    assert payload["sources"][0]["id"] == "headline_metrics"
    assert payload["sources"][0]["query"]["sql_status"] == "not_applicable"


def test_to_html_report_payload_preserves_available_sql_source() -> None:
    from marivo.analysis.publish import to_html_report_payload

    payload = to_html_report_payload(_html_artifact())

    trend_source = next(source for source in payload["sources"] if source["id"] == "trend_rows")
    assert trend_source["query"]["language"] == "sql"
    assert trend_source["query"]["sql"] == "select date, revenue, orders from trend_rows"
    assert trend_source["query"]["tables_used"] == ["sales.orders"]


def test_to_html_report_payload_rejects_invalid_artifact() -> None:
    from marivo.analysis.publish import to_html_report_payload

    invalid = _valid_artifact().model_copy(
        update={"report_spec": _valid_artifact().report_spec.model_copy(update={"sections": ()})}
    )

    with pytest.raises(ValueError, match="report artifact is not valid for HTML adapter"):
        to_html_report_payload(invalid)


def test_to_html_report_payload_rejects_bad_metric_value_ref() -> None:
    from marivo.analysis.publish import ReportMetric, to_html_report_payload

    artifact = _html_artifact()
    exec_section = artifact.report_spec.sections[0]
    kpi_block = exec_section.blocks[1].model_copy(
        update={
            "metrics": (
                ReportMetric(
                    label="Revenue",
                    value_ref="headline_metrics[99].value",
                    format="currency",
                ),
            )
        }
    )
    exec_section = exec_section.model_copy(update={"blocks": (exec_section.blocks[0], kpi_block)})
    artifact = artifact.model_copy(
        update={
            "report_spec": artifact.report_spec.model_copy(
                update={
                    "sections": (
                        exec_section,
                        artifact.report_spec.sections[1],
                        artifact.report_spec.sections[2],
                    )
                }
            )
        }
    )

    with pytest.raises(ValueError, match="references missing row 99"):
        to_html_report_payload(artifact)


def test_to_html_report_payload_rejects_unserializable_chart_options() -> None:
    from marivo.analysis.publish import ReportChartSpec, to_html_report_payload

    artifact = _html_artifact()
    trend_section = artifact.report_spec.sections[1]
    chart_block = trend_section.blocks[1].model_copy(
        update={
            "chart": ReportChartSpec(
                type="line",
                fields={"x": "date", "y": "revenue"},
                options={"bad": object()},
            )
        }
    )
    trend_section = trend_section.model_copy(
        update={
            "blocks": (
                trend_section.blocks[0],
                chart_block,
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

    with pytest.raises(
        ValueError,
        match="report artifact payload is not valid for HTML adapter",
    ):
        to_html_report_payload(artifact)


def test_to_html_report_payload_rejects_unserializable_evidence_payload() -> None:
    from marivo.analysis.publish import to_html_report_payload

    artifact = _html_artifact().model_copy(
        update={"evidence": {"artifact_observe_1": {"payload": object()}}}
    )

    with pytest.raises(
        ValueError,
        match="report artifact payload is not valid for HTML adapter",
    ):
        to_html_report_payload(artifact)


def test_to_html_report_payload_rejects_non_finite_evidence_payload() -> None:
    from marivo.analysis.publish import to_html_report_payload

    artifact = _html_artifact().model_copy(
        update={"evidence": {"artifact_observe_1": {"payload": float("nan")}}}
    )

    with pytest.raises(
        ValueError,
        match="report artifact payload is not valid for HTML adapter",
    ):
        to_html_report_payload(artifact)


def test_render_report_html_outputs_answer_first_document() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_html_artifact())

    assert rendered.startswith("<!doctype html>")
    assert '<main id="report"' in rendered
    assert "<h1>Revenue Review</h1>" in rendered
    assert rendered.index("Executive Summary") < rendered.index("Revenue Trend")
    assert rendered.index("Revenue Trend") < rendered.index("Caveats")
    assert 'id="section-exec"' in rendered
    assert 'href="#section-trend"' in rendered
    assert "Revenue is up in the reviewed window." in rendered
    assert '<script type="application/json" id="marivo-report-data">' in rendered
    assert "https://" not in rendered
    assert "http://" not in rendered


def test_render_report_html_shows_partial_evidence_notice() -> None:
    from marivo.analysis.publish import render_report_html

    artifact = _html_artifact()
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
            "grounding": artifact.grounding.model_copy(
                update={
                    "claims": (
                        artifact.grounding.claims[0].model_copy(
                            update={"evidence_status": "partial"}
                        ),
                    )
                }
            ),
        }
    )

    rendered = render_report_html(artifact)

    assert "Evidence status: partial" in rendered
    assert "Review caveats and source details before acting on the recommendations." in rendered


def test_render_report_html_renders_metrics_charts_and_tables_from_datasets() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_html_artifact())

    assert 'class="metric-grid"' in rendered
    assert 'data-value-ref="headline_metrics[0].value"' in rendered
    assert "$125" in rendered
    assert 'id="chart-trend_chart"' in rendered
    assert "Revenue by Date" in rendered
    assert "<svg" in rendered
    assert "<polyline" in rendered
    assert 'id="table-trend_table"' in rendered
    assert '<table data-sortable="true">' in rendered
    assert '<th data-sortable="true">Date</th>' in rendered
    assert '<td data-sort-value="125.0">$125</td>' in rendered


def test_render_report_html_rejects_chart_with_missing_dataset_field() -> None:
    from marivo.analysis.publish import ReportChartSpec, render_report_html

    artifact = _html_artifact()
    trend_section = artifact.report_spec.sections[1]
    bad_chart = trend_section.blocks[1].model_copy(
        update={
            "chart": ReportChartSpec(
                type="line",
                fields={"x": "date", "y": "missing_revenue"},
            )
        }
    )
    trend_section = trend_section.model_copy(
        update={"blocks": (trend_section.blocks[0], bad_chart, trend_section.blocks[2])}
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

    with pytest.raises(
        ValueError,
        match=r"chart block 'trend_chart'.*missing field 'missing_revenue'",
    ):
        render_report_html(artifact)


def test_render_report_html_rejects_unsupported_chart_type() -> None:
    from marivo.analysis.publish import ReportChartSpec, render_report_html

    artifact = _html_artifact()
    trend_section = artifact.report_spec.sections[1]
    pie_chart = trend_section.blocks[1].model_copy(
        update={
            "chart": ReportChartSpec(
                type="pie",
                fields={"x": "date", "y": "revenue"},
            )
        }
    )
    trend_section = trend_section.model_copy(
        update={"blocks": (trend_section.blocks[0], pie_chart, trend_section.blocks[2])}
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

    with pytest.raises(
        ValueError,
        match=r"chart block 'trend_chart' does not support chart type 'pie'",
    ):
        render_report_html(artifact)


def test_render_report_html_renders_mixed_sign_bar_chart_around_zero_baseline() -> None:
    from marivo.analysis.publish import ReportChartSpec, render_report_html

    artifact = _html_artifact()
    trend_dataset = artifact.datasets["trend_rows"].model_copy(
        update={
            "metadata": artifact.datasets["trend_rows"].metadata.model_copy(
                update={"row_count": 3}
            ),
            "rows": (
                {"date": "negative", "revenue": -50.0, "orders": 4},
                {"date": "zero", "revenue": 0.0, "orders": 0},
                {"date": "positive", "revenue": 100.0, "orders": 8},
            ),
        }
    )
    trend_section = artifact.report_spec.sections[1]
    bar_chart = trend_section.blocks[1].model_copy(
        update={
            "chart": ReportChartSpec(
                type="bar",
                fields={"x": "date", "y": "revenue"},
            )
        }
    )
    trend_section = trend_section.model_copy(
        update={"blocks": (trend_section.blocks[0], bar_chart, trend_section.blocks[2])}
    )
    artifact = artifact.model_copy(
        update={
            "datasets": {**artifact.datasets, "trend_rows": trend_dataset},
            "report_spec": artifact.report_spec.model_copy(
                update={
                    "sections": (
                        artifact.report_spec.sections[0],
                        trend_section,
                        artifact.report_spec.sections[2],
                    )
                }
            ),
        }
    )

    rendered = render_report_html(artifact)

    rects = [
        (float(y), float(height))
        for y, height in re.findall(
            r'<rect class="chart-bar" x="[^"]+" y="([^"]+)" width="[^"]+" height="([^"]+)"[^>]*>.*?</rect>',
            rendered,
        )
    ]
    assert len(rects) == 3
    negative_y, negative_height = rects[0]
    zero_y, zero_height = rects[1]
    positive_y, positive_height = rects[2]
    assert negative_y == zero_y
    assert negative_height > 0
    assert zero_height == 0
    assert positive_y < zero_y
    assert positive_height > 0


def test_render_report_html_marks_sortable_table_headers() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_html_artifact())

    assert '<th data-sortable="true">Date</th>' in rendered
    assert '<th data-sortable="true">Revenue</th>' in rendered


def test_render_report_html_links_claims_steps_datasets_and_sources() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_html_artifact())

    assert 'id="claim-claim_revenue_up"' in rendered
    assert 'href="#step-step_observe"' in rendered
    assert 'href="#dataset-headline_metrics"' in rendered
    assert 'id="step-step_observe"' in rendered
    assert "Observe revenue for the requested window." in rendered
    assert 'id="source-trend_rows"' in rendered
    assert 'id="dataset-trend_rows"' in rendered
    assert "select date, revenue, orders from trend_rows" in rendered
    assert "sales.revenue = sum(order_amount)" in rendered
    assert "Revenue observation." in rendered


def test_render_report_html_keeps_audit_details_after_main_sections() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_html_artifact())

    assert rendered.index("Executive Summary") < rendered.index("Audit Trail")
    assert rendered.index("Revenue Trend") < rendered.index("Audit Trail")
    assert rendered.index("Caveats") < rendered.index("Audit Trail")


def test_materialize_html_adapter_writes_index_and_canonical_package(tmp_path) -> None:
    from marivo.analysis.publish.report_html_adapter import materialize_html_adapter
    from marivo.analysis.publish.report_package import load_report_artifact

    artifact = _html_artifact()
    updated = materialize_html_adapter(artifact, tmp_path)

    index_path = tmp_path / "index.html"
    assert index_path.is_file()
    rendered = index_path.read_text(encoding="utf-8")
    assert "<h1>Revenue Review</h1>" in rendered
    assert (tmp_path / "manifest.json").is_file()
    assert (tmp_path / "report_spec.json").is_file()
    assert (tmp_path / "flow.json").is_file()
    assert (tmp_path / "grounding.json").is_file()
    assert (tmp_path / "datasets" / "trend_rows.json").is_file()
    assert updated.manifest.entrypoints["html"] == "index.html"
    restored = load_report_artifact(tmp_path)
    assert restored.manifest.entrypoints["html"] == "index.html"
    assert restored.manifest.report_id == "revenue_review"


def test_materialize_html_adapter_preserves_existing_non_html_entrypoints(tmp_path) -> None:
    from marivo.analysis.publish.report_html_adapter import materialize_html_adapter

    artifact = _html_artifact()
    artifact = artifact.model_copy(
        update={
            "manifest": artifact.manifest.model_copy(
                update={"entrypoints": {"custom": "custom.json"}}
            )
        }
    )

    updated = materialize_html_adapter(artifact, tmp_path)

    assert updated.manifest.entrypoints == {
        "custom": "custom.json",
        "html": "index.html",
    }


def _artifact_with_inline_evidence_blocks(*, collapsed: bool = False):
    from marivo.analysis.publish import ReportBlock, ReportSection

    artifact = _html_artifact()
    evidence_section = ReportSection(
        section_id="evidence_detail",
        section_type="analysis_step",
        title="Evidence Detail",
        blocks=(
            ReportBlock(
                block_id="proof_revenue",
                block_type="claim_evidence",
                title="Why revenue is up",
                claim_refs=("claim_revenue_up",),
                collapsed_by_default=collapsed,
            ),
            ReportBlock(
                block_id="trace_observe",
                block_type="step_trace",
                title="Observation trace",
                step_refs=("step_observe",),
                collapsed_by_default=collapsed,
            ),
            ReportBlock(
                block_id="sql_trend",
                block_type="source_code",
                title="Trend SQL",
                dataset_id="trend_rows",
                source_refs=("trend_rows",),
                collapsed_by_default=collapsed,
            ),
        ),
    )
    return artifact.model_copy(
        update={
            "report_spec": artifact.report_spec.model_copy(
                update={"sections": (*artifact.report_spec.sections, evidence_section)}
            )
        }
    )


def test_render_report_html_renders_inline_claim_evidence_panel() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_artifact_with_inline_evidence_blocks())

    assert 'id="proof-claim_revenue_up"' in rendered
    assert "<summary>Revenue is {value}.</summary>" in rendered
    assert 'href="#step-step_observe">step_observe</a>' in rendered
    assert 'href="#dataset-headline_metrics">headline_metrics</a>' in rendered


def test_render_report_html_honors_collapsed_by_default_for_inline_blocks() -> None:
    from marivo.analysis.publish import render_report_html

    expanded = render_report_html(_artifact_with_inline_evidence_blocks(collapsed=False))
    collapsed = render_report_html(_artifact_with_inline_evidence_blocks(collapsed=True))

    assert '<details class="proof-panel" id="proof-claim_revenue_up" open>' in expanded
    assert '<details class="proof-panel" id="proof-claim_revenue_up">' in collapsed
    assert '<details class="proof-panel" id="proof-claim_revenue_up" open>' not in collapsed


def test_render_report_html_renders_inline_step_trace() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_artifact_with_inline_evidence_blocks())

    assert 'id="trace-step_observe"' in rendered
    assert 'href="#step-step_observe">step_observe</a>' in rendered
    assert 'href="#evidence-artifact_observe_1">artifact_observe_1</a>' in rendered


def test_render_report_html_renders_inline_source_code() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_artifact_with_inline_evidence_blocks())

    assert 'id="sourcecode-trend_rows"' in rendered
    assert '<details class="source-code" id="sourcecode-trend_rows" open>' in rendered
    assert "select date, revenue, orders from trend_rows" in rendered


def test_materialize_html_adapter_does_not_write_manifest_if_index_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from marivo.analysis.publish.report_html_adapter import materialize_html_adapter

    original_write_text = Path.write_text

    def fail_for_index(path: Path, data: str, *args: Any, **kwargs: Any) -> int:
        if path.name == "index.html":
            raise OSError("index write failed")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_for_index)

    with pytest.raises(OSError, match="index write failed"):
        materialize_html_adapter(_html_artifact(), tmp_path)

    assert not (tmp_path / "manifest.json").exists()


def test_render_report_html_adds_line_chart_tooltips() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_html_artifact())

    assert "<title>2026-06-01: 100.0</title>" in rendered
    assert "<title>2026-06-02: 125.0</title>" in rendered


def test_render_report_html_adds_bar_chart_tooltips() -> None:
    from marivo.analysis.publish import ReportChartSpec, render_report_html

    artifact = _html_artifact()
    trend_section = artifact.report_spec.sections[1]
    bar_chart = trend_section.blocks[1].model_copy(
        update={"chart": ReportChartSpec(type="bar", fields={"x": "date", "y": "revenue"})}
    )
    trend_section = trend_section.model_copy(
        update={"blocks": (trend_section.blocks[0], bar_chart, trend_section.blocks[2])}
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

    rendered = render_report_html(artifact)

    assert '<rect class="chart-bar"' in rendered
    assert "<title>2026-06-02: 125.0</title>" in rendered


def _artifact_with_long_table():
    artifact = _html_artifact()
    trend = artifact.datasets["trend_rows"]
    rows = tuple(
        {"date": f"2026-06-{day:02d}", "revenue": 100.0 + day, "orders": day}
        for day in range(1, 13)
    )
    long_trend = trend.model_copy(
        update={
            "metadata": trend.metadata.model_copy(update={"row_count": len(rows)}),
            "rows": rows,
        }
    )
    return artifact.model_copy(update={"datasets": {**artifact.datasets, "trend_rows": long_trend}})


def test_render_report_html_paginates_long_tables() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_artifact_with_long_table())

    assert 'data-page-size="10"' in rendered
    assert 'class="table-pager"' in rendered
    assert 'class="table-next"' in rendered
    assert 'class="table-prev"' in rendered


def test_render_report_html_does_not_paginate_short_tables() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_html_artifact())

    assert 'data-page-size="10"' not in rendered
    assert 'class="table-pager"' not in rendered


def test_render_report_html_includes_local_search() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_html_artifact())

    assert '<input id="report-search"' in rendered
    assert 'data-searchable="true"' in rendered
    assert 'getElementById("report-search")' in rendered


def _artifact_with_markdown(text: str):
    artifact = _html_artifact()
    section = artifact.report_spec.sections[0]
    block = section.blocks[0].model_copy(update={"text": text})
    section = section.model_copy(update={"blocks": (block, *section.blocks[1:])})
    return artifact.model_copy(
        update={
            "report_spec": artifact.report_spec.model_copy(
                update={"sections": (section, *artifact.report_spec.sections[1:])}
            )
        }
    )


def test_render_report_html_renders_inline_and_block_markdown() -> None:
    from marivo.analysis.publish import render_report_html

    text = (
        "Total **19,505,768** via `query_count`\n"
        "\n"
        "- bullet **bold**\n"
        "\n"
        "1. first\n"
        "2. second\n"
        "\n"
        "| A | B |\n"
        "|---|---|\n"
        "| `x` | y |\n"
    )

    rendered = render_report_html(_artifact_with_markdown(text))

    assert "<p>Total <strong>19,505,768</strong> via <code>query_count</code></p>" in rendered
    assert "<ul><li>bullet <strong>bold</strong></li></ul>" in rendered
    assert "<ol><li>first</li><li>second</li></ol>" in rendered
    assert (
        "<table><thead><tr><th>A</th><th>B</th></tr></thead>"
        "<tbody><tr><td><code>x</code></td><td>y</td></tr></tbody></table>"
    ) in rendered


def test_render_report_html_keeps_code_span_content_literal() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_artifact_with_markdown("see `a**b**c` here"))

    assert "<code>a**b**c</code>" in rendered
    assert "<strong>b</strong>" not in rendered


def _set_kpi_format(artifact, value_format: str):
    from marivo.analysis.publish import ReportMetric

    section = artifact.report_spec.sections[0]
    kpi = section.blocks[1].model_copy(
        update={
            "metrics": (
                ReportMetric(
                    label="Share",
                    value_ref="headline_metrics[0].value",
                    format=value_format,
                ),
            )
        }
    )
    section = section.model_copy(update={"blocks": (section.blocks[0], kpi)})
    return artifact.model_copy(
        update={
            "report_spec": artifact.report_spec.model_copy(
                update={"sections": (section, *artifact.report_spec.sections[1:])}
            )
        }
    )


def test_render_report_html_formats_percent_without_scaling() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_set_kpi_format(_html_artifact(), "percent"))

    assert "125%" in rendered
    assert "12,500%" not in rendered


def _artifact_with_trend_chart(rows, fields, *, chart_type="bar"):
    from marivo.analysis.publish import ReportChartSpec

    artifact = _html_artifact()
    trend = artifact.datasets["trend_rows"]
    dataset = trend.model_copy(
        update={
            "metadata": trend.metadata.model_copy(update={"row_count": len(rows)}),
            "rows": tuple(rows),
        }
    )
    trend_section = artifact.report_spec.sections[1]
    chart = trend_section.blocks[1].model_copy(
        update={"chart": ReportChartSpec(type=chart_type, fields=fields)}
    )
    trend_section = trend_section.model_copy(
        update={"blocks": (trend_section.blocks[0], chart, trend_section.blocks[2])}
    )
    return artifact.model_copy(
        update={
            "datasets": {**artifact.datasets, "trend_rows": dataset},
            "report_spec": artifact.report_spec.model_copy(
                update={
                    "sections": (
                        artifact.report_spec.sections[0],
                        trend_section,
                        artifact.report_spec.sections[2],
                    )
                }
            ),
        }
    )


def test_render_report_html_truncates_long_chart_labels() -> None:
    from marivo.analysis.publish import render_report_html

    rows = (
        {"date": "k8soneservice-oneservice", "revenue": 100.0, "orders": 1},
        {"date": "k8sdqc-dqc1-cluster", "revenue": 80.0, "orders": 2},
        {"date": "k8sbi-bi1-cluster", "revenue": 60.0, "orders": 3},
    )
    rendered = render_report_html(_artifact_with_trend_chart(rows, {"x": "date", "y": "revenue"}))

    # Full label is preserved in a hover title; the visible label is truncated.
    assert "<title>k8soneservice-oneservice</title>" in rendered
    assert "k8soneservice…" in rendered


def test_render_report_html_renders_grouped_series_bar_chart() -> None:
    from marivo.analysis.publish import render_report_html

    rows = (
        {"date": "Mon", "revenue": 10.0, "orders": 1, "series": "SELECT"},
        {"date": "Mon", "revenue": 4.0, "orders": 2, "series": "INSERT"},
        {"date": "Tue", "revenue": 8.0, "orders": 3, "series": "SELECT"},
        {"date": "Tue", "revenue": 3.0, "orders": 4, "series": "INSERT"},
    )
    rendered = render_report_html(
        _artifact_with_trend_chart(rows, {"x": "date", "y": "revenue", "series": "series"})
    )

    assert 'fill="#0f766e"' in rendered
    assert 'fill="#b45309"' in rendered
    assert ">SELECT</text>" in rendered
    assert ">INSERT</text>" in rendered
    assert "<title>Mon / SELECT: 10.0</title>" in rendered


def test_render_report_html_rejects_chart_with_duplicate_x_without_series() -> None:
    from marivo.analysis.publish import render_report_html

    rows = (
        {"date": "Mon", "revenue": 10.0, "orders": 1},
        {"date": "Mon", "revenue": 4.0, "orders": 2},
    )

    with pytest.raises(ValueError, match=r"chart block 'trend_chart' has duplicate x values"):
        render_report_html(_artifact_with_trend_chart(rows, {"x": "date", "y": "revenue"}))


def test_render_report_html_rejects_series_grouped_duplicate_rows() -> None:
    from marivo.analysis.publish import render_report_html

    rows = (
        {"date": "Mon", "revenue": 10.0, "orders": 1, "series": "SELECT"},
        {"date": "Mon", "revenue": 4.0, "orders": 2, "series": "SELECT"},
    )

    with pytest.raises(ValueError, match=r"duplicate rows for x='Mon' series='SELECT'"):
        render_report_html(
            _artifact_with_trend_chart(rows, {"x": "date", "y": "revenue", "series": "series"})
        )


def _with_language(artifact, language: str):
    return artifact.model_copy(
        update={"manifest": artifact.manifest.model_copy(update={"language": language})}
    )


def _zh_labels() -> dict[str, str]:
    import json
    from importlib import resources

    text = (resources.files("marivo.analysis.publish") / "locales" / "zh-Hans.json").read_text(
        encoding="utf-8"
    )
    return json.loads(text)


def test_render_report_html_defaults_to_english_audit_trail() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_html_artifact())

    assert '<html lang="en">' in rendered
    assert "<h2>Audit Trail</h2>" in rendered
    assert "Marivo Analysis Report" in rendered


def test_render_report_html_localizes_chrome_and_audit_trail() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_with_language(_html_artifact(), "zh-Hans"))
    labels = _zh_labels()

    assert '<html lang="zh-Hans">' in rendered
    assert f"<h2>{labels['audit_trail']}</h2>" in rendered
    assert labels["report_eyebrow"] in rendered
    assert labels["claim_evidence"] in rendered


def test_render_report_html_resolves_bcp47_region_tag_to_script_catalog() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_with_language(_html_artifact(), "zh-Hans-CN"))
    labels = _zh_labels()

    assert '<html lang="zh-Hans-CN">' in rendered
    assert f"<h2>{labels['audit_trail']}</h2>" in rendered


def test_render_report_html_unknown_language_falls_back_to_english_labels() -> None:
    from marivo.analysis.publish import render_report_html

    rendered = render_report_html(_with_language(_html_artifact(), "xx"))

    assert '<html lang="xx">' in rendered
    assert "<h2>Audit Trail</h2>" in rendered


def test_render_report_html_links_multiple_script_refs() -> None:
    from marivo.analysis.publish import render_report_html

    artifact = _html_artifact()
    step = artifact.flow.steps[0].model_copy(
        update={"script_refs": ("scripts/step_a.py", "scripts/step_b.py")}
    )
    artifact = artifact.model_copy(
        update={
            "flow": artifact.flow.model_copy(update={"steps": (step, *artifact.flow.steps[1:])})
        }
    )

    rendered = render_report_html(artifact)

    assert '<a href="scripts/step_a.py">scripts/step_a.py</a>' in rendered
    assert '<a href="scripts/step_b.py">scripts/step_b.py</a>' in rendered


def _artifact_with_script_refs(
    *,
    flow_script_refs: tuple[str, ...] = (),
    provenance_script_refs: tuple[str, ...] = (),
) -> MarivoReportArtifact:

    artifact = _html_artifact()
    step = artifact.flow.steps[0].model_copy(update={"script_refs": flow_script_refs})
    provenance = artifact.datasets["headline_metrics"].metadata.source_provenance.model_copy(
        update={"script_refs": provenance_script_refs}
    )
    meta = artifact.datasets["headline_metrics"].metadata.model_copy(
        update={"source_provenance": provenance}
    )
    ds = artifact.datasets["headline_metrics"].model_copy(update={"metadata": meta})
    return artifact.model_copy(
        update={
            "flow": artifact.flow.model_copy(update={"steps": (step, *artifact.flow.steps[1:])}),
            "datasets": {**artifact.datasets, "headline_metrics": ds},
        }
    )


def test_materialize_copies_scripts_when_source_dir_provided(tmp_path: Path) -> None:
    from marivo.analysis.publish.report_html_adapter import materialize_html_adapter

    artifact = _artifact_with_script_refs(
        flow_script_refs=("scripts/step_observe.py",),
        provenance_script_refs=("scripts/step_observe.py",),
    )

    source_dir = tmp_path / "source"
    (source_dir / "scripts").mkdir(parents=True)
    (source_dir / "scripts" / "step_observe.py").write_text("# observe\n", encoding="utf-8")

    package_dir = tmp_path / "package"
    materialize_html_adapter(artifact, package_dir, script_source_dir=source_dir)

    assert (package_dir / "scripts" / "step_observe.py").is_file()
    assert (package_dir / "scripts" / "step_observe.py").read_text() == "# observe\n"


def test_materialize_no_scripts_without_source_dir(tmp_path: Path) -> None:
    from marivo.analysis.publish.report_html_adapter import materialize_html_adapter

    artifact = _artifact_with_script_refs(
        flow_script_refs=("scripts/step_observe.py",),
    )

    materialize_html_adapter(artifact, tmp_path)

    assert not (tmp_path / "scripts").exists()


def test_materialize_removes_stale_scripts_on_re_run(tmp_path: Path) -> None:
    from marivo.analysis.publish.report_html_adapter import materialize_html_adapter

    source_dir = tmp_path / "source"
    (source_dir / "scripts").mkdir(parents=True)

    (source_dir / "scripts" / "step_a.py").write_text("# a\n", encoding="utf-8")
    (source_dir / "scripts" / "step_b.py").write_text("# b\n", encoding="utf-8")

    artifact_a = _artifact_with_script_refs(
        flow_script_refs=("scripts/step_a.py", "scripts/step_b.py"),
    )
    package_dir = tmp_path / "package"
    materialize_html_adapter(artifact_a, package_dir, script_source_dir=source_dir)
    assert (package_dir / "scripts" / "step_a.py").is_file()
    assert (package_dir / "scripts" / "step_b.py").is_file()

    artifact_b = _artifact_with_script_refs(
        flow_script_refs=("scripts/step_a.py",),
    )
    materialize_html_adapter(artifact_b, package_dir, script_source_dir=source_dir)
    assert (package_dir / "scripts" / "step_a.py").is_file()
    assert not (package_dir / "scripts" / "step_b.py").exists()


def test_materialize_warns_on_missing_source_script(tmp_path: Path) -> None:
    import warnings

    from marivo.analysis.publish.report_html_adapter import materialize_html_adapter

    source_dir = tmp_path / "source"
    (source_dir / "scripts").mkdir(parents=True)

    artifact = _artifact_with_script_refs(
        flow_script_refs=("scripts/ghost.py",),
    )
    package_dir = tmp_path / "package"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        materialize_html_adapter(artifact, package_dir, script_source_dir=source_dir)

    assert any("ghost.py" in str(w.message) for w in caught)
    assert not (package_dir / "scripts" / "ghost.py").exists()


def test_materialize_skips_path_traversal_script_ref(tmp_path: Path) -> None:
    import warnings

    from marivo.analysis.publish.report_html_adapter import materialize_html_adapter

    source_dir = tmp_path / "source"
    (source_dir / "scripts").mkdir(parents=True)
    (source_dir / "scripts" / "step.py").write_text("# step\n", encoding="utf-8")

    artifact = _artifact_with_script_refs(
        flow_script_refs=("scripts/step.py", "../../etc/passwd"),
    )
    package_dir = tmp_path / "package"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        materialize_html_adapter(artifact, package_dir, script_source_dir=source_dir)

    assert (package_dir / "scripts" / "step.py").is_file()
    # The traversal ref triggers a warning (missing source or path escape).
    assert any("passwd" in str(w.message) for w in caught)
    assert not (package_dir.parent.parent / "etc" / "passwd").exists()
