"""Session-scoped report registration, validation, and publishing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

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
    ReportChartSpec,
    ReportManifest,
    ReportSection,
    ReportSpec,
    SourceProvenance,
)
from marivo.analysis.session._layout import PersistenceLayout
from marivo.analysis.session._store import SessionStore
from marivo.analysis.session.core import Session


def _now():
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _valid_artifact() -> MarivoReportArtifact:
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
                sql_reason="Typed intent did not expose SQL.",
            ),
            metric_definitions=("sales.revenue = sum(order_amount)",),
            filters=(),
            data_policy=DataPolicy(),
        ),
        rows=({"metric": "revenue", "value": 125.0},),
    )
    return MarivoReportArtifact(
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
                ReportSection(
                    section_id="caveats",
                    section_type="caveat",
                    title="Caveats",
                    blocks=(
                        ReportBlock(
                            block_id="caveat_text",
                            block_type="markdown",
                            text="No material caveats were found.",
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


def _artifact_with_chart() -> MarivoReportArtifact:
    """A valid artifact that includes a chart block for MCP adapter tests.

    The chart dataset uses available SQL provenance so the MCP adapter
    accepts it.
    """
    artifact = _valid_artifact()
    # Update the dataset to have available SQL provenance for chart blocks.
    updated_dataset = artifact.datasets["headline_metrics"].model_copy(
        update={
            "metadata": artifact.datasets["headline_metrics"].metadata.model_copy(
                update={
                    "source_provenance": artifact.datasets[
                        "headline_metrics"
                    ].metadata.source_provenance.model_copy(
                        update={
                            "sql_status": "available",
                            "sql": "SELECT metric, value FROM headline_metrics",
                            "sql_reason": None,
                        }
                    )
                }
            )
        }
    )
    chart_section = ReportSection(
        section_id="charts",
        section_type="analysis_step",
        title="Charts",
        blocks=(
            ReportBlock(
                block_id="chart_text",
                block_type="markdown",
                text="A chart follows.",
            ),
            ReportBlock(
                block_id="rev_chart",
                block_type="chart",
                dataset_id="headline_metrics",
                chart=ReportChartSpec(
                    type="bar",
                    fields={"x": "metric", "y": "value"},
                ),
                narrative_ref="chart_text",
            ),
        ),
    )
    return artifact.model_copy(
        update={
            "datasets": {"headline_metrics": updated_dataset},
            "report_spec": artifact.report_spec.model_copy(
                update={
                    "sections": (
                        artifact.report_spec.sections[0],
                        artifact.report_spec.sections[1],
                        chart_section,
                    )
                }
            ),
        }
    )


def _session(tmp_path, *, read_only: bool = False) -> Session:
    layout = PersistenceLayout(project_root=tmp_path, session_id="sess_t01")
    store = SessionStore(project_root=tmp_path)
    # Insert a session row with the known ID so foreign key constraints pass.
    with store._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, name, question, cwd, default_calendar, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "sess_t01",
                "demo",
                "q",
                str(tmp_path),
                None,
                "2026-05-24T10:00:00+00:00",
                "2026-05-24T10:00:00+00:00",
            ),
        )
    return Session(
        id="sess_t01",
        name="demo",
        question="q",
        cwd=tmp_path,
        project_root=tmp_path,
        created_at=_now(),
        updated_at=_now(),
        backend_factory=None if read_only else (lambda name: object()),
        layout=layout,
        semantic_project=None,
        store=store,
    )


# ---------------------------------------------------------------------------
# save_report
# ---------------------------------------------------------------------------


def test_save_report_writes_package_files_under_session_reports_dir(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _valid_artifact()
    registration = s.save_report(artifact)

    # Package dir should exist under session reports
    assert registration.package_dir.is_dir()
    assert registration.package_dir == s._layout.reports_dir / registration.report_id
    # Core package files should exist
    assert (registration.package_dir / "manifest.json").is_file()
    assert (registration.package_dir / "report_spec.json").is_file()
    assert (registration.package_dir / "flow.json").is_file()
    assert (registration.package_dir / "grounding.json").is_file()


def test_save_report_generates_report_id_when_omitted(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _valid_artifact()
    registration = s.save_report(artifact)

    assert registration.report_id
    assert registration.report_id.startswith("rpt_")


def test_save_report_uses_provided_report_id(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _valid_artifact()
    registration = s.save_report(artifact, report_id="my_custom_id")

    assert registration.report_id == "my_custom_id"
    assert registration.package_dir == s._layout.reports_dir / "my_custom_id"


def test_save_report_rejects_unsafe_report_ids(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _valid_artifact()

    for unsafe_id in ["", ".", "..", "a/b", "a\\b"]:
        with pytest.raises(ValueError, match="report_id"):
            s.save_report(artifact, report_id=unsafe_id)


def test_save_report_writes_package_bytes_before_inserting_store_row(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _valid_artifact()
    registration = s.save_report(artifact)

    # The store row should have been created
    row = s._store.get_report(s.id, registration.report_id)
    assert row is not None

    # Files should be present
    assert (registration.package_dir / "manifest.json").is_file()


def test_save_report_records_entrypoint_package_hash_and_relative_dir(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _valid_artifact()
    registration = s.save_report(artifact)

    # Check the immutable dataclass fields
    assert registration.entrypoint  # should be non-empty
    assert registration.content_hash.startswith("sha256:")
    assert registration.package_dir.is_absolute()

    # Check the store row matches
    row = s._store.get_report(s.id, registration.report_id)
    assert row is not None
    assert row["entrypoint"] == registration.entrypoint
    assert row["package_hash"] == registration.content_hash
    # package_dir in store is project-relative
    assert row["package_dir"]


def test_save_report_html_adapter_writes_index_html(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _valid_artifact()
    registration = s.save_report(artifact, adapter="html")

    assert (registration.package_dir / "index.html").is_file()
    html = (registration.package_dir / "index.html").read_text(encoding="utf-8")
    assert "<html" in html.lower()


def test_save_report_mcp_adapter_writes_adapter_files(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _artifact_with_chart()
    registration = s.save_report(artifact, adapter="mcp")

    assert (registration.package_dir / "adapters" / "mcp" / "manifest.json").is_file()
    assert (registration.package_dir / "adapters" / "mcp" / "snapshot.json").is_file()


def test_save_report_package_adapter_writes_core_files_only(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _valid_artifact()
    registration = s.save_report(artifact, adapter="package")

    # Core package files exist but no index.html or adapters
    assert (registration.package_dir / "manifest.json").is_file()
    assert not (registration.package_dir / "index.html").is_file()
    assert not (registration.package_dir / "adapters").is_dir()


# ---------------------------------------------------------------------------
# validate_report
# ---------------------------------------------------------------------------


def test_validate_report_resolves_package_dir_from_store(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _valid_artifact()
    registration = s.save_report(artifact)

    result = s.validate_report(registration.report_id)
    assert result.ok is True
    assert result.issues == ()


def test_validate_report_missing_report_id_raises_with_guidance(tmp_path) -> None:
    from marivo.analysis.errors import ReportPublishError

    s = _session(tmp_path)

    with pytest.raises(ReportPublishError):
        s.validate_report("nonexistent_report")


def test_validate_report_validates_registered_package(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _valid_artifact()
    registration = s.save_report(artifact, adapter="html")

    result = s.validate_report(registration.report_id)
    assert result.ok is True


# ---------------------------------------------------------------------------
# publish_report
# ---------------------------------------------------------------------------


def test_publish_report_resolves_package_dir_from_store_and_publishes(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _valid_artifact()
    registration = s.save_report(artifact, adapter="html")

    base = tmp_path / "published"
    result = s.publish_report(
        registration.report_id,
        target=str(base),
        project_root=tmp_path,
    )

    assert result.uri.startswith("file://")
    assert result.content_hash.startswith("sha256:")
    assert result.exported_by
    assert result.exported_at
    assert result.file_count >= 1


def test_publish_report_records_published_url(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _valid_artifact()
    registration = s.save_report(artifact, adapter="html")

    base = tmp_path / "published"
    result = s.publish_report(
        registration.report_id,
        target=str(base),
        project_root=tmp_path,
    )

    # The store should record the published URL
    row = s._store.get_report(s.id, registration.report_id)
    assert row is not None
    assert row["published_url"] is not None
    assert row["published_url"] == result.uri


def test_publish_report_result_includes_required_fields(tmp_path) -> None:
    s = _session(tmp_path)
    artifact = _valid_artifact()
    registration = s.save_report(artifact, adapter="html")

    base = tmp_path / "published"
    result = s.publish_report(
        registration.report_id,
        exported_by="alice",
        exported_at="2026-06-06T00:00:00Z",
        target=str(base),
        project_root=tmp_path,
    )

    assert result.uri
    assert result.content_hash.startswith("sha256:")
    assert result.exported_by == "alice"
    assert result.exported_at == "2026-06-06T00:00:00Z"
    assert isinstance(result.file_count, int)
    assert result.file_count > 0


def test_publish_report_missing_report_id_raises(tmp_path) -> None:
    from marivo.analysis.errors import ReportPublishError

    s = _session(tmp_path)

    with pytest.raises(ReportPublishError):
        s.publish_report("nonexistent_report", target=str(tmp_path / "out"))


def test_publish_report_validates_package_before_publishing(tmp_path) -> None:
    from marivo.analysis.errors import ReportPublishValidationError

    s = _session(tmp_path)
    artifact = _valid_artifact()
    registration = s.save_report(artifact, adapter="package")

    # Package adapter does not write index.html; publish should fail validation
    base = tmp_path / "published"
    with pytest.raises(ReportPublishValidationError):
        s.publish_report(
            registration.report_id,
            target=str(base),
            project_root=tmp_path,
        )


# ---------------------------------------------------------------------------
# Import/surface: directory-based helpers must not be public
# ---------------------------------------------------------------------------


def test_publish_does_not_export_write_report_artifact() -> None:
    import marivo.analysis.publish as pub

    assert not hasattr(pub, "write_report_artifact")


def test_publish_does_not_export_materialize_html_adapter() -> None:
    import marivo.analysis.publish as pub

    assert not hasattr(pub, "materialize_html_adapter")


def test_publish_does_not_export_materialize_mcp_adapter() -> None:
    import marivo.analysis.publish as pub

    assert not hasattr(pub, "materialize_mcp_adapter")


def test_publish_does_not_export_publish_report_package() -> None:
    import marivo.analysis.publish as pub

    assert not hasattr(pub, "publish_report_package")
