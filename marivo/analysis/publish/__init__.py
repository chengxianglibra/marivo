"""marivo.analysis publishing helpers (deterministic, file/package oriented)."""

from __future__ import annotations

from marivo.analysis.publish.publish_config import (
    PublishConfig,
    resolve_publish_config,
    resolve_publish_prefix,
)
from marivo.analysis.publish.publish_hash import compute_package_hash
from marivo.analysis.publish.publish_secrets import SecretScanIssue, scan_package_for_secrets
from marivo.analysis.publish.publish_targets import LocalFilesystemTarget, PublishTarget
from marivo.analysis.publish.replay_check import (
    ReplayCheckIssue,
    ReplayCheckResult,
    static_check_replay,
)
from marivo.analysis.publish.report_html_adapter import (
    materialize_html_adapter,
    render_report_html,
    to_html_report_payload,
)
from marivo.analysis.publish.report_mcp_adapter import (
    materialize_mcp_adapter,
    to_mcp_artifact_payload,
)
from marivo.analysis.publish.report_models import (
    DataPolicy,
    Dataset,
    DatasetMetadata,
    Flow,
    FlowStep,
    GroundedClaim,
    Grounding,
    MarivoReportArtifact,
    McpAdapterMetadata,
    ReportBlock,
    ReportChartSpec,
    ReportColumn,
    ReportManifest,
    ReportMetric,
    ReportPackageValidationIssue,
    ReportPackageValidationResult,
    ReportSection,
    ReportSpec,
    SourceProvenance,
    export_report_json_schema,
)
from marivo.analysis.publish.report_package import load_report_artifact, write_report_artifact
from marivo.analysis.publish.report_publish import PublishReportResult, publish_report_package
from marivo.analysis.publish.report_validation import validate_report_artifact

__all__ = [
    "DataPolicy",
    "Dataset",
    "DatasetMetadata",
    "Flow",
    "FlowStep",
    "GroundedClaim",
    "Grounding",
    "LocalFilesystemTarget",
    "MarivoReportArtifact",
    "McpAdapterMetadata",
    "PublishConfig",
    "PublishReportResult",
    "PublishTarget",
    "ReplayCheckIssue",
    "ReplayCheckResult",
    "ReportBlock",
    "ReportChartSpec",
    "ReportColumn",
    "ReportManifest",
    "ReportMetric",
    "ReportPackageValidationIssue",
    "ReportPackageValidationResult",
    "ReportSection",
    "ReportSpec",
    "SecretScanIssue",
    "SourceProvenance",
    "compute_package_hash",
    "export_report_json_schema",
    "load_report_artifact",
    "materialize_html_adapter",
    "materialize_mcp_adapter",
    "publish_report_package",
    "render_report_html",
    "resolve_publish_config",
    "resolve_publish_prefix",
    "scan_package_for_secrets",
    "static_check_replay",
    "to_html_report_payload",
    "to_mcp_artifact_payload",
    "validate_report_artifact",
    "write_report_artifact",
]
