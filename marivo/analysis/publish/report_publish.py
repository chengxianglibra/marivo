"""Publish a staged Marivo report package to a publish target."""

from __future__ import annotations

import getpass
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from marivo.analysis.errors import (
    ReportPublishAttributionError,
    ReportPublishTargetExistsError,
    ReportPublishValidationError,
)
from marivo.analysis.publish.publish_config import resolve_publish_config, resolve_publish_prefix
from marivo.analysis.publish.publish_hash import compute_package_hash
from marivo.analysis.publish.publish_secrets import scan_package_for_secrets
from marivo.analysis.publish.publish_targets import LocalFilesystemTarget, PublishTarget
from marivo.analysis.publish.report_models import ReportManifest
from marivo.analysis.publish.report_package import load_report_artifact
from marivo.analysis.publish.report_validation import validate_report_artifact

_MANIFEST_FILE = "manifest.json"


@dataclass(frozen=True)
class PublishReportResult:
    uri: str
    content_hash: str
    exported_by: str
    exported_at: str
    file_count: int


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_segment(value: str, label: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ReportPublishAttributionError(
            message=f"{label} is not a safe publish path segment: {value!r}",
            details={"label": label, "value": value},
        )
    return value


def _resolve_exporter(exported_by: str | None) -> str:
    if exported_by is not None and not exported_by.strip():
        raise ReportPublishAttributionError(
            message="exported_by must be a non-empty exporter name",
            details={"label": "exported_by"},
        )
    return _safe_segment((exported_by or getpass.getuser()).strip(), "exported_by")


def _render_manifest_json(manifest: ReportManifest) -> bytes:
    payload = json.dumps(
        manifest.model_dump(mode="json"), allow_nan=False, indent=2, sort_keys=True
    )
    return (payload + "\n").encode("utf-8")


def publish_report_package(
    package_dir: str | Path,
    *,
    exported_by: str | None = None,
    exported_at: str | None = None,
    target: str | PublishTarget | None = None,
    overwrite: bool = False,
    project_root: str | Path | None = None,
) -> PublishReportResult:
    """Validate and publish a staged report package to a publish target.

    The package directory must already contain the canonical report files (a
    materialized adapter, e.g. ``index.html``, plus the core JSON files). The
    library validates and publishes deterministically; it does not author
    narrative, HTML, or replay scripts.
    """
    package_root = Path(package_dir)

    artifact = load_report_artifact(package_root)
    validation = validate_report_artifact(artifact)
    if not validation.ok:
        details = "; ".join(
            f"{issue.check} at {issue.location}: {issue.message}" for issue in validation.issues
        )
        raise ReportPublishValidationError(
            message=f"report package failed validation: {details}",
            details={"issue_count": len(validation.issues)},
        )

    files = sorted(path for path in package_root.rglob("*") if path.is_file())
    rel_paths = [path.relative_to(package_root).as_posix() for path in files]

    for name, rel in artifact.manifest.entrypoints.items():
        if rel not in rel_paths:
            raise ReportPublishValidationError(
                message=f"declared entrypoint {name!r} file is missing from package: {rel}",
                details={"entrypoint": name, "path": rel},
            )

    policy = artifact.manifest.data_policy
    if (policy.row_level_data == "omitted" or policy.frame_snapshots == "omitted") and any(
        rel == "frames" or rel.startswith("frames/") for rel in rel_paths
    ):
        raise ReportPublishValidationError(
            message="data_policy omits row-level/frame data but package includes frames/",
            details={
                "row_level_data": policy.row_level_data,
                "frame_snapshots": policy.frame_snapshots,
            },
        )

    secret_issues = scan_package_for_secrets(package_root)
    if secret_issues:
        first = secret_issues[0]
        raise ReportPublishValidationError(
            message=(
                f"package contains a possible secret in {first.rel_path}:{first.lineno} "
                f"({first.check})"
            ),
            details={"issue_count": len(secret_issues)},
        )

    resolved_by = _resolve_exporter(exported_by)
    resolved_at = exported_at or _utc_now_iso()
    content_hash = compute_package_hash(package_root)

    stamped = artifact.manifest.model_copy(
        update={
            "exported_by": resolved_by,
            "exported_at": resolved_at,
            "content_hash": content_hash,
        }
    )
    manifest_bytes = _render_manifest_json(stamped)

    if isinstance(target, PublishTarget):
        tgt: PublishTarget = target
        prefix_template = resolve_publish_prefix(project_root=project_root)
    else:
        config = resolve_publish_config(target, project_root=project_root)
        tgt = LocalFilesystemTarget(config.base)
        prefix_template = config.prefix_template

    prefix = prefix_template.format(username=resolved_by)
    if f"/{resolved_by}/" not in f"/{prefix}/":
        raise ReportPublishAttributionError(
            message=f"resolved publish prefix {prefix!r} does not include exporter {resolved_by!r}",
            details={"prefix": prefix, "exported_by": resolved_by},
        )

    report_id = _safe_segment(artifact.manifest.report_id, "report_id")
    export_id = _safe_segment(artifact.manifest.export_id, "export_id")
    dest_prefix = f"{prefix}/analysis-reports/{report_id}/{export_id}"

    if tgt.exists(f"{dest_prefix}/{_MANIFEST_FILE}") and not overwrite:
        raise ReportPublishTargetExistsError(
            message=f"publish target already has a completed manifest: {dest_prefix}",
            details={"dest_prefix": dest_prefix},
        )

    file_count = 0
    for rel, path in zip(rel_paths, files, strict=True):
        if rel == _MANIFEST_FILE:
            continue
        tgt.put_file(f"{dest_prefix}/{rel}", path.read_bytes())
        file_count += 1
    tgt.put_file(f"{dest_prefix}/{_MANIFEST_FILE}", manifest_bytes)
    file_count += 1

    return PublishReportResult(
        uri=tgt.uri(dest_prefix),
        content_hash=content_hash,
        exported_by=resolved_by,
        exported_at=resolved_at,
        file_count=file_count,
    )
