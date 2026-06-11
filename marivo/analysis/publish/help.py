"""mv.publish.help - agent-facing introspection of the publish surface."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

from marivo.introspection.surface import Surface, render

_SUMMARIES: dict[str, str] = {
    "DataPolicy": "row-level data inclusion policy for a report package",
    "Dataset": "a named dataset with metadata, rows, and optional provenance",
    "DatasetMetadata": "metadata for a report dataset (grain, row count, provenance)",
    "Flow": "ordered list of analysis steps that produced a report artifact",
    "FlowStep": "a single step in the analysis flow of a report artifact",
    "GroundedClaim": "a claim in a report grounded to evidence, steps, or datasets",
    "Grounding": "collection of grounded claims for a report artifact",
    "LocalFilesystemTarget": "local filesystem publish target with a directory path",
    "MarivoReportArtifact": "top-level report artifact with manifest, spec, flow, grounding, and datasets",
    "McpAdapterMetadata": "MCP adapter materialization metadata for a report package",
    "PublishConfig": "publish configuration resolved from marivo.publish.toml",
    "PublishReportResult": "result of a deterministic publish operation",
    "PublishTarget": "abstract publish target (local filesystem or future cloud)",
    "ReportBlock": "a content block within a report section",
    "ReportChartSpec": "chart specification within a report block",
    "ReportColumn": "column definition for a table or dataset within a report",
    "ReportManifest": "report package manifest (id, version, title, hashes)",
    "ReportMetric": "a metric value reference within a report block",
    "ReportPackageValidationIssue": "a single validation issue found in a report package",
    "ReportPackageValidationResult": "result of validating a report package",
    "ReportSection": "a section within a report spec",
    "ReportSpec": "report structure specification (title and sections)",
    "SourceProvenance": "source provenance metadata for a dataset or artifact",
    "export_report_json_schema": "export the JSON Schema for the report artifact model",
    "load_report_artifact": "load a report artifact from a package directory",
    "render_report_html": "render a standalone HTML string from a report artifact",
    "to_html_report_payload": "build the HTML renderer payload without writing files",
    "to_mcp_artifact_payload": "build the MCP adapter payload without writing files",
    "validate_report_artifact": "validate a report artifact and return issues",
}

_SEE_ALSO: dict[str, tuple[str, ...]] = {
    "MarivoReportArtifact": (
        "mv.publish.help('ReportManifest')",
        "mv.publish.help('ReportSpec')",
    ),
    "ReportManifest": ("mv.publish.help('MarivoReportArtifact')",),
    "ReportSpec": ("mv.publish.help('MarivoReportArtifact')",),
}


def _resolve(symbol: str) -> Any | None:
    import marivo.analysis.publish as pub

    if hasattr(pub, symbol):
        return getattr(pub, symbol)
    return None


@lru_cache(maxsize=1)
def _surface() -> Surface:
    import marivo.analysis.publish as pub

    all_names = tuple(pub.__all__)
    summaries = {name: _SUMMARIES.get(name, "") for name in all_names}
    return Surface(
        name="marivo.analysis.publish",
        all_names=all_names,
        summaries=summaries,
        resolve=_resolve,
        catalog={},
        topics={},
        see_also=_SEE_ALSO,
    )


def _format_top_level_text() -> str:
    data = cast("dict[str, object]", render(_surface(), None, "json"))
    entries = cast("list[dict[str, str]]", data["entries"])
    lines = ["marivo.analysis.publish - top-level entries:", ""]
    for entry in entries:
        lines.append(f"  mv.publish.{entry['name']:<28} [{entry['kind']}]  {entry['summary']}")
    lines.append("")
    lines.append('Call mv.publish.help("<name>") for detail on any entry.')
    lines.append("")
    lines.append("For report registration and publishing, use session methods:")
    lines.append("  session.save_report(artifact)   — persist a report under the session")
    lines.append("  session.validate_report(id)     — validate a registered report")
    lines.append("  session.publish_report(id)      — publish a registered report")
    return "\n".join(lines)


def help_text(symbol: str | None = None) -> str:
    """Return help text as a string instead of printing it."""

    normalized = None if symbol == "" else symbol
    if normalized is None:
        return _format_top_level_text()
    return cast("str", render(_surface(), normalized, "text"))


def help(symbol: str | None = None) -> None:
    """Print bounded agent-facing help for the publish surface and return None.

    Args:
        symbol: Symbol name (e.g. "ReportSpec", "PublishTarget").
            None prints the top-level surface listing.

    Returns:
        None

    Example:
        >>> mv.publish.help()
        >>> mv.publish.help("ReportSpec")
    """

    normalized = None if symbol == "" else symbol
    print(help_text(normalized))
