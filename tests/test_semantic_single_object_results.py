from __future__ import annotations

from dataclasses import fields

import marivo.semantic as ms
from marivo.semantic.dtos import VerifyResult
from marivo.semantic.readiness import ReadinessReport
from marivo.semantic.richness import RichnessReport


def _make_readiness_report() -> ReadinessReport:
    from marivo.semantic.readiness import ReadinessInputSummary

    return ReadinessReport(
        status="ready_with_warnings",
        analysis_ready_refs=(ms.ref.metric("sales.revenue"),),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(datasources=(), refs=(), tables=()),
        checked_at="2026-06-09T00:00:00Z",
    )


def _make_richness_report() -> RichnessReport:
    return RichnessReport(gaps=(), checked_at="2026-06-09T00:00:00Z")


def test_verify_result_has_exact_static_contract() -> None:
    result = VerifyResult(
        status="passed",
        ref="sales.orders",
        kind="entity",
        validation_level="static",
        runtime_checked=False,
        issues=(),
        warnings=(),
    )

    assert tuple(field.name for field in fields(VerifyResult)) == (
        "status",
        "ref",
        "kind",
        "validation_level",
        "runtime_checked",
        "issues",
        "warnings",
    )
    assert result.validation_level == "static"
    assert result.runtime_checked is False
    assert not hasattr(result, "scope")
    assert not hasattr(result, "scan")


# --- ReadinessReport ---


def test_readiness_report_repr_is_one_line():
    r = repr(_make_readiness_report())
    assert r.count("\n") == 0
    assert "ReadinessReport" in r
    assert "call .show() to inspect" in r


def test_readiness_report_repr_includes_status():
    r = repr(_make_readiness_report())
    assert "ready_with_warnings" in r


def test_readiness_report_render_returns_str_no_stdout(capsys):
    report = _make_readiness_report()
    result = report.render()
    assert isinstance(result, str)
    assert capsys.readouterr().out == ""


def test_readiness_report_render_does_not_end_with_newline():
    assert not _make_readiness_report().render().endswith("\n")


def test_readiness_report_render_contains_identity_and_available():
    rendered = _make_readiness_report().render()
    assert rendered == "\n".join(
        [
            "ReadinessReport status=ready_with_warnings issues=0",
            "analysis_ready: metric:sales.revenue",
            "checked_at: 2026-06-09T00:00:00Z",
            "available:",
            "- .render()",
            "- .to_dict()",
            "- .contract()",
            "- .preview_required_refs",
            "- .analysis_ready_inputs",
        ]
    )


def test_readiness_report_render_available_not_empty():
    rendered = _make_readiness_report().render()
    lines = rendered.splitlines()
    avail_idx = next(i for i, ln in enumerate(lines) if ln == "available:")
    assert avail_idx < len(lines) - 1


def test_readiness_report_show_prints_render_plus_newline(capsys):
    report = _make_readiness_report()
    result = report.show()
    captured = capsys.readouterr()
    assert result is None
    assert captured.out == report.render() + "\n"


def test_readiness_report_does_not_implement_summary_or_preview():
    report_type = type(_make_readiness_report())
    assert not callable(getattr(report_type, "summary", None))
    assert not callable(getattr(report_type, "preview", None))


# --- RichnessReport ---


def test_richness_report_repr_is_one_line():
    r = repr(_make_richness_report())
    assert r.count("\n") == 0
    assert "RichnessReport" in r
    assert "call .show() to inspect" in r


def test_richness_report_render_no_stdout(capsys):
    _make_richness_report().render()
    assert capsys.readouterr().out == ""


def test_richness_report_render_does_not_end_with_newline():
    assert not _make_richness_report().render().endswith("\n")


def test_richness_report_show_returns_none(capsys):
    result = _make_richness_report().show()
    assert result is None


def test_richness_report_render_contains_available():
    assert _make_richness_report().render() == "\n".join(
        [
            "RichnessReport gaps=0",
            "gaps: none",
            "checked_at: 2026-06-09T00:00:00Z",
            "available:",
            "- .render()",
            "- .to_dict()",
        ]
    )
