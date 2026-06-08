"""Adapter from Marivo report artifacts to Data Analytics MCP report payloads."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, NamedTuple

from marivo.analysis.publish.report_models import (
    Dataset,
    GroundedClaim,
    MarivoReportArtifact,
    McpAdapterMetadata,
    ReportBlock,
)
from marivo.analysis.publish.report_package import write_report_artifact
from marivo.analysis.publish.report_validation import validate_report_artifact

_VALUE_REF_RE = re.compile(
    r"^(?P<dataset>[A-Za-z0-9_-]+)\[(?P<row>\d+)\]\.(?P<field>[A-Za-z0-9_]+)$"
)
_DEFAULT_TARGET_SCHEMA = "data-analytics-artifact-v1"


class _ParsedValueRef(NamedTuple):
    dataset_id: str
    row_index: int
    field: str


def _render_json(payload: Any) -> str:
    return json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_json(payload), encoding="utf-8")


def _raise_on_invalid(artifact: MarivoReportArtifact) -> None:
    result = validate_report_artifact(artifact)
    if result.ok:
        return
    details = "; ".join(
        f"{issue.check} at {issue.location}: {issue.message}" for issue in result.issues
    )
    raise ValueError(f"report artifact is not valid for MCP adapter: {details}")


def _snapshot_status(status: str) -> str:
    if status == "complete":
        return "ready"
    if status == "partial":
        return "partial"
    return "blocked"


def _parse_value_ref(value_ref: str) -> _ParsedValueRef:
    match = _VALUE_REF_RE.match(value_ref)
    if match is None:
        raise ValueError(f"invalid value ref for MCP adapter: {value_ref}")
    return _ParsedValueRef(
        dataset_id=match.group("dataset"),
        row_index=int(match.group("row")),
        field=match.group("field"),
    )


def _metric_value_ref(
    value_ref: str, block: ReportBlock, artifact: MarivoReportArtifact
) -> _ParsedValueRef:
    parsed = _parse_value_ref(value_ref)
    if parsed.dataset_id != block.dataset_id:
        raise ValueError(
            f"metric_strip block {block.block_id!r} value ref {value_ref!r} "
            f"must reference block dataset {block.dataset_id!r}"
        )
    if parsed.row_index != 0:
        raise ValueError(
            f"metric_strip block {block.block_id!r} value ref {value_ref!r} "
            "must reference row 0 for MCP metric cards"
        )
    dataset = artifact.datasets.get(parsed.dataset_id)
    if dataset is None:
        raise ValueError(
            f"metric_strip block {block.block_id!r} value ref {value_ref!r} "
            f"references missing dataset {parsed.dataset_id!r}"
        )
    if not dataset.rows:
        raise ValueError(
            f"metric_strip block {block.block_id!r} value ref {value_ref!r} "
            "references missing row 0"
        )
    if parsed.field not in dataset.rows[0]:
        raise ValueError(
            f"metric_strip block {block.block_id!r} value ref {value_ref!r} "
            f"references missing field {parsed.field!r}"
        )
    return parsed


def _source_for_dataset(dataset: Dataset) -> dict[str, Any]:
    source = dataset.metadata.source_provenance
    query: dict[str, Any] = {
        "description": source.query_summary,
        "filters": list(dataset.metadata.filters),
        "metric_definitions": list(dataset.metadata.metric_definitions),
        "tables_used": list(source.datasource_refs),
        "language": "sql" if source.sql_status == "available" else source.generated_from,
    }
    if source.sql_status == "available":
        query["sql"] = source.sql
    else:
        query["sql_status"] = source.sql_status
        query["sql_reason"] = source.sql_reason
    notes = [
        f"grain: {dataset.metadata.grain}",
        f"generated_from: {source.generated_from}",
    ]
    if dataset.metadata.source_artifacts:
        notes.append(f"source_artifacts: {', '.join(dataset.metadata.source_artifacts)}")
    if source.semantic_refs:
        notes.append(f"semantic_refs: {', '.join(source.semantic_refs)}")
    if source.script_refs:
        notes.append(f"script_refs: {', '.join(source.script_refs)}")
    if source.promotion_ref:
        notes.append(f"promotion_ref: {source.promotion_ref}")
    return {
        "id": dataset.dataset_id,
        "label": dataset.dataset_id,
        "query": query,
        "notes": notes,
    }


def _source_id_for_block(block: ReportBlock) -> str | None:
    return block.dataset_id


def _claim_lookup(artifact: MarivoReportArtifact) -> dict[str, GroundedClaim]:
    return {claim.claim_id: claim for claim in artifact.grounding.claims}


def _audit_markdown(block: ReportBlock, artifact: MarivoReportArtifact) -> str:
    claims = _claim_lookup(artifact)
    lines: list[str] = []
    if block.text:
        lines.append(block.text)
    for claim_ref in block.claim_refs:
        claim = claims.get(claim_ref)
        if claim is not None:
            lines.append(f"- Claim `{claim.claim_id}`: {claim.text_template}")
    for step_ref in block.step_refs:
        lines.append(f"- Step `{step_ref}`")
    for source_ref in block.source_refs:
        lines.append(f"- Source `{source_ref}`")
    return (
        "\n".join(lines) if lines else "Audit details are available in the Marivo report package."
    )


class _GeneratedBlockIdAllocator:
    def __init__(self, reserved_ids: set[str]) -> None:
        self._used = set(reserved_ids)

    def allocate(self, preferred: str) -> str:
        candidate = preferred
        index = 1
        while candidate in self._used:
            candidate = f"{preferred}-{index}"
            index += 1
        self._used.add(candidate)
        return candidate

    def allocate_evidence_status(self) -> str:
        if "evidence-status" not in self._used:
            return self.allocate("evidence-status")
        return self.allocate("adapter-evidence-status")


def _section_heading(block_id: str, title: str) -> dict[str, str]:
    return {
        "id": block_id,
        "type": "markdown",
        "body": f"## {title}",
    }


def _markdown_block(block: ReportBlock) -> dict[str, str]:
    return {
        "id": block.block_id,
        "type": "markdown",
        "body": block.text or "",
    }


def _metric_cards(block: ReportBlock, artifact: MarivoReportArtifact) -> list[dict[str, Any]]:
    if block.dataset_id is None:
        raise ValueError(f"metric_strip block {block.block_id!r} requires dataset_id")
    cards: list[dict[str, Any]] = []
    for index, metric in enumerate(block.metrics):
        parsed = _metric_value_ref(metric.value_ref, block, artifact)
        cards.append(
            {
                "id": f"{block.block_id}-card-{index}",
                "description": block.subtitle,
                "dataset": block.dataset_id,
                "metrics": [
                    {
                        "label": metric.label,
                        "field": parsed.field,
                        "format": metric.format,
                        "signed": metric.signed,
                    }
                ],
            }
        )
    if not cards:
        value_refs = block.value_refs
        if not value_refs:
            raise ValueError(
                f"metric_strip block {block.block_id!r} requires metrics or value_refs"
            )
        metrics: list[dict[str, Any]] = []
        for index, value_ref in enumerate(value_refs):
            parsed = _metric_value_ref(value_ref, block, artifact)
            metrics.append(
                {
                    "label": block.title if index == 0 and block.title else parsed.field,
                    "field": parsed.field,
                    "format": "compact",
                    "signed": False,
                }
            )
        cards.append(
            {
                "id": f"{block.block_id}-card-0",
                "description": block.subtitle,
                "dataset": block.dataset_id,
                "metrics": metrics,
            }
        )
    return cards


def _metric_strip_block(
    block: ReportBlock, manifest: dict[str, Any], artifact: MarivoReportArtifact
) -> dict[str, Any]:
    cards = _metric_cards(block, artifact)
    manifest["cards"].extend(cards)
    return {
        "id": block.block_id,
        "type": "metric-strip",
        "cardIds": [card["id"] for card in cards],
    }


def _dataset_for_visual_block(block: ReportBlock, artifact: MarivoReportArtifact) -> Dataset:
    if block.dataset_id is None:
        raise ValueError(f"{block.block_type} block {block.block_id!r} requires dataset_id")
    dataset = artifact.datasets.get(block.dataset_id)
    if dataset is None:
        raise ValueError(
            f"{block.block_type} block {block.block_id!r} references missing dataset "
            f"{block.dataset_id!r}"
        )
    return dataset


def _require_available_sql_provenance(block: ReportBlock, dataset: Dataset) -> None:
    source = dataset.metadata.source_provenance
    if source.sql_status == "available" and source.sql:
        return
    raise ValueError(
        f"{block.block_type} block {block.block_id!r} native MCP chart/table blocks "
        "require available SQL provenance"
    )


def _validate_dataset_fields(block: ReportBlock, dataset: Dataset, fields: tuple[str, ...]) -> None:
    if not dataset.rows:
        raise ValueError(
            f"{block.block_type} block {block.block_id!r} references empty dataset "
            f"{dataset.dataset_id!r}"
        )
    for row_index, row in enumerate(dataset.rows):
        for field in fields:
            if field not in row:
                raise ValueError(
                    f"{block.block_type} block {block.block_id!r} references missing field "
                    f"{field!r} in dataset {dataset.dataset_id!r} row {row_index}"
                )


def _chart_block(
    block: ReportBlock, artifact: MarivoReportArtifact, manifest: dict[str, Any]
) -> dict[str, Any]:
    if block.chart is None:
        raise ValueError(f"chart block {block.block_id!r} requires chart")
    x_field = block.chart.fields.get("x", "").strip()
    y_field = block.chart.fields.get("y", "").strip()
    if not x_field or not y_field:
        raise ValueError(
            f"chart block {block.block_id!r} requires non-empty x/y encodings for MCP native charts"
        )
    dataset = _dataset_for_visual_block(block, artifact)
    _require_available_sql_provenance(block, dataset)
    field_refs = tuple(
        field_name.strip() for field_name in block.chart.fields.values() if field_name.strip()
    )
    _validate_dataset_fields(block, dataset, field_refs)
    chart_spec: dict[str, Any] = {
        "id": block.block_id,
        "title": block.title or block.block_id,
        "subtitle": block.subtitle,
        "type": block.chart.type,
        "dataset": dataset.dataset_id,
        "sourceId": _source_id_for_block(block),
        "encodings": {
            channel: {"field": field_name.strip()}
            for channel, field_name in block.chart.fields.items()
            if field_name.strip()
        },
    }
    if block.chart.options:
        chart_spec["surface"] = {"options": dict(block.chart.options)}
    manifest["charts"].append(chart_spec)
    return {
        "id": block.block_id,
        "type": "chart",
        "chartId": block.block_id,
    }


def _table_columns(block: ReportBlock, dataset: Dataset) -> list[dict[str, Any]]:
    if block.columns:
        _validate_dataset_fields(block, dataset, tuple(column.key for column in block.columns))
        return [
            {
                "field": column.key,
                "label": column.label,
                "type": column.type,
                "format": column.format,
            }
            for column in block.columns
        ]
    if not dataset.rows:
        raise ValueError(
            f"table block {block.block_id!r} cannot derive columns from empty dataset "
            f"{dataset.dataset_id!r}"
        )
    return [
        {
            "field": field,
            "label": field.replace("_", " ").title(),
            "type": "text",
            "format": None,
        }
        for field in dataset.rows[0]
    ]


def _table_block(
    block: ReportBlock, artifact: MarivoReportArtifact, manifest: dict[str, Any]
) -> dict[str, Any]:
    dataset = _dataset_for_visual_block(block, artifact)
    _require_available_sql_provenance(block, dataset)
    manifest["tables"].append(
        {
            "id": block.block_id,
            "title": block.title or block.block_id,
            "subtitle": block.subtitle,
            "dataset": dataset.dataset_id,
            "sourceId": _source_id_for_block(block),
            "columns": _table_columns(block, dataset),
        }
    )
    return {
        "id": block.block_id,
        "type": "table",
        "tableId": block.block_id,
    }


def _manifest_source_list(artifact: MarivoReportArtifact) -> list[dict[str, Any]]:
    return [_source_for_dataset(dataset) for dataset in artifact.datasets.values()]


def _snapshot_datasets(artifact: MarivoReportArtifact) -> dict[str, list[dict[str, Any]]]:
    return {
        dataset_id: [dict(row) for row in dataset.rows]
        for dataset_id, dataset in artifact.datasets.items()
    }


def _authored_manifest_block_ids(artifact: MarivoReportArtifact) -> set[str]:
    block_ids: set[str] = set()
    for section in artifact.report_spec.sections:
        block_ids.update(block.block_id for block in section.blocks)
    return block_ids


def _evidence_status_block(
    artifact: MarivoReportArtifact,
    block_id_allocator: _GeneratedBlockIdAllocator,
) -> dict[str, str] | None:
    status = artifact.manifest.evidence_status
    if status == "complete":
        return None
    return {
        "id": block_id_allocator.allocate_evidence_status(),
        "type": "markdown",
        "body": (
            "## Evidence Status\n"
            f"This Marivo report has `{status}` evidence. Review caveats and source details "
            "before acting on the recommendations."
        ),
    }


def to_mcp_artifact_payload(
    artifact: MarivoReportArtifact,
    *,
    target_schema: str = _DEFAULT_TARGET_SCHEMA,
) -> dict[str, Any]:
    """Convert a validated Marivo report artifact to an MCP report payload."""
    _raise_on_invalid(artifact)
    sources = _manifest_source_list(artifact)
    block_id_allocator = _GeneratedBlockIdAllocator(_authored_manifest_block_ids(artifact))
    manifest: dict[str, Any] = {
        "version": 1,
        "surface": "report",
        "title": artifact.manifest.title,
        "description": f"Marivo report artifact {artifact.manifest.report_id}",
        "blocks": [
            {
                "id": block_id_allocator.allocate("title"),
                "type": "markdown",
                "body": f"# {artifact.manifest.title}",
            }
        ],
        "cards": [],
        "charts": [],
        "tables": [],
        "sources": sources,
    }
    evidence_status_block = _evidence_status_block(artifact, block_id_allocator)
    if evidence_status_block is not None:
        manifest["blocks"].append(evidence_status_block)
    for section in artifact.report_spec.sections:
        manifest["blocks"].append(
            _section_heading(
                block_id_allocator.allocate(f"section-{section.section_id}"),
                section.title,
            )
        )
        for block in section.blocks:
            if block.block_type == "markdown":
                manifest["blocks"].append(_markdown_block(block))
            elif block.block_type == "metric_strip":
                manifest["blocks"].append(_metric_strip_block(block, manifest, artifact))
            elif block.block_type == "chart":
                manifest["blocks"].append(_chart_block(block, artifact, manifest))
            elif block.block_type == "table":
                manifest["blocks"].append(_table_block(block, artifact, manifest))
            else:
                manifest["blocks"].append(
                    {
                        "id": block.block_id,
                        "type": "markdown",
                        "body": _audit_markdown(block, artifact),
                    }
                )
    if not manifest["charts"]:
        raise ValueError("MCP report adapter requires at least one native chart block")
    snapshot = {
        "version": 1,
        "status": _snapshot_status(artifact.manifest.evidence_status),
        "datasets": _snapshot_datasets(artifact),
    }
    package_info = {
        "kind": "marivo_analysis_report_mcp_adapter",
        "target_schema": target_schema,
        "source_report_id": artifact.manifest.report_id,
        "source_export_id": artifact.manifest.export_id,
    }
    return {
        "manifest": manifest,
        "snapshot": snapshot,
        "package_info": package_info,
        "sources": sources,
    }


def materialize_mcp_adapter(
    artifact: MarivoReportArtifact,
    root: str | Path,
    *,
    target_schema: str = _DEFAULT_TARGET_SCHEMA,
    language: str | None = None,
) -> MarivoReportArtifact:
    """Write MCP adapter payload files and return the artifact with adapter metadata.

    If *language* is provided, it is stamped onto the manifest before writing
    so the written ``manifest.json`` reflects the intended report language.
    The manifest is only marked ``adapter_mcp.materialized=True`` once all
    adapter files have been written successfully.
    """
    manifest_for_payload: dict[str, Any] = {}
    if language is not None:
        manifest_for_payload["language"] = language
    staged = (
        artifact
        if not manifest_for_payload
        else artifact.model_copy(
            update={"manifest": artifact.manifest.model_copy(update=manifest_for_payload)}
        )
    )
    payload = to_mcp_artifact_payload(staged, target_schema=target_schema)
    package_root = Path(root)
    write_report_artifact(staged, package_root)
    adapter_root = package_root / "adapters" / "mcp"
    _write_json(adapter_root / "manifest.json", payload["manifest"])
    _write_json(adapter_root / "snapshot.json", payload["snapshot"])
    _write_json(adapter_root / "package_info.json", payload["package_info"])
    _write_json(adapter_root / "sources.json", payload["sources"])
    updated = staged.model_copy(
        update={
            "manifest": staged.manifest.model_copy(
                update={
                    "adapter_mcp": McpAdapterMetadata(
                        materialized=True,
                        target_schema=target_schema,
                        manifest="adapters/mcp/manifest.json",
                        snapshot="adapters/mcp/snapshot.json",
                    )
                }
            )
        }
    )
    write_report_artifact(updated, package_root)
    return updated
