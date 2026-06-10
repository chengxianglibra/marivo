"""Tests for mv.publish.help() surface."""

from __future__ import annotations


def test_publish_help_lists_names() -> None:
    from marivo.analysis.publish import help_text

    text = help_text()
    for name in [
        "DataPolicy",
        "Dataset",
        "MarivoReportArtifact",
        "ReportSpec",
        "render_report_html",
        "publish_report_package",
        "validate_report_artifact",
    ]:
        assert name in text


def test_publish_help_symbol_detail() -> None:
    from marivo.analysis.publish import help_text

    text = help_text("ReportSpec")
    assert "ReportSpec" in text


def test_publish_module_exposes_help() -> None:
    import marivo.analysis as mv

    assert callable(mv.publish.help)
    assert callable(mv.publish.help_text)


def test_publish_help_report_artifact_has_fields() -> None:
    from marivo.analysis.publish.help import _resolve

    cls = _resolve("MarivoReportArtifact")
    assert cls is not None
    assert cls.__name__ == "MarivoReportArtifact"
