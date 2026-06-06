"""Adapter from Marivo report artifacts to static HTML report payloads."""

from __future__ import annotations

import json
import re
from html import escape as _html_escape
from pathlib import Path
from typing import Any, NamedTuple

from marivo.analysis.publish.report_models import (
    Dataset,
    FlowStep,
    GroundedClaim,
    MarivoReportArtifact,
    ReportBlock,
    ReportMetric,
    ReportSection,
)
from marivo.analysis.publish.report_package import write_report_artifact
from marivo.analysis.publish.report_validation import (
    validate_report_artifact as _validate_report_artifact,
)

_VALUE_REF_RE = re.compile(
    r"^(?P<dataset>[A-Za-z0-9_-]+)\[(?P<row>\d+)\]\.(?P<field>[A-Za-z0-9_]+)$"
)


class _ParsedValueRef(NamedTuple):
    dataset_id: str
    row_index: int
    field: str


def _escape(text: Any) -> str:
    return _html_escape(str(text), quote=True)


def _raise_on_invalid(artifact: MarivoReportArtifact) -> None:
    try:
        result = _validate_report_artifact(artifact)
    except Exception as exc:
        raise ValueError("report artifact is not valid for HTML adapter") from exc
    if result.ok:
        return
    details = "; ".join(
        f"{issue.check} at {issue.location}: {issue.message}" for issue in result.issues
    )
    raise ValueError(f"report artifact is not valid for HTML adapter: {details}")


def _parse_value_ref(value_ref: str) -> _ParsedValueRef:
    match = _VALUE_REF_RE.match(value_ref)
    if match is None:
        raise ValueError(f"invalid value ref for HTML adapter: {value_ref}")
    return _ParsedValueRef(
        dataset_id=match.group("dataset"),
        row_index=int(match.group("row")),
        field=match.group("field"),
    )


def _resolve_value_ref(value_ref: str, artifact: MarivoReportArtifact) -> Any:
    parsed = _parse_value_ref(value_ref)
    dataset = artifact.datasets.get(parsed.dataset_id)
    if dataset is None:
        raise ValueError(
            f"value ref {value_ref!r} references missing dataset {parsed.dataset_id!r}"
        )
    if parsed.row_index >= len(dataset.rows):
        raise ValueError(f"value ref {value_ref!r} references missing row {parsed.row_index}")
    row = dataset.rows[parsed.row_index]
    if parsed.field not in row:
        raise ValueError(f"value ref {value_ref!r} references missing field {parsed.field!r}")
    return row[parsed.field]


def _format_number(value: float) -> str:
    if value.is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _format_compact(value: float) -> str:
    abs_value = abs(value)
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs_value >= threshold:
            compact = value / threshold
            return f"{compact:.1f}".rstrip("0").rstrip(".") + suffix
    return _format_number(value)


def _format_metric_value(value: Any, value_format: str) -> str:
    if not isinstance(value, int | float):
        return str(value)
    numeric = float(value)
    if value_format == "currency":
        return f"${_format_number(numeric)}"
    if value_format == "percent":
        return f"{_format_number(numeric * 100)}%"
    if value_format == "number":
        return _format_number(numeric)
    if value_format == "compact":
        return _format_compact(numeric)
    return str(value)


def _metric_payload(metric: ReportMetric, artifact: MarivoReportArtifact) -> dict[str, Any]:
    value = _resolve_value_ref(metric.value_ref, artifact)
    return {
        "label": metric.label,
        "value_ref": metric.value_ref,
        "value": value,
        "formatted_value": _format_metric_value(value, metric.format),
        "format": metric.format,
        "signed": metric.signed,
    }


def _source_payload(dataset: Dataset) -> dict[str, Any]:
    source = dataset.metadata.source_provenance
    query: dict[str, Any] = {
        "description": source.query_summary,
        "generated_from": source.generated_from,
        "semantic_refs": list(source.semantic_refs),
        "datasource_refs": list(source.datasource_refs),
        "tables_used": list(source.datasource_refs),
        "sql_status": source.sql_status,
        "sql_reason": source.sql_reason,
        "metric_definitions": list(dataset.metadata.metric_definitions),
        "filters": list(dataset.metadata.filters),
    }
    if source.sql_status == "available":
        query["language"] = "sql"
        query["sql"] = source.sql
    else:
        query["language"] = source.generated_from
    if source.script_ref is not None:
        query["script_ref"] = source.script_ref
    if source.promotion_ref is not None:
        query["promotion_ref"] = source.promotion_ref
    return {
        "id": dataset.dataset_id,
        "label": dataset.dataset_id,
        "query": query,
    }


def _dataset_payload(dataset: Dataset) -> dict[str, Any]:
    return {
        "metadata": dataset.metadata.model_dump(mode="json"),
        "rows": [dict(row) for row in dataset.rows],
    }


def _chart_payload(block: ReportBlock) -> dict[str, Any] | None:
    if block.chart is None:
        return None
    return {
        "type": block.chart.type,
        "fields": dict(block.chart.fields),
        "options": dict(block.chart.options),
    }


def _columns_payload(block: ReportBlock) -> list[dict[str, Any]]:
    return [
        {
            "key": column.key,
            "label": column.label,
            "type": column.type,
            "format": column.format,
        }
        for column in block.columns
    ]


def _block_payload(block: ReportBlock, artifact: MarivoReportArtifact) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": block.block_id,
        "type": block.block_type,
        "title": block.title,
        "subtitle": block.subtitle,
        "text": block.text,
        "dataset_id": block.dataset_id,
        "value_refs": list(block.value_refs),
        "claim_refs": list(block.claim_refs),
        "step_refs": list(block.step_refs),
        "source_refs": list(block.source_refs),
        "narrative_ref": block.narrative_ref,
        "collapsed_by_default": block.collapsed_by_default,
    }
    if block.metrics:
        payload["metrics"] = [_metric_payload(metric, artifact) for metric in block.metrics]
    elif block.value_refs:
        payload["metrics"] = [
            {
                "label": block.title or _parse_value_ref(value_ref).field,
                "value_ref": value_ref,
                "value": _resolve_value_ref(value_ref, artifact),
                "formatted_value": _format_metric_value(
                    _resolve_value_ref(value_ref, artifact), "compact"
                ),
                "format": "compact",
                "signed": False,
            }
            for value_ref in block.value_refs
        ]
    if block.columns:
        payload["columns"] = _columns_payload(block)
    chart = _chart_payload(block)
    if chart is not None:
        payload["chart"] = chart
    return payload


def _section_payload(section: ReportSection, artifact: MarivoReportArtifact) -> dict[str, Any]:
    return {
        "id": section.section_id,
        "type": section.section_type,
        "title": section.title,
        "blocks": [_block_payload(block, artifact) for block in section.blocks],
    }


def _claim_payload(claim: GroundedClaim) -> dict[str, Any]:
    return {
        "id": claim.claim_id,
        "text_template": claim.text_template,
        "value_refs": list(claim.value_refs),
        "section_id": claim.section_id,
        "grounding_type": claim.grounding_type,
        "evidence_status": claim.evidence_status,
        "supporting_artifacts": list(claim.supporting_artifacts),
        "supporting_steps": list(claim.supporting_steps),
        "supporting_datasets": list(claim.supporting_datasets),
        "source_refs": list(claim.source_refs),
        "risk_refs": list(claim.risk_refs),
        "confidence_scope": claim.confidence_scope,
    }


def _flow_step_payload(step: FlowStep) -> dict[str, Any]:
    return {
        "id": step.step_id,
        "order": step.order,
        "kind": step.kind,
        "description": step.description,
        "input_artifacts": list(step.input_artifacts),
        "output_artifacts": list(step.output_artifacts),
        "semantic_refs": list(step.semantic_refs),
        "source_queries": list(step.source_queries),
        "script_ref": step.script_ref,
        "evidence_status": step.evidence_status,
        "query_summary": step.query_summary,
        "links": dict(step.links),
    }


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        rendered = json.dumps(payload, allow_nan=False, sort_keys=True)
        loaded: dict[str, Any] = json.loads(rendered)
    except (TypeError, ValueError) as exc:
        raise ValueError("report artifact payload is not valid for HTML adapter") from exc
    return loaded


def to_html_report_payload(artifact: MarivoReportArtifact) -> dict[str, Any]:
    """Convert a validated Marivo report artifact to a static HTML report payload."""
    _raise_on_invalid(artifact)
    payload = {
        "kind": "marivo_analysis_report_html_payload",
        "manifest_version": artifact.manifest.manifest_version,
        "report_id": artifact.manifest.report_id,
        "export_id": artifact.manifest.export_id,
        "title": artifact.manifest.title,
        "created_at": artifact.manifest.created_at,
        "marivo_version": artifact.manifest.marivo_version,
        "evidence_status": artifact.manifest.evidence_status,
        "data_policy": artifact.manifest.data_policy.model_dump(mode="json"),
        "sections": [
            _section_payload(section, artifact) for section in artifact.report_spec.sections
        ],
        "claims": [_claim_payload(claim) for claim in artifact.grounding.claims],
        "flow_steps": [_flow_step_payload(step) for step in artifact.flow.steps],
        "datasets": {
            dataset_id: _dataset_payload(dataset)
            for dataset_id, dataset in artifact.datasets.items()
        },
        "sources": [_source_payload(dataset) for dataset in artifact.datasets.values()],
        "evidence": {
            evidence_id: dict(evidence_payload)
            for evidence_id, evidence_payload in artifact.evidence.items()
        },
    }
    return _normalize_payload(payload)


def _raise_visual_block_field_errors(artifact: MarivoReportArtifact) -> None:
    for section in artifact.report_spec.sections:
        for block in section.blocks:
            fields: list[str] = []
            if block.block_type == "chart" and block.chart is not None:
                fields.extend(
                    field
                    for field in (block.chart.fields.get("x"), block.chart.fields.get("y"))
                    if field
                )
            elif block.block_type == "table":
                fields.extend(column.key for column in block.columns)
            else:
                continue
            if not block.dataset_id:
                continue
            dataset = artifact.datasets.get(block.dataset_id)
            if dataset is None:
                continue
            for field in fields:
                for index, row in enumerate(dataset.rows):
                    if field not in row:
                        raise ValueError(
                            f"{block.block_type} block {block.block_id!r} "
                            f"references missing field {field!r} "
                            f"in dataset {block.dataset_id!r} row {index}"
                        )


_STYLE = """
:root {
  color-scheme: light;
  --bg: #f7f8fa;
  --panel: #ffffff;
  --ink: #1b1f24;
  --muted: #5e6875;
  --line: #d9dee7;
  --accent: #0f766e;
  --accent-soft: #e8f5f2;
  --warning: #8a4b0f;
  --warning-soft: #fff4df;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family:
    Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.55;
}

a {
  color: var(--accent);
  text-decoration: none;
}

a:hover {
  text-decoration: underline;
}

.layout {
  width: min(1120px, calc(100% - 32px));
  margin: 0 auto;
  padding: 40px 0 56px;
}

.report-header {
  margin-bottom: 28px;
}

.eyebrow {
  color: var(--muted);
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0;
  margin: 0 0 8px;
  text-transform: uppercase;
}

h1,
h2,
h3 {
  line-height: 1.18;
  letter-spacing: 0;
}

h1 {
  font-size: clamp(2.25rem, 3rem, 3rem);
  margin: 0;
}

h2 {
  font-size: 1.55rem;
  margin: 0 0 16px;
}

h3 {
  font-size: 1rem;
  margin: 0 0 8px;
}

.toc {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 18px;
  padding: 12px;
}

.toc a {
  border: 1px solid var(--line);
  border-radius: 6px;
  color: var(--ink);
  font-size: 0.9rem;
  padding: 6px 10px;
}

.notice {
  background: var(--warning-soft);
  border: 1px solid #e7c58c;
  border-radius: 8px;
  color: var(--warning);
  margin: 0 0 18px;
  padding: 12px 14px;
}

.notice strong {
  display: block;
}

.report-section {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  margin: 18px 0;
  padding: 24px;
}

.report-block {
  border-top: 1px solid var(--line);
  margin-top: 18px;
  padding-top: 18px;
}

.report-block:first-of-type {
  border-top: 0;
  margin-top: 0;
  padding-top: 0;
}

.subtitle {
  color: var(--muted);
  margin: -2px 0 12px;
}

.markdown p {
  margin: 0 0 12px;
}

.markdown p:last-child,
.markdown ul:last-child,
.markdown ol:last-child {
  margin-bottom: 0;
}

.markdown ul,
.markdown ol {
  margin: 0 0 12px 1.2rem;
  padding: 0;
}

.placeholder {
  background: #fbfcfd;
  border: 1px dashed #b9c1cf;
  border-radius: 8px;
  color: var(--muted);
  padding: 14px;
}

.metric-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
}

.metric-card,
.chart-card,
.table-card {
  background: #fbfcfd;
  border: 1px solid var(--line);
  border-radius: 8px;
}

.metric-card {
  padding: 14px;
}

.metric-label {
  color: var(--muted);
  font-size: 0.82rem;
  font-weight: 700;
  margin: 0 0 4px;
  text-transform: uppercase;
}

.metric-value {
  font-size: 1.8rem;
  font-weight: 750;
  line-height: 1.1;
}

.chart-card,
.table-card {
  overflow: hidden;
}

.chart-frame {
  padding: 14px;
}

.chart-frame svg {
  display: block;
  height: auto;
  width: 100%;
}

.chart-axis {
  stroke: #aab3c2;
  stroke-width: 1;
}

.chart-line {
  fill: none;
  stroke: var(--accent);
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 3;
}

.chart-point {
  fill: var(--accent);
}

.chart-bar {
  fill: var(--accent);
}

.chart-label {
  fill: var(--muted);
  font-size: 11px;
}

.table-scroll {
  overflow-x: auto;
}

.audit-group {
  margin-top: 22px;
}

.audit-panel,
.audit-detail {
  background: #fbfcfd;
  border: 1px solid var(--line);
  border-radius: 8px;
  margin: 12px 0;
  padding: 14px;
}

.audit-detail summary {
  cursor: pointer;
  font-weight: 750;
}

.audit-meta,
.audit-links {
  color: var(--muted);
  margin: 8px 0 0;
}

.audit-links {
  padding-left: 1.2rem;
}

.audit-code {
  background: #f3f5f8;
  border: 1px solid var(--line);
  border-radius: 6px;
  margin: 10px 0 0;
  overflow-x: auto;
  padding: 10px;
  white-space: pre-wrap;
}

table {
  border-collapse: collapse;
  width: 100%;
}

th,
td {
  border-bottom: 1px solid var(--line);
  padding: 8px 10px;
  text-align: left;
  white-space: nowrap;
}

th {
  background: #f3f5f8;
  color: var(--muted);
  font-size: 0.82rem;
  font-weight: 750;
}

table[data-sortable] th {
  cursor: pointer;
}

@media (max-width: 720px) {
  .layout {
    width: min(100% - 20px, 1120px);
    padding: 24px 0 40px;
  }

  .report-section {
    padding: 18px;
  }
}
""".strip()

_SCRIPT = """
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("table[data-sortable] th").forEach((header) => {
    header.addEventListener("click", () => {
      const table = header.closest("table");
      const tbody = table && table.querySelector("tbody");
      if (!table || !tbody) {
        return;
      }
      const index = Array.from(header.parentElement.children).indexOf(header);
      const direction = header.dataset.sortDirection === "asc" ? "desc" : "asc";
      table.querySelectorAll("th[data-sortable]").forEach((cell) => {
        delete cell.dataset.sortDirection;
      });
      header.dataset.sortDirection = direction;
      const rows = Array.from(tbody.querySelectorAll("tr"));
      rows.sort((left, right) => {
        const leftCell = left.children[index];
        const rightCell = right.children[index];
        const leftValue = leftCell ? (leftCell.dataset.sortValue || leftCell.textContent.trim()) : "";
        const rightValue = rightCell ? (rightCell.dataset.sortValue || rightCell.textContent.trim()) : "";
        const leftNumber = Number(leftValue);
        const rightNumber = Number(rightValue);
        const result = Number.isFinite(leftNumber) && Number.isFinite(rightNumber)
          ? leftNumber - rightNumber
          : leftValue.localeCompare(rightValue);
        return direction === "asc" ? result : -result;
      });
      rows.forEach((row) => tbody.appendChild(row));
    });
  });
});
""".strip()


def _json_for_script(payload: dict[str, Any]) -> str:
    return json.dumps(payload, allow_nan=False, sort_keys=True).replace("</", "<\\/")


def _render_markdown_block(text: str | None) -> str:
    if text is None or not text.strip():
        return ""
    lines = text.strip().splitlines()
    chunks: list[str] = []
    list_items: list[str] = []

    def flush_list() -> None:
        if not list_items:
            return
        chunks.append("<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>")
        list_items.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_list()
            continue
        if stripped.startswith("- "):
            list_items.append(_escape(stripped[2:]))
            continue
        flush_list()
        if stripped.startswith("### "):
            chunks.append(f"<h4>{_escape(stripped[4:])}</h4>")
        elif stripped.startswith("## "):
            chunks.append(f"<h3>{_escape(stripped[3:])}</h3>")
        elif stripped.startswith("# "):
            chunks.append(f"<h2>{_escape(stripped[2:])}</h2>")
        else:
            chunks.append(f"<p>{_escape(stripped)}</p>")
    flush_list()
    return '<div class="markdown">' + "".join(chunks) + "</div>"


def _render_placeholder_block(block: dict[str, Any], block_type: str) -> str:
    parts = [_render_markdown_block(block.get("text"))]
    dataset_id = block.get("dataset_id")
    dataset_detail = f" Dataset: <code>{_escape(dataset_id)}</code>." if dataset_id else ""
    parts.append(
        f'<div class="placeholder">{_escape(block_type)} block retained for offline rendering.'
        f"{dataset_detail}</div>"
    )
    return "".join(parts)


def _dataset_for_block(block: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    dataset_id = block.get("dataset_id")
    if not dataset_id:
        raise ValueError(f"{block.get('type', 'unknown')} block {block.get('id')!r} has no dataset")
    datasets = payload.get("datasets", {})
    dataset = datasets.get(dataset_id)
    if dataset is None:
        raise ValueError(
            f"{block.get('type', 'unknown')} block {block.get('id')!r} "
            f"references missing dataset {dataset_id!r}"
        )
    return {str(key): value for key, value in dataset.items()}


def _require_dataset_fields(
    block: dict[str, Any],
    dataset: dict[str, Any],
    fields: list[str],
) -> None:
    rows = dataset.get("rows", [])
    dataset_id = block.get("dataset_id")
    block_type = block.get("type", "unknown")
    block_id = block.get("id", "block")
    for field in fields:
        for index, row in enumerate(rows):
            if field not in row:
                raise ValueError(
                    f"{block_type} block {block_id!r} references missing field {field!r} "
                    f"in dataset {dataset_id!r} row {index}"
                )


def _render_metric_strip(block: dict[str, Any]) -> str:
    metrics = block.get("metrics", [])
    cards = []
    for metric in metrics:
        cards.append(
            '<div class="metric-card"'
            f' data-value-ref="{_escape(metric.get("value_ref", ""))}">'
            f'<p class="metric-label">{_escape(metric.get("label", ""))}</p>'
            f'<div class="metric-value">{_escape(metric.get("formatted_value", ""))}</div>'
            "</div>"
        )
    return '<div class="metric-grid">' + "".join(cards) + "</div>"


def _numeric(value: Any, *, block: dict[str, Any], field: str, row_index: int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(
            f"{block.get('type', 'unknown')} block {block.get('id')!r} "
            f"requires numeric field {field!r} in row {row_index}"
        )
    return float(value)


def _scale(
    value: float, *, domain_min: float, domain_max: float, range_min: float, range_max: float
) -> float:
    if domain_min == domain_max:
        return (range_min + range_max) / 2
    ratio = (value - domain_min) / (domain_max - domain_min)
    return range_min + ratio * (range_max - range_min)


def _render_svg_chart(block: dict[str, Any], payload: dict[str, Any]) -> str:
    chart = block.get("chart") or {}
    fields = chart.get("fields") or {}
    x_field = fields.get("x")
    y_field = fields.get("y")
    if not x_field or not y_field:
        raise ValueError(f"chart block {block.get('id')!r} requires x and y fields")
    chart_type = chart.get("type", "line")
    if chart_type not in {"line", "bar"}:
        raise ValueError(
            f"chart block {block.get('id')!r} does not support chart type {chart_type!r}"
        )
    dataset = _dataset_for_block(block, payload)
    rows = dataset.get("rows", [])
    _require_dataset_fields(block, dataset, [x_field, y_field])
    values = [
        _numeric(row[y_field], block=block, field=y_field, row_index=index)
        for index, row in enumerate(rows)
    ]
    width = 640
    height = 280
    left = 44
    right = 18
    top = 18
    bottom = 42
    plot_width = width - left - right
    min_value = min(values) if values else 0.0
    max_value = max(values) if values else 0.0
    if chart_type == "bar":
        y_domain_min = min(0.0, min_value)
        y_domain_max = max(0.0, max_value)
        axis_y = _scale(
            0.0,
            domain_min=y_domain_min,
            domain_max=y_domain_max,
            range_min=height - bottom,
            range_max=top,
        )
    else:
        y_domain_min = min_value
        y_domain_max = max_value
        axis_y = height - bottom
    parts = [
        f'<div class="chart-card" id="chart-{_escape(block.get("id", "block"))}">',
        '<div class="chart-frame">',
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_escape(block.get("title") or "Chart")}">',
        f'<line class="chart-axis" x1="{left}" y1="{axis_y:.2f}" x2="{width - right}" y2="{axis_y:.2f}"></line>',
        f'<line class="chart-axis" x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}"></line>',
    ]
    if chart_type == "bar":
        bar_gap = 10
        bar_width = max(8.0, (plot_width - bar_gap * max(0, len(rows) - 1)) / max(1, len(rows)))
        for index, row in enumerate(rows):
            value = values[index]
            x = left + index * (bar_width + bar_gap)
            y = _scale(
                value,
                domain_min=y_domain_min,
                domain_max=y_domain_max,
                range_min=height - bottom,
                range_max=top,
            )
            bar_y = min(y, axis_y)
            bar_height = abs(axis_y - y)
            parts.append(
                f'<rect class="chart-bar" x="{x:.2f}" y="{bar_y:.2f}" '
                f'width="{bar_width:.2f}" height="{bar_height:.2f}"></rect>'
            )
            parts.append(
                f'<text class="chart-label" x="{x + bar_width / 2:.2f}" y="{height - 16}" '
                f'text-anchor="middle">{_escape(row[x_field])}</text>'
            )
    else:
        points = []
        for index, row in enumerate(rows):
            x = _scale(
                float(index),
                domain_min=0.0,
                domain_max=float(max(1, len(rows) - 1)),
                range_min=left,
                range_max=width - right,
            )
            y = _scale(
                values[index],
                domain_min=y_domain_min,
                domain_max=y_domain_max,
                range_min=height - bottom,
                range_max=top,
            )
            points.append(f"{x:.2f},{y:.2f}")
            parts.append(f'<circle class="chart-point" cx="{x:.2f}" cy="{y:.2f}" r="4"></circle>')
            parts.append(
                f'<text class="chart-label" x="{x:.2f}" y="{height - 16}" '
                f'text-anchor="middle">{_escape(row[x_field])}</text>'
            )
        parts.append(f'<polyline class="chart-line" points="{" ".join(points)}"></polyline>')
    parts.extend(["</svg>", "</div>", "</div>"])
    return "".join(parts)


def _column_label(column: dict[str, Any]) -> str:
    return str(column.get("label") or column.get("key") or "")


def _format_cell(value: Any, column: dict[str, Any]) -> str:
    value_format = column.get("format")
    if value_format is None and column.get("type") == "number":
        value_format = "number"
    if isinstance(value, int | float) and value_format is not None:
        return _format_metric_value(value, str(value_format))
    return str(value)


def _table_columns(block: dict[str, Any], dataset: dict[str, Any]) -> list[dict[str, Any]]:
    columns = list(block.get("columns") or [])
    if columns:
        return columns
    rows = dataset.get("rows", [])
    if not rows:
        return []
    return [{"key": key, "label": key, "type": "text", "format": None} for key in rows[0]]


def _render_table(block: dict[str, Any], payload: dict[str, Any]) -> str:
    dataset = _dataset_for_block(block, payload)
    columns = _table_columns(block, dataset)
    fields = [str(column["key"]) for column in columns]
    _require_dataset_fields(block, dataset, fields)
    header = "".join(
        f'<th data-sortable="true">{_escape(_column_label(column))}</th>' for column in columns
    )
    body_rows = []
    for row in dataset.get("rows", []):
        cells = []
        for column in columns:
            key = str(column["key"])
            raw_value = row[key]
            cells.append(
                f'<td data-sort-value="{_escape(raw_value)}">{_escape(_format_cell(raw_value, column))}</td>'
            )
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        f'<div class="table-card" id="table-{_escape(block.get("id", "block"))}">'
        '<div class="table-scroll">'
        '<table data-sortable="true">'
        f"<thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table>"
        "</div>"
        "</div>"
    )


def _render_block(block: dict[str, Any], payload: dict[str, Any]) -> str:
    block_id = _escape(block.get("id", "block"))
    block_type = str(block.get("type", "markdown"))
    title = block.get("title")
    subtitle = block.get("subtitle")
    parts = [f'<article class="report-block report-block-{_escape(block_type)}" id="{block_id}">']
    if title:
        parts.append(f"<h3>{_escape(title)}</h3>")
    if subtitle:
        parts.append(f'<p class="subtitle">{_escape(subtitle)}</p>')
    if block_type == "markdown":
        parts.append(_render_markdown_block(block.get("text")))
    elif block_type == "metric_strip":
        parts.append(_render_metric_strip(block))
    elif block_type == "chart":
        parts.append(_render_svg_chart(block, payload))
    elif block_type == "table":
        parts.append(_render_table(block, payload))
    else:
        parts.append(_render_placeholder_block(block, block_type))
    parts.append("</article>")
    return "".join(parts)


def _render_section(section: dict[str, Any], payload: dict[str, Any]) -> str:
    section_id = _escape(section.get("id", "section"))
    parts = [f'<section class="report-section" id="section-{section_id}">']
    parts.append(f"<h2>{_escape(section.get('title', 'Untitled Section'))}</h2>")
    for block in section.get("blocks", []):
        parts.append(_render_block(block, payload))
    parts.append("</section>")
    return "".join(parts)


def _link_list(items: list[str], prefix: str, empty: str) -> str:
    if not items:
        return f'<p class="audit-meta">{_escape(empty)}</p>'
    links = "".join(
        f'<li><a href="#{_escape(prefix)}-{_escape(item)}">{_escape(item)}</a></li>'
        for item in items
    )
    return f'<ul class="audit-links">{links}</ul>'


def _render_labeled_value(label: str, value: Any) -> str:
    if value is None or value == "" or value == []:
        return ""
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value) if value else ""
    return f'<p class="audit-meta"><strong>{_escape(label)}:</strong> {_escape(value)}</p>'


def _render_claims(payload: dict[str, Any]) -> str:
    claims = payload.get("claims", [])
    if not claims:
        return '<p class="audit-meta">No claim evidence recorded.</p>'
    parts = []
    for claim in claims:
        claim_id = _escape(claim.get("id", "claim"))
        claim_text = claim.get("text") or claim.get("text_template") or claim.get("id", "")
        parts.append(f'<article class="audit-panel" id="claim-{claim_id}">')
        parts.append(f"<h4>{_escape(claim_text)}</h4>")
        parts.append(_render_labeled_value("Grounding", claim.get("grounding_type")))
        parts.append(_render_labeled_value("Evidence status", claim.get("evidence_status")))
        parts.append(_render_labeled_value("Scope", claim.get("confidence_scope")))
        parts.append(_render_labeled_value("Value refs", claim.get("value_refs")))
        parts.append("<h5>Supporting steps</h5>")
        parts.append(
            _link_list(list(claim.get("supporting_steps", [])), "step", "No supporting steps.")
        )
        parts.append("<h5>Supporting datasets</h5>")
        parts.append(
            _link_list(
                list(claim.get("supporting_datasets", [])),
                "dataset",
                "No supporting datasets.",
            )
        )
        parts.append("<h5>Evidence objects</h5>")
        parts.append(
            _link_list(
                list(claim.get("supporting_artifacts", [])),
                "evidence",
                "No supporting evidence objects.",
            )
        )
        parts.append(_render_labeled_value("Source refs", claim.get("source_refs")))
        parts.append(_render_labeled_value("Risk refs", claim.get("risk_refs")))
        parts.append("</article>")
    return "".join(parts)


def _render_flow_steps(payload: dict[str, Any]) -> str:
    steps = payload.get("flow_steps", [])
    if not steps:
        return '<p class="audit-meta">No analysis steps recorded.</p>'
    parts = []
    for step in steps:
        step_id = _escape(step.get("id", "step"))
        summary = f"{step.get('order', '')}. {step.get('description', step.get('id', ''))}"
        parts.append(f'<details class="audit-detail" id="step-{step_id}" open>')
        parts.append(f"<summary>{_escape(summary)}</summary>")
        parts.append(_render_labeled_value("Kind", step.get("kind")))
        parts.append(_render_labeled_value("Evidence status", step.get("evidence_status")))
        parts.append(_render_labeled_value("Query summary", step.get("query_summary")))
        parts.append(_render_labeled_value("Input artifacts", step.get("input_artifacts")))
        parts.append(_render_labeled_value("Output artifacts", step.get("output_artifacts")))
        parts.append(_render_labeled_value("Semantic refs", step.get("semantic_refs")))
        parts.append(_render_labeled_value("Source queries", step.get("source_queries")))
        parts.append(_render_labeled_value("Script ref", step.get("script_ref")))
        if step.get("links"):
            parts.append(_render_labeled_value("Links", step.get("links")))
        parts.append("</details>")
    return "".join(parts)


def _render_dataset_panels(payload: dict[str, Any]) -> str:
    datasets = payload.get("datasets", {})
    if not datasets:
        return '<p class="audit-meta">No datasets recorded.</p>'
    parts = []
    for dataset_id, dataset in datasets.items():
        metadata = dataset.get("metadata", {})
        parts.append(f'<article class="audit-panel" id="dataset-{_escape(dataset_id)}">')
        parts.append(f"<h4>{_escape(dataset_id)}</h4>")
        parts.append(_render_labeled_value("Grain", metadata.get("grain")))
        parts.append(_render_labeled_value("Row count", metadata.get("row_count")))
        parts.append(_render_labeled_value("Truncated", metadata.get("truncated")))
        parts.append(_render_labeled_value("Source artifacts", metadata.get("source_artifacts")))
        parts.append(
            _render_labeled_value("Metric definitions", metadata.get("metric_definitions"))
        )
        parts.append(_render_labeled_value("Filters", metadata.get("filters")))
        parts.append("</article>")
    return "".join(parts)


def _render_sources(payload: dict[str, Any]) -> str:
    sources = payload.get("sources", [])
    if not sources:
        return '<p class="audit-meta">No sources recorded.</p>'
    parts = []
    for source in sources:
        source_id = source.get("id", "source")
        query = source.get("query", {})
        parts.append(f'<article class="audit-panel" id="source-{_escape(source_id)}">')
        parts.append(f"<h4>{_escape(source.get('label') or source_id)}</h4>")
        parts.append(_render_labeled_value("Description", query.get("description")))
        parts.append(_render_labeled_value("Language", query.get("language")))
        parts.append(_render_labeled_value("Generated from", query.get("generated_from")))
        parts.append(_render_labeled_value("Tables", query.get("tables_used")))
        parts.append(_render_labeled_value("Datasource refs", query.get("datasource_refs")))
        parts.append(_render_labeled_value("Semantic refs", query.get("semantic_refs")))
        parts.append(_render_labeled_value("Metric definitions", query.get("metric_definitions")))
        parts.append(_render_labeled_value("Filters", query.get("filters")))
        parts.append(_render_labeled_value("SQL status", query.get("sql_status")))
        parts.append(_render_labeled_value("SQL reason", query.get("sql_reason")))
        parts.append(_render_labeled_value("Script ref", query.get("script_ref")))
        parts.append(_render_labeled_value("Promotion ref", query.get("promotion_ref")))
        if query.get("sql"):
            parts.append(f'<pre class="audit-code">{_escape(query["sql"])}</pre>')
        parts.append("</article>")
    return "".join(parts)


def _render_evidence(payload: dict[str, Any]) -> str:
    evidence = payload.get("evidence", {})
    if not evidence:
        return '<p class="audit-meta">No evidence objects recorded.</p>'
    parts = []
    for evidence_id, evidence_payload in evidence.items():
        pretty = json.dumps(evidence_payload, allow_nan=False, indent=2, sort_keys=True)
        parts.append(f'<details class="audit-detail" id="evidence-{_escape(evidence_id)}" open>')
        parts.append(f"<summary>{_escape(evidence_id)}</summary>")
        parts.append(f'<pre class="audit-code">{_escape(pretty)}</pre>')
        parts.append("</details>")
    return "".join(parts)


def _render_audit_trail(payload: dict[str, Any]) -> str:
    return (
        '<section class="report-section" id="audit-trail">'
        "<h2>Audit Trail</h2>"
        '<div class="audit-group"><h3>Claim Evidence</h3>'
        f"{_render_claims(payload)}</div>"
        '<div class="audit-group"><h3>Analysis Steps</h3>'
        f"{_render_flow_steps(payload)}</div>"
        '<div class="audit-group"><h3>Datasets</h3>'
        f"{_render_dataset_panels(payload)}</div>"
        '<div class="audit-group"><h3>Sources</h3>'
        f"{_render_sources(payload)}</div>"
        '<div class="audit-group"><h3>Evidence Objects</h3>'
        f"{_render_evidence(payload)}</div>"
        "</section>"
    )


def render_report_html(artifact: MarivoReportArtifact) -> str:
    """Render a validated Marivo report artifact as a standalone HTML document."""
    _raise_visual_block_field_errors(artifact)
    payload = to_html_report_payload(artifact)
    title = payload["title"]
    sections = payload["sections"]
    toc = (
        "".join(
            f'<a href="#section-{_escape(section["id"])}">{_escape(section["title"])}</a>'
            for section in sections
        )
        + '<a href="#audit-trail">Audit Trail</a>'
    )
    notice = ""
    evidence_status = payload["evidence_status"]
    if evidence_status != "complete":
        notice = (
            '<aside class="notice">'
            f"<strong>Evidence status: {_escape(evidence_status)}</strong>"
            "Review caveats and source details before acting on the recommendations."
            "</aside>"
        )
    rendered_sections = "".join(_render_section(section, payload) for section in sections)
    audit_trail = _render_audit_trail(payload)
    data = _json_for_script(payload)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_escape(title)}</title>\n"
        f"<style>{_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        '<main id="report" class="layout">\n'
        '<header class="report-header">\n'
        '<p class="eyebrow">Marivo Analysis Report</p>\n'
        f"<h1>{_escape(title)}</h1>\n"
        "</header>\n"
        f'<nav class="toc" aria-label="Report sections">{toc}</nav>\n'
        f"{notice}\n"
        f"{rendered_sections}\n"
        f"{audit_trail}\n"
        "</main>\n"
        f'<script type="application/json" id="marivo-report-data">{data}</script>\n'
        f"<script>{_SCRIPT}</script>\n"
        "</body>\n"
        "</html>\n"
    )


def materialize_html_adapter(
    artifact: MarivoReportArtifact,
    root: str | Path,
) -> MarivoReportArtifact:
    """Write canonical report package files plus standalone ``index.html``."""
    package_root = Path(root)
    entrypoints = dict(artifact.manifest.entrypoints)
    entrypoints["html"] = "index.html"
    updated = artifact.model_copy(
        update={"manifest": artifact.manifest.model_copy(update={"entrypoints": entrypoints})}
    )
    html_text = render_report_html(updated)
    package_root.mkdir(parents=True, exist_ok=True)
    index_path = package_root / "index.html"
    index_path.write_text(html_text, encoding="utf-8")
    write_report_artifact(updated, package_root)
    return updated
