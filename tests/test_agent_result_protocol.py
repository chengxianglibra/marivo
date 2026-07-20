"""Contract tests for the AgentResult protocol and terminal result types."""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import pytest

import marivo.analysis as ma
import marivo.analysis.frames as analysis_frames
from marivo._authoring.model import AuthoringContract, AuthoringStateRef
from marivo.analysis._capabilities.surface import TYPE_REGISTRY
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.session._store import SessionSummary
from marivo.analysis.session.core import FrameSummaryEntry, JobSummary
from marivo.datasource.errors import repair as datasource_repair
from marivo.datasource.manage import (
    DatasourceDescription,
    DatasourceList,
    DatasourceSummary,
    DatasourceTestResult,
    RawSqlResult,
)
from marivo.preview import PreviewCoverage, PreviewResult
from marivo.refs import Ref
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES, AgentResult, result_repr
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringAssessment,
    PreviewBatchResult,
    VerifyResult,
)
from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport
from marivo.semantic.richness import RichnessReport

datasource_ref = Ref.datasource

REPR_MAX_LEN = 200
RENDER_MAX_LINES = 1000
RENDER_MAX_CHARS = _DEFAULT_MAX_OUTPUT_BYTES


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


def test_authoring_contract_conforms_to_agent_result() -> None:
    contract = AuthoringContract(
        subject_refs=("sales.revenue",),
        states=(AuthoringStateRef(id="semantic.loaded", subject_refs=("sales.revenue",)),),
        transitions=(),
    )

    assert_conforms(contract)
    assert contract.model_dump()["states"][0]["id"] == "semantic.loaded"


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
    assert len(rendered.encode("utf-8")) <= RENDER_MAX_CHARS, (
        f"render() too large ({len(rendered.encode('utf-8'))} bytes)"
    )

    assert obj.show() is None  # type: ignore[attr-defined]


def test_session_results_byte_capped_and_uncapped() -> None:
    job = _job_summary()
    capped = job.render()
    assert len(capped.encode("utf-8")) <= _DEFAULT_MAX_OUTPUT_BYTES
    full = job.render(max_output_bytes=None)
    assert "truncated" not in full
    assert full == capped  # small fixture fits both ways


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
        status="passed",
        coverage=PreviewCoverage(
            scopes=(),
            rows_observed=1,
            scope_exhaustion="exhaustive",
            scope_exactness="scope_exact",
            snapshot_ids=(),
            cache_status="fresh",
        ),
    )


def _preview_batch_result() -> PreviewBatchResult:
    return PreviewBatchResult(results=(_preview_result(),))


def _datasource_description() -> DatasourceDescription:
    return DatasourceDescription(
        name="wh",
        backend_type="trino",
        literal_fields={"host": "trino.example", "catalog": "hive"},
        env_refs={"auth": "TRINO_AUTH"},
    )


def _datasource_summary() -> DatasourceSummary:
    return DatasourceSummary(name="wh", backend_type="duckdb")


def _datasource_list() -> DatasourceList:
    return DatasourceList((DatasourceSummary(name="wh", backend_type="duckdb"),))


def _datasource_test_result() -> DatasourceTestResult:
    return DatasourceTestResult(name="wh", ok=True, latency_ms=12, repair=None)


def _raw_sql_result() -> RawSqlResult:
    return RawSqlResult(
        datasource=datasource_ref("wh"),
        backend_type="duckdb",
        sql="SELECT 1 AS ok",
        reason="check query path",
        columns=("ok",),
        types={"ok": "int64"},
        rows=({"ok": 1},),
        requested_limit=10,
        returned_row_count=1,
        is_truncated=False,
        timeout_seconds=30,
        duration_ms=5,
        warnings=(),
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


def test_frame_summary_entry_positional_row_count_compatibility() -> None:
    entry = FrameSummaryEntry(
        "frame_ab12",
        "metric_frame",
        "sales.revenue",
        "time_series",
        "sales",
        "2026-06-13T00:00:00Z",
        10,
        "sha256:abc",
    )

    assert entry.row_count == 10
    assert entry.content_hash == "sha256:abc"
    assert entry.analysis_purpose is None


def _session_summary() -> SessionSummary:
    return SessionSummary(
        id="sess_1",
        name="q2",
        question=None,
        created_at="2026-06-13T00:00:00Z",
        updated_at="2026-06-13T00:00:00Z",
        job_count=1,
        frame_count=2,
    )


def test_frame_summary_entry_id_aliases_ref() -> None:
    entry = _frame_summary_entry()

    assert entry.id == entry.ref


def _authoring_assessment() -> AuthoringAssessment:
    issue = AssessmentIssue(
        kind="missing_evidence",
        severity="warning",
        refs=("sales.revenue",),
        message="needs evidence",
        rule_id="R1",
    )
    return AuthoringAssessment(status="needs_input", issues=(issue,))


def _verify_result() -> VerifyResult:
    return VerifyResult(
        status="passed",
        ref="sales.orders",
        kind="entity",
        validation_level="static",
        runtime_checked=False,
        issues=(),
        warnings=(),
    )


def _readiness_report() -> ReadinessReport:
    return ReadinessReport(
        status="ready",
        analysis_ready_refs=(Ref.metric("sales.revenue"),),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(
            datasources=("warehouse",),
            refs=("sales.revenue",),
            tables=("sales.orders",),
        ),
        checked_at="2026-06-09T00:00:00Z",
    )


def _richness_report() -> RichnessReport:
    return RichnessReport(gaps=(), checked_at="2026-06-09T00:00:00Z")


TERMINAL_BUILDERS: list = [
    pytest.param(_preview_result, id="PreviewResult"),
    pytest.param(_preview_batch_result, id="PreviewBatchResult"),
    pytest.param(_datasource_description, id="DatasourceDescription"),
    pytest.param(_datasource_list, id="DatasourceList"),
    pytest.param(_datasource_summary, id="DatasourceSummary"),
    pytest.param(_datasource_test_result, id="DatasourceTestResult"),
    pytest.param(_raw_sql_result, id="RawSqlResult"),
    pytest.param(_job_summary, id="JobSummary"),
    pytest.param(_frame_summary_entry, id="FrameSummaryEntry"),
    pytest.param(_session_summary, id="SessionSummary"),
    pytest.param(_authoring_assessment, id="AuthoringAssessment"),
    pytest.param(_verify_result, id="VerifyResult"),
    pytest.param(_readiness_report, id="ReadinessReport"),
    pytest.param(_richness_report, id="RichnessReport"),
]


@pytest.mark.parametrize("builder", TERMINAL_BUILDERS)
def test_terminal_type_conforms(builder: Callable[[], object]) -> None:
    assert_conforms(builder())


@pytest.mark.parametrize("builder", TERMINAL_BUILDERS)
def test_terminal_type_byte_contract(builder: Callable[[], object]) -> None:
    obj = builder()
    capped = obj.render()  # type: ignore[attr-defined]
    assert len(capped.encode("utf-8")) <= _DEFAULT_MAX_OUTPUT_BYTES
    full = obj.render(max_output_bytes=None)  # type: ignore[attr-defined]
    assert "output truncated" not in full
    with pytest.raises(ValueError):
        obj.render(max_output_bytes=1)  # type: ignore[attr-defined]


def test_preview_result_renders_shared_card_shape() -> None:
    result = _preview_result()

    assert result.render() == "\n".join(
        [
            "PreviewResult kind=semantic_dataset ref=sales.orders rows=1/50",
            "status: status=passed truncated=False coverage=exhaustive/scope_exact",
            "columns: id | country",
            "preview:",
            "1 | US",
            "available:",
            "- .render()",
            "- .show()",
        ]
    )

    assert _preview_batch_result().render() == "\n".join(
        [
            "PreviewBatchResult status=passed refs=1",
            "previews (1):",
            "- sales.orders: semantic_dataset, rows=1, warnings=0",
            "available:",
            "- .results",
            "- .refs",
            "- .contract()",
        ]
    )


def test_datasource_management_results_render_shared_card_shape() -> None:
    assert _datasource_summary().render() == "\n".join(
        [
            "DatasourceSummary name=wh backend=duckdb",
            "available:",
            "- .contract()",
            "- .render()",
            "- .show()",
        ]
    )
    assert _datasource_list().render() == "\n".join(
        [
            "DatasourceList count=1",
            "columns: name | backend",
            "preview:",
            "wh | duckdb",
            "available:",
            "- .items",
            "- .ids()",
            "- .render()",
            "- .show()",
        ]
    )
    assert _datasource_description().render() == "\n".join(
        [
            "DatasourceDescription name=wh backend=trino fields=2 env_refs=1",
            "columns: catalog | host | auth_env",
            "available:",
            "- .contract()",
            "- .render()",
            "- .show()",
        ]
    )

    failed = DatasourceTestResult(
        name="wh",
        ok=False,
        latency_ms=None,
        repair=datasource_repair(
            kind="reconnect",
            canonical_id="test",
            action="Reconnect the datasource after fixing its connection settings.",
        ),
    )
    assert failed.render() == "\n".join(
        [
            "DatasourceTestResult name=wh ok=False latency=n/a",
            "status: Reconnect the datasource after fixing its connection settings.",
            "available:",
            "- .contract()",
            "- .render()",
            "- .show()",
        ]
    )


def test_datasource_description_render_includes_all_field_names() -> None:
    literal_fields = {f"field_{index:02d}": index for index in range(10)}
    env_refs = {f"secret_{index:02d}": f"SECRET_{index:02d}" for index in range(3)}
    result = DatasourceDescription(
        name="wh",
        backend_type="trino",
        literal_fields=literal_fields,
        env_refs=env_refs,
    )

    assert "field_09" in result.render()
    assert "secret_02_env" in result.render()


def test_semantic_dto_and_report_results_render_shared_card_shape() -> None:
    assert _authoring_assessment().render() == "\n".join(
        [
            "AuthoringAssessment status=needs_input issues=1",
            "columns: issue | severity",
            "preview:",
            "missing_evidence | warning",
            "available:",
            "- .render()",
            "- .show()",
        ]
    )
    assert _verify_result().render() == "\n".join(
        [
            "VerifyResult status=passed ref=sales.orders kind=entity",
            "status: passed",
            "validation_level: static",
            "runtime_checked: false",
            "Next step:",
            "- continue the batch or run catalog.readiness(refs=...)",
            "available:",
            "- .issues",
            "- .warnings",
        ]
    )
    assert _readiness_report().render() == "\n".join(
        [
            "ReadinessReport status=ready issues=0",
            "analysis_ready: metric:sales.revenue",
            "checked_at: 2026-06-09T00:00:00Z",
            "available:",
            "- .render()",
            "- .to_dict()",
            "- .contract()",
            "- .preview_required_refs",
        ]
    )
    assert _richness_report().render() == "\n".join(
        [
            "RichnessReport gaps=0",
            "gaps: none",
            "checked_at: 2026-06-09T00:00:00Z",
            "available:",
            "- .render()",
            "- .to_dict()",
        ]
    )


def _walk_concrete_analysis_frame_classes() -> list[type[BaseFrame]]:
    pending = list(BaseFrame.__subclasses__())
    seen: set[type[BaseFrame]] = set()
    found: list[type[BaseFrame]] = []
    while pending:
        cls = pending.pop()
        if cls in seen:
            continue
        seen.add(cls)
        pending.extend(cls.__subclasses__())
        if cls.__module__.startswith("marivo.analysis.frames"):
            found.append(cls)
    return sorted(found, key=lambda cls: f"{cls.__module__}.{cls.__name__}")


def test_concrete_analysis_frames_are_public_and_descriptive() -> None:
    assert analysis_frames.__all__
    # Build the set of registered frame type names from the capability kernel.
    analysis_frame_symbols = set(TYPE_REGISTRY.values())
    # ComponentFrame and CoverageFrame are advanced frame types that remain
    # resolvable via explicit help (kept in the type registry) but are pruned
    # from the default __all__ surface.
    advanced_frames = {"ComponentFrame", "CoverageFrame"}
    for cls in _walk_concrete_analysis_frame_classes():
        assert cls._repr_identity is not BaseFrame._repr_identity, cls.__name__
        if cls.__name__ in advanced_frames:
            assert cls.__name__ not in ma.__all__, cls.__name__
            assert cls.__name__ in analysis_frame_symbols, cls.__name__
            continue
        assert cls.__name__ in ma.__all__, cls.__name__
        assert cls.__name__ in analysis_frame_symbols, cls.__name__


def _metric_frame() -> MetricFrame:
    from datetime import UTC, datetime

    from marivo.analysis.lineage import Lineage
    from tests.shared_fixtures import make_test_metric_meta_contract

    meta = MetricFrameMeta(
        **make_test_metric_meta_contract("sales.revenue"),
        kind="metric_frame",
        ref="frame_protocol_test",
        session_id="sess_test",
        project_root="/tmp",
        produced_by_job=None,
        created_at=datetime(2026, 6, 28, tzinfo=UTC),
        row_count=1,
        byte_size=0,
        lineage=Lineage(),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "revenue"},
        window=None,
        where={},
        semantic_kind="time_series",
        semantic_model="sales",
    )
    return MetricFrame(_df=pd.DataFrame({"value": [1.0]}), meta=meta)


def test_metric_frame_contract_warns_for_cumulative_values() -> None:
    frame = _metric_frame()
    frame.meta = frame.meta.model_copy(
        update={
            "cumulative": {
                "kind": "cumulative",
                "base": "sales.gmv",
                "over": "sales.orders.event_time",
                "anchor": "all_history",
                "components": None,
            }
        }
    )

    contract = frame.contract()

    warnings = [
        pre
        for aff in contract.affordances
        for pre in aff.preconditions
        if pre.check == "running_total_caveat"
    ]
    assert warnings
    assert "shared monotonic trend" in (warnings[0].reason or "")
    # Failed preconditions on cumulative all_history must carry a repair.
    assert warnings[0].repair is not None
    assert warnings[0].repair.action.strip()


def test_public_analysis_frames_expose_two_agent_exits() -> None:
    frame = _metric_frame()
    assert hasattr(frame, "show")
    assert hasattr(frame, "contract")
    assert hasattr(frame, "to_pandas")
    assert not hasattr(frame, "summary")
    assert not hasattr(frame, "schema")
    assert not hasattr(frame, "preview")
    assert not hasattr(frame, "next_intents")
