from __future__ import annotations

import pytest

from tests.test_analysis_report_artifact_validation import _valid_artifact


def test_report_manifest_publish_fields_default_to_none() -> None:
    manifest = _valid_artifact().manifest
    assert manifest.exported_by is None
    assert manifest.exported_at is None
    assert manifest.content_hash is None


def test_report_manifest_publish_fields_round_trip() -> None:
    from marivo.analysis.publish import ReportManifest

    manifest = _valid_artifact().manifest.model_copy(
        update={
            "exported_by": "alice",
            "exported_at": "2026-06-06T00:00:00Z",
            "content_hash": "sha256:abc",
        }
    )
    restored = ReportManifest.model_validate(manifest.model_dump(mode="json"))

    assert restored == manifest
    assert restored.exported_by == "alice"
    assert restored.content_hash == "sha256:abc"


def _staged_package(tmp_path):
    """Materialize a valid report package (manifest, core files, datasets, index.html)."""
    from marivo.analysis.publish import materialize_html_adapter

    package_dir = tmp_path / "staging"
    materialize_html_adapter(_valid_artifact(), package_dir)
    return package_dir


def test_publish_report_package_writes_user_scoped_layout(tmp_path) -> None:
    from marivo.analysis.publish.report_publish import publish_report_package

    package_dir = _staged_package(tmp_path)
    base = tmp_path / "published"

    result = publish_report_package(
        package_dir,
        exported_by="alice",
        exported_at="2026-06-06T00:00:00Z",
        target=str(base),
        project_root=tmp_path,
    )

    dest = base / "marivo/users/alice/analysis-reports/revenue_review/exp_20260605_120000"
    assert (dest / "manifest.json").is_file()
    assert (dest / "index.html").is_file()
    assert (dest / "datasets" / "headline_metrics.json").is_file()
    assert result.exported_by == "alice"
    assert result.exported_at == "2026-06-06T00:00:00Z"
    assert result.content_hash.startswith("sha256:")
    assert result.file_count >= 5
    assert result.uri.startswith("file://")


def test_publish_stamps_manifest_with_attribution_and_hash(tmp_path) -> None:
    import json

    from marivo.analysis.publish.report_publish import publish_report_package

    package_dir = _staged_package(tmp_path)
    base = tmp_path / "published"

    result = publish_report_package(
        package_dir,
        exported_by="alice",
        exported_at="2026-06-06T00:00:00Z",
        target=str(base),
        project_root=tmp_path,
    )

    dest = base / "marivo/users/alice/analysis-reports/revenue_review/exp_20260605_120000"
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["exported_by"] == "alice"
    assert manifest["exported_at"] == "2026-06-06T00:00:00Z"
    assert manifest["content_hash"] == result.content_hash


def test_publish_content_hash_matches_recompute_excluding_manifest(tmp_path) -> None:
    from marivo.analysis.publish.publish_hash import compute_package_hash
    from marivo.analysis.publish.report_publish import publish_report_package

    package_dir = _staged_package(tmp_path)
    base = tmp_path / "published"

    result = publish_report_package(
        package_dir,
        exported_by="alice",
        exported_at="2026-06-06T00:00:00Z",
        target=str(base),
        project_root=tmp_path,
    )

    dest = base / "marivo/users/alice/analysis-reports/revenue_review/exp_20260605_120000"
    assert compute_package_hash(dest) == result.content_hash


def test_publish_writes_manifest_last(tmp_path) -> None:
    from marivo.analysis.publish.report_publish import publish_report_package

    package_dir = _staged_package(tmp_path)

    class _Recorder:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def uri(self, rel_path: str) -> str:
            return f"mem://{rel_path}"

        def exists(self, rel_path: str) -> bool:
            return False

        def put_file(self, rel_path: str, data: bytes) -> None:
            self.writes.append(rel_path)

    recorder = _Recorder()
    publish_report_package(
        package_dir,
        exported_by="alice",
        exported_at="2026-06-06T00:00:00Z",
        target=recorder,
        project_root=tmp_path,
    )

    assert recorder.writes[-1].endswith("manifest.json")
    assert sum(name.endswith("manifest.json") for name in recorder.writes) == 1


def test_publish_rejects_invalid_artifact(tmp_path) -> None:
    from marivo.analysis.errors import ReportPublishValidationError
    from marivo.analysis.publish import load_report_artifact, write_report_artifact
    from marivo.analysis.publish.report_publish import publish_report_package

    package_dir = _staged_package(tmp_path)
    artifact = load_report_artifact(package_dir)
    # Drop the executive_summary section; grounding still references section "exec".
    broken = artifact.model_copy(
        update={
            "report_spec": artifact.report_spec.model_copy(
                update={"sections": (artifact.report_spec.sections[1],)}
            )
        }
    )
    write_report_artifact(broken, package_dir)

    with pytest.raises(ReportPublishValidationError):
        publish_report_package(
            package_dir,
            exported_by="alice",
            exported_at="t",
            target=str(tmp_path / "out"),
            project_root=tmp_path,
        )


def test_publish_rejects_missing_entrypoint(tmp_path) -> None:
    from marivo.analysis.errors import ReportPublishValidationError
    from marivo.analysis.publish import write_report_artifact
    from marivo.analysis.publish.report_publish import publish_report_package

    package_dir = tmp_path / "staging"
    # write_report_artifact does NOT create index.html, but the manifest declares it.
    write_report_artifact(_valid_artifact(), package_dir)

    with pytest.raises(ReportPublishValidationError):
        publish_report_package(
            package_dir,
            exported_by="alice",
            exported_at="t",
            target=str(tmp_path / "out"),
            project_root=tmp_path,
        )


def test_publish_rejects_packaged_secret(tmp_path) -> None:
    from marivo.analysis.errors import ReportPublishValidationError
    from marivo.analysis.publish.report_publish import publish_report_package

    package_dir = _staged_package(tmp_path)
    (package_dir / "replay.py").write_text('token = "abcd1234secret"\n', encoding="utf-8")

    with pytest.raises(ReportPublishValidationError):
        publish_report_package(
            package_dir,
            exported_by="alice",
            exported_at="t",
            target=str(tmp_path / "out"),
            project_root=tmp_path,
        )


def test_publish_rejects_frames_when_policy_omits_row_level(tmp_path) -> None:
    from marivo.analysis.errors import ReportPublishValidationError
    from marivo.analysis.publish.report_publish import publish_report_package

    package_dir = _staged_package(tmp_path)
    frames_dir = package_dir / "frames"
    frames_dir.mkdir()
    (frames_dir / "snapshot.parquet").write_bytes(b"PAR1")

    with pytest.raises(ReportPublishValidationError):
        publish_report_package(
            package_dir,
            exported_by="alice",
            exported_at="t",
            target=str(tmp_path / "out"),
            project_root=tmp_path,
        )


def test_publish_rejects_empty_exported_by(tmp_path) -> None:
    from marivo.analysis.errors import ReportPublishAttributionError
    from marivo.analysis.publish.report_publish import publish_report_package

    package_dir = _staged_package(tmp_path)

    with pytest.raises(ReportPublishAttributionError):
        publish_report_package(
            package_dir,
            exported_by="",
            exported_at="t",
            target=str(tmp_path / "out"),
            project_root=tmp_path,
        )


def test_publish_rejects_prefix_without_username(tmp_path) -> None:
    from marivo.analysis.errors import ReportPublishAttributionError
    from marivo.analysis.publish.report_publish import publish_report_package

    (tmp_path / "marivo.publish.toml").write_text(
        '[storage.local]\nprefix = "reports/fixed"\n', encoding="utf-8"
    )
    package_dir = _staged_package(tmp_path)

    with pytest.raises(ReportPublishAttributionError):
        publish_report_package(
            package_dir,
            exported_by="alice",
            exported_at="t",
            target=str(tmp_path / "out"),
            project_root=tmp_path,
        )


def test_publish_is_immutable_unless_overwrite(tmp_path) -> None:
    from marivo.analysis.errors import ReportPublishTargetExistsError
    from marivo.analysis.publish.report_publish import publish_report_package

    package_dir = _staged_package(tmp_path)
    base = tmp_path / "published"

    publish_report_package(
        package_dir,
        exported_by="alice",
        exported_at="t",
        target=str(base),
        project_root=tmp_path,
    )

    with pytest.raises(ReportPublishTargetExistsError):
        publish_report_package(
            package_dir,
            exported_by="alice",
            exported_at="t",
            target=str(base),
            project_root=tmp_path,
        )

    result = publish_report_package(
        package_dir,
        exported_by="alice",
        exported_at="t",
        target=str(base),
        overwrite=True,
        project_root=tmp_path,
    )
    assert result.exported_by == "alice"


def test_publish_public_api_is_exported() -> None:
    import marivo.analysis as mv
    from marivo.analysis.publish import (
        LocalFilesystemTarget,
        PublishConfig,
        PublishReportResult,
        PublishTarget,
        SecretScanIssue,
        compute_package_hash,
        publish_report_package,
        resolve_publish_config,
        resolve_publish_prefix,
        scan_package_for_secrets,
    )

    assert callable(publish_report_package)
    assert callable(compute_package_hash)
    assert callable(scan_package_for_secrets)
    assert callable(resolve_publish_config)
    assert callable(resolve_publish_prefix)
    assert SecretScanIssue is not None
    assert mv.publish.publish_report_package is publish_report_package
    assert mv.publish.PublishReportResult is PublishReportResult
    assert mv.publish.PublishConfig is PublishConfig
    assert mv.publish.PublishTarget is PublishTarget
    assert mv.publish.LocalFilesystemTarget is LocalFilesystemTarget
