"""Contract tests for the AgentResult protocol and terminal result types."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from marivo.analysis.frames.base import FrameSummary
from marivo.analysis.session._store import SessionSummary
from marivo.analysis.session.core import FrameSummaryEntry, JobSummary
from marivo.datasource.ir import TableSourceIR
from marivo.datasource.manage import DatasourceSummary, DatasourceTestResult
from marivo.datasource.scan import ColumnInspection, ScanReport
from marivo.preview import PreviewResult
from marivo.render import AgentResult, result_repr
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringAssessment,
    DerivedMetricBrief,
    DomainBrief,
)

REPR_MAX_LEN = 200
RENDER_MAX_LINES = 40
RENDER_MAX_CHARS = 2000


def test_result_repr_wraps_identity_single_line() -> None:
    out = result_repr("MetricFrame ref=frame_ab12 rows=7")
    assert out == "<MetricFrame ref=frame_ab12 rows=7; call .show() to inspect>"
    assert "\n" not in out


def test_agent_result_is_runtime_checkable() -> None:
    class _Conforming:
        def render(self) -> str:
            return "x"

        def show(self) -> None:
            print(self.render())

        def __repr__(self) -> str:
            return result_repr("X id=1")

    assert isinstance(_Conforming(), AgentResult)


def assert_conforms(obj: object) -> None:
    assert isinstance(obj, AgentResult)

    r = repr(obj)
    assert "\n" not in r, f"repr must be single-line: {r!r}"
    assert len(r) <= REPR_MAX_LEN, f"repr too long ({len(r)}): {r!r}"
    assert type(obj).__name__ in r, f"repr must name the type: {r!r}"

    rendered = obj.render()  # type: ignore[attr-defined]
    assert isinstance(rendered, str)
    assert not rendered.endswith("\n"), "render() must not end with newline"
    assert len(rendered.splitlines()) <= RENDER_MAX_LINES
    assert len(rendered) <= RENDER_MAX_CHARS

    assert obj.show() is None  # type: ignore[attr-defined]


def _preview_result() -> PreviewResult:
    return PreviewResult(
        kind="semantic_dataset",
        ref="sales.orders",
        columns=("id", "country"),
        types={"id": "int64", "country": "string"},
        rows=({"id": 1, "country": "US"},),
        requested_limit=50,
        returned_row_count=1,
        is_truncated=False,
    )


def _datasource_summary() -> DatasourceSummary:
    return DatasourceSummary(name="wh", backend_type="duckdb", description=None)


def _datasource_test_result() -> DatasourceTestResult:
    return DatasourceTestResult(name="wh", ok=True, error=None, latency_ms=12)


def _scan_report() -> ScanReport:
    return ScanReport(
        partition_used=None,
        partition_resolution="none",
        rows_scanned=10,
        columns_scanned=("id", "country"),
        truncated=False,
        elapsed_seconds=0.1,
        warnings=(),
    )


def _column_inspection() -> ColumnInspection:
    return ColumnInspection(
        datasource="wh",
        source=TableSourceIR(table="orders"),
        profiles=(),
        scan=_scan_report(),
    )


def _job_summary() -> JobSummary:
    return JobSummary(
        id="job_1",
        intent="observe",
        status="succeeded",
        started_at="2026-06-13T00:00:00Z",
        duration_ms=12,
        output_frame_ref="frame_ab12",
    )


def _frame_summary_entry() -> FrameSummaryEntry:
    return FrameSummaryEntry(
        ref="frame_ab12",
        kind="metric_frame",
        metric_id="sales.revenue",
        semantic_kind="time_series",
        semantic_model="sales",
        created_at="2026-06-13T00:00:00Z",
    )


def _session_summary() -> SessionSummary:
    return SessionSummary(
        id="sess_1",
        name="q2",
        question=None,
        created_at="2026-06-13T00:00:00Z",
        updated_at="2026-06-13T00:00:00Z",
        job_count=1,
        frame_count=2,
        report_count=0,
    )


def _frame_summary() -> FrameSummary:
    return FrameSummary(
        kind="metric_frame",
        ref="frame_ab12",
        row_count=7,
        columns=["period", "value"],
        null_ratios={"period": 0.0, "value": 0.0},
        produced_by_job="job_1",
        lineage_oneliner="observe",
        semantic_shape="time_series",
    )


def _authoring_assessment() -> AuthoringAssessment:
    issue = AssessmentIssue(
        kind="missing_evidence",
        severity="warning",
        refs=("sales.revenue",),
        message="needs evidence",
        rule_id="R1",
    )
    return AuthoringAssessment(status="needs_input", issues=(issue,), questions=())


def _domain_brief() -> DomainBrief:
    return DomainBrief(
        status="sufficient",
        proposed_name="sales",
        existing_domains=(),
        matches=(),
        questions=(),
        issues=(),
    )


def _derived_metric_brief() -> DerivedMetricBrief:
    return DerivedMetricBrief(
        status="needs_input",
        composition_kind="ratio",
        components=(),
        propagated_verification="python_native",
        unit_hint=None,
        matches=(),
        questions=(),
        issues=(),
    )


TERMINAL_BUILDERS: list = [
    pytest.param(_preview_result, id="PreviewResult"),
    pytest.param(_datasource_summary, id="DatasourceSummary"),
    pytest.param(_datasource_test_result, id="DatasourceTestResult"),
    pytest.param(_scan_report, id="ScanReport"),
    pytest.param(_column_inspection, id="ColumnInspection"),
    pytest.param(_job_summary, id="JobSummary"),
    pytest.param(_frame_summary_entry, id="FrameSummaryEntry"),
    pytest.param(_session_summary, id="SessionSummary"),
    pytest.param(_frame_summary, id="FrameSummary"),
    pytest.param(_authoring_assessment, id="AuthoringAssessment"),
    pytest.param(_domain_brief, id="DomainBrief"),
    pytest.param(_derived_metric_brief, id="DerivedMetricBrief"),
]


@pytest.mark.parametrize("builder", TERMINAL_BUILDERS)
def test_terminal_type_conforms(builder: Callable[[], object]) -> None:
    assert_conforms(builder())
