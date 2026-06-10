from __future__ import annotations

from marivo.semantic.ir import SymbolKind
from marivo.semantic.reader import DependencyNode
from marivo.semantic.readiness import ReadinessReport
from marivo.semantic.richness import RichnessReport


def _make_readiness_report() -> ReadinessReport:
    from marivo.semantic.readiness import (
        ParitySummary,
        PreviewSummary,
        ReadinessInputSummary,
        ReadinessReport,
    )
    from marivo.semantic.richness import RichnessSummary

    return ReadinessReport(
        status="ready_with_warnings",
        analysis_ready_refs=("sales.revenue",),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(
            datasources=(), refs=(), tables=(), decision_records=()
        ),
        preview_summary=PreviewSummary(
            required_previews=(), completed_previews=(), failed_previews=(), warnings=()
        ),
        parity_summary=ParitySummary(
            verified_metrics=(),
            unverified_metrics=(),
            drifted_metrics=(),
            unsupported_metrics=(),
            skipped_metrics=(),
        ),
        richness_summary=RichnessSummary(gaps=()),
        checked_at="2026-06-09T00:00:00Z",
    )


def _make_richness_report() -> RichnessReport:
    return RichnessReport(gaps=(), checked_at="2026-06-09T00:00:00Z")


def _make_dependency_node() -> DependencyNode:
    return DependencyNode(
        semantic_id="sales.revenue",
        kind=SymbolKind.METRIC,
        children=(DependencyNode("sales.orders", SymbolKind.ENTITY, ()),),
    )


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
    assert "ReadinessReport" in rendered
    assert "available:" in rendered


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
    assert "available:" in _make_richness_report().render()


# --- DependencyNode ---


def test_dependency_node_repr_is_one_line():
    r = repr(_make_dependency_node())
    assert r.count("\n") == 0
    assert "DependencyNode" in r
    assert "sales.revenue" in r
    assert "call .show() to inspect" in r


def test_dependency_node_render_no_stdout(capsys):
    _make_dependency_node().render()
    assert capsys.readouterr().out == ""


def test_dependency_node_render_does_not_end_with_newline():
    assert not _make_dependency_node().render().endswith("\n")


def test_dependency_node_show_returns_none(capsys):
    result = _make_dependency_node().show()
    assert result is None


def test_dependency_node_render_contains_children():
    rendered = _make_dependency_node().render()
    assert "sales.orders" in rendered


def test_dependency_node_render_contains_available():
    assert "available:" in _make_dependency_node().render()
