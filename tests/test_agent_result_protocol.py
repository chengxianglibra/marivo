"""Contract tests for the AgentResult protocol and terminal result types."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import pytest

import marivo.analysis as ma
from marivo._compat import UTC
from marivo._temporal import TimeScopeContractV1
from marivo.analysis.evidence._dataset_types import ArtifactDigest
from marivo.analysis.session._lazy_read_model import SessionSummary
from marivo.datasource.errors import repair as datasource_repair
from marivo.datasource.manage import (
    DatasourceDescription,
    DatasourceList,
    DatasourceSummary,
    DatasourceTestResult,
    RawSqlResult,
)
from marivo.preview import PreviewCoverage, PreviewResult
from marivo.refs import ref as ref_factory
from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES, AgentResult, result_repr
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringAssessment,
    PreviewBatchResult,
)
from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport
from marivo.semantic.richness import RichnessReport

datasource_ref = ref_factory.datasource

REPR_MAX_LEN = 200
RENDER_MAX_LINES = 1000
RENDER_MAX_CHARS = _DEFAULT_MAX_OUTPUT_BYTES


def test_result_repr_wraps_identity_single_line() -> None:
    out = result_repr("MaterializedMetricDataset artifact=artifact_ab12 rows=7")
    assert (
        out == "<MaterializedMetricDataset artifact=artifact_ab12 rows=7; call .show() to inspect>"
    )
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
    assert len(rendered.encode("utf-8")) <= RENDER_MAX_CHARS, (
        f"render() too large ({len(rendered.encode('utf-8'))} bytes)"
    )

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
        status="passed",
        coverage=PreviewCoverage(
            scopes=(),
            rows_observed=1,
            scope_exhaustion="exhaustive",
            scope_exactness="scope_exact",
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
    return DatasourceTestResult(name="wh", ok=True, latency_ms=12, failure=None, repair=None)


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


def test_session_summary_rejects_positional_construction() -> None:
    # Keyword construction must succeed so the TypeError below is pinned to
    # "positional args rejected", not "the field values are invalid".
    _session_summary()
    with pytest.raises(TypeError):
        SessionSummary("sess_1", "q2", None, "2026-06-13T00:00:00Z", "2026-06-13T00:00:00Z", 1, 2)  # type: ignore[misc, call-arg]


def _session_summary() -> SessionSummary:
    return SessionSummary(
        id="sess_1",
        name="q2",
        question=None,
        created_at=datetime(2026, 6, 13, tzinfo=UTC),
        updated_at=datetime(2026, 6, 13, tzinfo=UTC),
        run_count=1,
        artifact_count=2,
    )


def _authoring_assessment() -> AuthoringAssessment:
    issue = AssessmentIssue(
        kind="missing_evidence",
        severity="warning",
        refs=("sales.revenue",),
        message="needs evidence",
        rule_id="R1",
    )
    return AuthoringAssessment(status="needs_input", issues=(issue,))


def _readiness_report() -> ReadinessReport:
    return ReadinessReport(
        status="ready",
        analysis_ready_inputs=(ref_factory.metric("sales.revenue"),),
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
    pytest.param(_session_summary, id="SessionSummary"),
    pytest.param(_authoring_assessment, id="AuthoringAssessment"),
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


def test_committed_runtime_projections_satisfy_terminal_protocol(tmp_path) -> None:
    from marivo.analysis.materialization.admission import DatasetRuntime
    from marivo.analysis.materialization.store import SessionStore
    from tests.lazy_runtime_read_fixtures import failure, input_value, publish

    store = SessionStore(tmp_path)
    store.create_session("protocol", session_ref="session")
    publish(store, "succeeded", "artifact")
    store.admit("session", "failed-key", input_value(), run_ref="failed")
    store.fail("failed", failure())
    store.admit("session", "pending-key", input_value(), run_ref="pending")
    runtime = DatasetRuntime(store, "session")
    page = runtime.runs()
    candidates = (*page.items, page, runtime.graph(), _artifact_digest())
    for candidate in candidates:
        assert_conforms(candidate)
        assert candidate.render() == candidate.render()


def test_preview_result_renders_shared_card_shape() -> None:
    result = _preview_result()

    assert result.render() == "\n".join(
        [
            "PreviewResult kind=semantic_dataset ref=sales.orders rows=1/50",
            (
                "status: status=passed truncated=False "
                "scope_coverage=exhaustive/scope_exact "
                "sample_policy=bounded_limit(limit=20)"
            ),
            "columns: id | country",
            "preview:",
            "1 | US",
            "available:",
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
            "- .show()",
        ]
    )


def test_datasource_management_results_render_shared_card_shape() -> None:
    assert _datasource_summary().render() == "\n".join(
        [
            "DatasourceSummary name=wh backend=duckdb",
            "available:",
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
            "- .show()",
        ]
    )
    assert _datasource_description().render() == "\n".join(
        [
            "DatasourceDescription name=wh backend=trino fields=2 env_refs=1",
            "columns: catalog | host | auth_env",
            "available:",
            "- .show()",
        ]
    )

    from marivo.datasource.manage import DatasourceFailure

    failed = DatasourceTestResult(
        name="wh",
        ok=False,
        latency_ms=None,
        failure=DatasourceFailure(
            code="connection_roundtrip_failed",
            exception_type="ProgrammingError",
            backend_code="115",
            backend_name="UNKNOWN_SETTING",
            message="Unknown setting access_mode",
        ),
        repair=datasource_repair(
            kind="reconnect",
            canonical_id="test",
            action="Reconnect the datasource after fixing its connection settings.",
        ),
    )
    assert failed.render() == "\n".join(
        [
            "DatasourceTestResult name=wh ok=False latency=n/a",
            "status: connection_roundtrip_failed",
            "failure: ProgrammingError code=115 name=UNKNOWN_SETTING",
            "message: Unknown setting access_mode",
            "repair: Reconnect the datasource after fixing its connection settings.",
            'repair help: marivo.help("datasource.test")',
            "available:",
            "- .failure",
            "- .repair",
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
            "- .show()",
        ]
    )
    assert _readiness_report().render() == "\n".join(
        [
            "ReadinessReport scope=semantic_static status=ready issues=0",
            "scope: semantic_static",
            "analysis_ready: metric:sales.revenue",
            "checked_at: 2026-06-09T00:00:00Z",
            "available:",
            "- .show()",
            "- .to_dict()",
            "- .analysis_ready_inputs",
        ]
    )
    assert _richness_report().render() == "\n".join(
        [
            "RichnessReport gaps=0",
            "gaps: none",
            "checked_at: 2026-06-09T00:00:00Z",
            "available:",
            "- .show()",
            "- .to_dict()",
        ]
    )


def _time_scope_contract() -> TimeScopeContractV1:
    return TimeScopeContractV1(
        kind="absolute",
        start=date(2026, 7, 1),
        end=date(2026, 8, 1),
    )


CONTRACT_BUILDERS: list = [
    pytest.param(_time_scope_contract, id="TimeScopeContractV1"),
]


@pytest.mark.parametrize("builder", CONTRACT_BUILDERS)
def test_contract_result_protocol_is_structural_and_side_effect_free(
    builder: Callable[[], object],
    capsys: pytest.CaptureFixture[str],
) -> None:
    contract = builder()

    rendered = contract.render()  # type: ignore[attr-defined]
    assert repr(contract)
    assert capsys.readouterr().out == ""
    assert rendered == contract.render()  # type: ignore[attr-defined]
    assert len(rendered.encode("utf-8")) <= _DEFAULT_MAX_OUTPUT_BYTES
    assert contract.model_dump()  # type: ignore[attr-defined]

    lines = rendered.splitlines()
    available_index = lines.index("available:")
    for line in lines[available_index + 1 :]:
        assert line.startswith("- .")
        member = line.removeprefix("- .").split("(", 1)[0]
        assert hasattr(contract, member), (type(contract).__name__, member)

    assert contract.show() is None  # type: ignore[attr-defined]
    assert capsys.readouterr().out == rendered + "\n"


def test_time_scope_render_honors_output_budget(capsys: pytest.CaptureFixture[str]) -> None:
    scope = ma.time_scope(start="2026-07-01", end="2026-08-01")

    assert scope.render() == "TimeScope(kind=absolute, start=2026-07-01, end=2026-08-01)"
    assert scope.render(max_output_bytes=None) == scope.render()
    scope.show()
    assert capsys.readouterr().out == scope.render() + "\n"

    with pytest.raises(ValueError, match="max_output_bytes is too small"):
        scope.render(max_output_bytes=1)
    with pytest.raises(ValueError, match="max_output_bytes is too small"):
        scope.show(max_output_bytes=1)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("builder", CONTRACT_BUILDERS)
def test_contract_str_renders_same_card(builder: Callable[[], object]) -> None:
    contract = builder()
    assert str(contract) == contract.render()  # type: ignore[attr-defined]


def _artifact_digest() -> ArtifactDigest:
    return ArtifactDigest(
        artifact_ref=ma.ArtifactRef(ref="artifact_abc"),
        quality_summary_digest="a" * 64,
        typed_issue_digest="b" * 64,
        evidence_digest="c" * 64,
        finding_count=0,
        finding_set_digest="d" * 64,
        extractor_contract_versions=("zero_findings@v1",),
    )


def test_artifact_digest_repr_uses_shared_result_repr() -> None:
    digest = _artifact_digest()
    assert repr(digest) == result_repr("ArtifactDigest ref=artifact_abc")
    assert_conforms(digest)


def _footer_entries(obj: object) -> tuple[str, ...]:
    lines = obj.render().splitlines()  # type: ignore[attr-defined]
    index = lines.index("available:")
    return tuple(line.removeprefix("- ") for line in lines[index + 1 :] if line.startswith("- "))


@pytest.mark.parametrize("builder", [*TERMINAL_BUILDERS, *CONTRACT_BUILDERS])
def test_available_footer_follows_two_exit_rule(builder: Callable[[], object]) -> None:
    obj = builder()
    entries = _footer_entries(obj)
    assert ".show()" in entries, (type(obj).__name__, entries)
    assert not any(entry.startswith(".render") for entry in entries), (
        type(obj).__name__,
        entries,
    )
    if callable(getattr(obj, "contract", None)):
        assert ".contract()" in entries, (type(obj).__name__, entries)
