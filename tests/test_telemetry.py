"""Tests for local loop-engineering telemetry v2."""

from __future__ import annotations

import inspect
import json
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import marivo.analysis as mv


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _log_record(entry: dict[str, object]) -> dict[str, object]:
    resource_logs = entry["resourceLogs"]
    assert isinstance(resource_logs, list)
    scope_logs = resource_logs[0]["scopeLogs"]
    assert isinstance(scope_logs, list)
    records = scope_logs[0]["logRecords"]
    assert isinstance(records, list)
    return records[0]


def _attrs(record: dict[str, object]) -> dict[str, object]:
    raw_attrs = record["attributes"]
    assert isinstance(raw_attrs, list)
    decoded: dict[str, object] = {}
    for item in raw_attrs:
        assert isinstance(item, dict)
        key = item["key"]
        value = item["value"]
        assert isinstance(key, str)
        assert isinstance(value, dict)
        if "arrayValue" in value:
            array = value["arrayValue"]
            assert isinstance(array, dict)
            values = array["values"]
            assert isinstance(values, list)
            decoded[key] = tuple(next(iter(element.values())) for element in values)
        else:
            decoded[key] = next(iter(value.values()))
    return decoded


def _capability_records(path: Path, capability_id: str) -> list[dict[str, object]]:
    return [
        record
        for entry in _records(path)
        if (_attrs(record := _log_record(entry))).get("marivo.capability.id") == capability_id
    ]


@pytest.fixture
def telemetry_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n', encoding="utf-8")
    monkeypatch.delenv("MARIVO_TELEMETRY", raising=False)
    yield tmp_path


def test_track_operation_writes_correlated_v2_pair(telemetry_project: Path) -> None:
    from marivo.telemetry import track_operation

    session = SimpleNamespace(
        id="sess_test",
        question="Why did revenue fall?",
        project_root=telemetry_project,
    )
    with track_operation(
        "marivo.analysis.compare",
        family="operator",
        intent="compare",
        session=session,
    ):
        pass

    records = [
        _log_record(entry)
        for entry in _records(telemetry_project / ".marivo" / "telemetry" / "events.jsonl")
    ]
    assert [record["body"] for record in records] == [
        {"stringValue": "marivo.operation.started"},
        {"stringValue": "marivo.operation.completed"},
    ]
    started, completed = map(_attrs, records)
    assert started["marivo.event.schema_version"] == "2"
    assert started["marivo.operation.id"] == completed["marivo.operation.id"]
    assert started["marivo.operation.status"] == "started"
    assert completed["marivo.operation.status"] == "ok"
    assert started["marivo.session.id"] == "sess_test"
    assert started["marivo.session.question"] == "Why did revenue fall?"
    assert started["marivo.project.instance_id"] == completed["marivo.project.instance_id"]


def test_track_operation_records_structured_error_without_message(
    telemetry_project: Path,
) -> None:
    from marivo.telemetry import track_operation

    with (
        pytest.raises(ValueError, match="sensitive failure text"),
        track_operation(
            "marivo.analysis.compare",
            family="operator",
            intent="compare",
            project_root=telemetry_project,
        ),
    ):
        raise ValueError("sensitive failure text")

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    attrs = _attrs(_log_record(_records(path)[-1]))
    assert attrs["marivo.operation.status"] == "error"
    assert attrs["marivo.error.class"] == "ValueError"
    assert attrs["marivo.error.domain"] == "runtime"
    assert "sensitive failure text" not in path.read_text(encoding="utf-8")


def test_nested_operations_link_parent_and_suppress_same_capability_delegate(
    telemetry_project: Path,
) -> None:
    from marivo.telemetry import tracked_capability

    @tracked_capability(surface="analysis", capability_id="child", capability_kind="read")
    def child(*, project_root: Path) -> str:
        return "ok"

    @tracked_capability(surface="analysis", capability_id="parent", capability_kind="operator")
    def parent(*, project_root: Path) -> str:
        return child(project_root=project_root)

    @tracked_capability(surface="analysis", capability_id="same", capability_kind="read")
    def delegated(*, project_root: Path) -> None:
        return None

    same_outer = tracked_capability(
        surface="analysis", capability_id="same", capability_kind="read"
    )(delegated)

    assert parent(project_root=telemetry_project) == "ok"
    same_outer(project_root=telemetry_project)

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    parent_attrs = [_attrs(record) for record in _capability_records(path, "parent")]
    child_attrs = [_attrs(record) for record in _capability_records(path, "child")]
    assert len(parent_attrs) == len(child_attrs) == 2
    assert all(
        attrs["marivo.operation.parent_id"] == parent_attrs[0]["marivo.operation.id"]
        for attrs in child_attrs
    )
    assert parent_attrs[0]["marivo.operation.origin"] == "explicit"
    assert child_attrs[0]["marivo.operation.origin"] == "delegated"
    assert len(_capability_records(path, "same")) == 2


def test_restored_session_suppresses_successful_internal_load_declarations(
    telemetry_project: Path,
) -> None:
    from marivo.telemetry import tracked_capability

    store_path = telemetry_project / ".marivo" / "analysis" / "session_store.db"
    store_path.parent.mkdir(parents=True)
    with sqlite3.connect(store_path) as connection:
        connection.execute("CREATE TABLE sessions (name TEXT, question TEXT)")
        connection.execute(
            "INSERT INTO sessions(name, question) VALUES (?, ?)",
            ("demo", "persisted question"),
        )

    @tracked_capability(
        surface="semantic",
        capability_id="measure_column",
        capability_kind="declaration",
    )
    def declaration(*, project_root: Path) -> str:
        return "declared"

    @tracked_capability(
        surface="datasource",
        capability_id="trino",
        capability_kind="callable",
    )
    def datasource_declaration(*, project_root: Path) -> str:
        return "datasource declared"

    def load_project(*, project_root: Path) -> str:
        declaration(project_root=project_root)
        datasource_declaration(project_root=project_root)
        return "declared"

    @tracked_capability(
        surface="semantic",
        capability_id="load",
        capability_kind="callable",
        default_stage="resolve",
    )
    def load(*, project_root: Path) -> str:
        return load_project(project_root=project_root)

    @tracked_capability(
        surface="analysis",
        capability_id="session.get_or_create",
        capability_kind="lifecycle",
    )
    def resume(*, name: str, question: str, project_root: Path) -> str:
        return load_project(project_root=project_root)

    assert (
        resume(
            name="demo",
            question="ignored replacement",
            project_root=telemetry_project,
        )
        == "declared"
    )

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    assert len(_capability_records(path, "session.get_or_create")) == 2
    assert _capability_records(path, "load") == []
    assert _capability_records(path, "measure_column") == []
    assert _capability_records(path, "trino") == []

    assert (
        resume(
            name="fresh",
            question="new question",
            project_root=telemetry_project,
        )
        == "declared"
    )
    fresh_declarations = [_attrs(record) for record in _capability_records(path, "measure_column")]
    fresh_datasources = [_attrs(record) for record in _capability_records(path, "trino")]
    assert len(fresh_declarations) == len(fresh_datasources) == 2
    assert fresh_declarations[0]["marivo.operation.origin"] == "internal_load"
    assert fresh_datasources[0]["marivo.operation.origin"] == "internal_load"

    assert load(project_root=telemetry_project) == "declared"
    load_attrs = [_attrs(record) for record in _capability_records(path, "load")]
    declaration_attrs = [_attrs(record) for record in _capability_records(path, "measure_column")]
    assert len(load_attrs) == 2
    assert load_attrs[0]["marivo.operation.origin"] == "explicit"
    assert declaration_attrs[-2]["marivo.operation.origin"] == "internal_load"


def test_sensitive_inputs_are_excluded_but_goal_and_identifiers_are_kept(
    telemetry_project: Path,
) -> None:
    from marivo.telemetry import tracked_capability

    @tracked_capability(surface="datasource", capability_id="raw_sql", capability_kind="boundary")
    def diagnostic(
        datasource: object,
        sql: str,
        *,
        reason: str,
        host: str,
        password: str,
        slice_by: dict[str, str],
        project_root: Path,
    ) -> None:
        return None

    diagnostic(
        SimpleNamespace(id="datasource.warehouse"),
        "SELECT secret_value FROM private_table",
        reason="check connectivity",
        host="private.example",
        password="dont-log-me",
        slice_by={"customer": "Alice"},
        project_root=telemetry_project,
    )

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    text = path.read_text(encoding="utf-8")
    attrs = _attrs(_capability_records(path, "raw_sql")[0])
    assert attrs["marivo.datasource.raw_sql.reason"] == "check connectivity"
    assert attrs["marivo.input.datasource.id"] == "datasource.warehouse"
    for forbidden in (
        "SELECT secret_value",
        "private_table",
        "private.example",
        "dont-log-me",
        "Alice",
    ):
        assert forbidden not in text


def test_free_text_collections_are_shape_only_and_closed_options_are_kept(
    telemetry_project: Path,
) -> None:
    from marivo.telemetry import tracked_capability

    @tracked_capability(
        surface="semantic",
        capability_id="ai_context",
        capability_kind="declaration",
    )
    def author(
        *,
        guardrails: tuple[str, ...],
        client_tags: tuple[str, ...],
        dimensions: tuple[str, ...],
        additivity: str,
        granularity: str,
        project_root: Path,
    ) -> None:
        return None

    author(
        guardrails=("Never expose the private customer cohort.",),
        client_tags=("tenant=secret-account",),
        dimensions=("orders.created_at",),
        additivity="semi_additive",
        granularity="daily",
        project_root=telemetry_project,
    )

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    text = path.read_text(encoding="utf-8")
    attrs = _attrs(_capability_records(path, "ai_context")[0])
    assert attrs["marivo.input.guardrails.count"] == "1"
    assert attrs["marivo.input.guardrails.item_types"] == ("str",)
    assert "marivo.input.guardrails.ids" not in attrs
    assert attrs["marivo.input.client_tags.count"] == "1"
    assert "marivo.input.client_tags.ids" not in attrs
    assert attrs["marivo.input.dimensions.ids"] == ("orders.created_at",)
    assert attrs["marivo.input.additivity"] == "semi_additive"
    assert attrs["marivo.input.granularity"] == "daily"
    assert "private customer cohort" not in text
    assert "secret-account" not in text


def test_session_question_create_and_resume_semantics(telemetry_project: Path) -> None:
    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"

    first = mv.session.get_or_create(
        name="demo",
        question="Original question",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )
    resumed = mv.session.get_or_create(
        name="demo",
        question="Ignored replacement",
        backend_factory=lambda _name: None,
        use_datasources=False,
    )

    assert resumed.id == first.id
    calls = [_attrs(record) for record in _capability_records(path, "session.get_or_create")]
    assert len(calls) == 4
    assert calls[0]["marivo.session.created"] is True
    assert calls[0]["marivo.session.question_applied"] is True
    assert calls[1]["marivo.session.question"] == "Original question"
    assert calls[2]["marivo.session.created"] is False
    assert calls[2]["marivo.session.question_applied"] is False
    assert calls[2]["marivo.session.requested_question"] == "Ignored replacement"
    assert calls[2]["marivo.session.question"] == "Original question"
    assert calls[3]["marivo.session.question"] == "Original question"


def test_analysis_purpose_and_repair_survive_pre_persistence_failure(
    telemetry_project: Path,
) -> None:
    session = mv.session.get_or_create(
        name="demo", backend_factory=lambda _name: None, use_datasources=False
    )

    with pytest.raises(mv.errors.AnalysisError):
        session.assess_quality(object(), analysis_purpose="check whether evidence is usable")

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    completed = _attrs(_capability_records(path, "assess_quality")[-1])
    assert completed["marivo.operation.status"] == "error"
    assert completed["marivo.analysis.purpose"] == "check whether evidence is usable"
    assert completed["marivo.error.domain"] == "analysis"
    assert completed["marivo.error.class"] == "AnalysisError"
    assert completed["marivo.repair.kind"] == "retry"
    assert "marivo.phase.validate.duration_ms" in completed


def test_datasource_raw_sql_failure_keeps_reason_and_structured_repair(
    telemetry_project: Path,
) -> None:
    import marivo.datasource as md
    from marivo.datasource.errors import DatasourceMissingError

    with pytest.raises(DatasourceMissingError):
        md.raw_sql(
            md.ref("datasource.missing"),
            "SELECT private_value FROM secret_table",
            reason="verify the terminal fallback",
            project_root=telemetry_project,
        )

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    completed = _attrs(_capability_records(path, "raw_sql")[-1])
    assert completed["marivo.datasource.raw_sql.reason"] == "verify the terminal fallback"
    assert completed["marivo.error.domain"] == "datasource"
    assert completed["marivo.repair.kind"] == "register"
    assert "SELECT private_value" not in path.read_text(encoding="utf-8")
    assert "secret_table" not in path.read_text(encoding="utf-8")


def test_semantic_help_failure_is_tracked_as_semantic_operation(
    telemetry_project: Path,
) -> None:
    import marivo.semantic as ms

    with pytest.raises(ms.errors.SemanticHelpTargetError):
        ms.help_text("not-a-semantic-capability")

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    completed = _attrs(_capability_records(path, "help_text")[-1])
    assert completed["marivo.surface"] == "semantic"
    assert completed["marivo.operation.status"] == "error"
    assert completed["marivo.error.domain"] == "semantic"
    assert completed["marivo.repair.help_surface"] == "semantic"


def test_result_summary_links_artifact_and_consumption(telemetry_project: Path) -> None:
    from marivo.telemetry import tracked_capability

    meta = SimpleNamespace(
        ref="frame_123",
        artifact_id="artifact_123",
        produced_by_job="job_123",
        row_count=7,
        content_hash="abc123",
        kind="metric_frame",
        semantic_kind="time_series",
        evidence_status="complete",
        session_id="sess_123",
        project_root=str(telemetry_project),
    )
    frame = SimpleNamespace(meta=meta, ref="frame_123")

    @tracked_capability(surface="analysis", capability_id="produce", capability_kind="operator")
    def produce(*, project_root: Path) -> object:
        return frame

    @tracked_capability(surface="analysis", capability_id="consume", capability_kind="read")
    def consume(source: object, *, project_root: Path) -> None:
        return None

    produced = produce(project_root=telemetry_project)
    consume(produced, project_root=telemetry_project)

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    result = _attrs(_capability_records(path, "produce")[-1])
    consumption = _attrs(_capability_records(path, "consume")[0])
    assert result["marivo.result.ref"] == "frame_123"
    assert result["marivo.result.artifact_id"] == "artifact_123"
    assert result["marivo.result.produced_by_job"] == "job_123"
    assert result["marivo.result.row_count"] == "7"
    assert consumption["marivo.input.source.ref"] == "frame_123"
    assert consumption["marivo.input.source.artifact_id"] == "artifact_123"


def test_method_consumption_records_receiver_artifact_identity(
    telemetry_project: Path,
) -> None:
    from marivo.telemetry import tracked_capability

    meta = SimpleNamespace(
        ref="frame_123",
        artifact_id="artifact_123",
        produced_by_job="job_123",
        content_hash="sha256:abc123",
        kind="metric_frame",
        semantic_kind="time_series",
        session_id="sess_123",
        project_root=str(telemetry_project),
    )

    class Frame:
        def __init__(self) -> None:
            self.meta = meta

        @tracked_capability(
            surface="analysis",
            capability_id="boundary.to_pandas",
            capability_kind="boundary",
        )
        def to_pandas(self) -> str:
            return "materialized"

    assert Frame().to_pandas() == "materialized"

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    started = _attrs(_capability_records(path, "boundary.to_pandas")[0])
    assert started["marivo.input.artifact_id"] == "artifact_123"
    assert started["marivo.input.ref"] == "frame_123"
    assert started["marivo.input.produced_by_job"] == "job_123"
    assert started["marivo.input.content_hash"] == "sha256:abc123"


def test_base_frame_show_does_not_instrument_shared_semantic_show(
    telemetry_project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
    from marivo.semantic.readiness import ReadinessInputSummary, ReadinessReport

    report = ReadinessReport(
        status="ready",
        analysis_ready_refs=(),
        blockers=(),
        warnings=(),
        input_summary=ReadinessInputSummary(datasources=(), refs=(), tables=()),
        checked_at="2026-07-17T00:00:00+00:00",
    )
    report.show()

    frame = BaseFrame(
        _df=pd.DataFrame({"value": [1.0]}),
        meta=BaseFrameMeta(
            kind="metric_frame",
            ref="artifact_123",
            session_id="sess_123",
            project_root=str(telemetry_project),
            produced_by_job="job_123",
            created_at=datetime.now(UTC),
            row_count=1,
            byte_size=1,
            artifact_id="artifact_123",
            content_hash="sha256:abc123",
        ),
    )
    frame.show()
    capsys.readouterr()

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    show_records = [_attrs(record) for record in _capability_records(path, "BaseFrame.show")]
    assert len(show_records) == 2
    assert show_records[0]["marivo.input.artifact_id"] == "artifact_123"
    assert show_records[0]["marivo.input.produced_by_job"] == "job_123"


def test_failed_default_stage_is_recorded_without_exception_message(
    telemetry_project: Path,
) -> None:
    from marivo.telemetry import tracked_capability

    @tracked_capability(
        surface="semantic",
        capability_id="load",
        capability_kind="callable",
        default_stage="resolve",
    )
    def load(*, project_root: Path) -> None:
        raise TypeError("private invalid declaration")

    with pytest.raises(TypeError, match="private invalid declaration"):
        load(project_root=telemetry_project)

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    completed = _attrs(_capability_records(path, "load")[-1])
    assert completed["marivo.error.stage"] == "resolve"
    assert "marivo.phase.resolve.duration_ms" in completed
    assert "private invalid declaration" not in path.read_text(encoding="utf-8")


def test_cli_init_system_exit_writes_deferred_correlated_pair(telemetry_project: Path) -> None:
    from marivo.cli import init_project

    (telemetry_project / "marivo.toml").write_text("this is not valid [[toml", encoding="utf-8")
    with pytest.raises(SystemExit) as exc_info:
        init_project(project_dir=telemetry_project)

    assert exc_info.value.code == 1
    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    calls = [_attrs(record) for record in _capability_records(path, "init")]
    assert len(calls) == 2
    assert calls[0]["marivo.operation.status"] == "started"
    assert calls[1]["marivo.operation.status"] == "error"
    assert calls[1]["marivo.error.class"] == "SystemExit"
    assert calls[0]["marivo.operation.id"] == calls[1]["marivo.operation.id"]


def test_cli_help_and_doctor_commands_write_operation_pairs(
    telemetry_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from marivo.cli import main
    from marivo.doctor import DoctorReport

    report = DoctorReport(
        status="ok",
        project_root=str(telemetry_project),
        python_executable="/tmp/python",
        marivo_version="test",
        marivo_package_path="/tmp/marivo",
        sections=(),
    )
    monkeypatch.setattr("marivo.doctor.run_doctor", lambda _options: report)

    main(["help", "analysis", "observe"])
    main(["doctor", "--project-root", str(telemetry_project), "--format", "json"])
    capsys.readouterr()

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    cli_help = [
        record
        for record in _capability_records(path, "help")
        if _attrs(record)["marivo.surface"] == "cli"
    ]
    assert len(cli_help) == 2
    assert len(_capability_records(path, "doctor")) == 2


def test_concurrent_appends_remain_valid_jsonl(telemetry_project: Path) -> None:
    from marivo.telemetry import track_operation

    def emit(index: int) -> None:
        with track_operation(
            f"marivo.analysis.concurrent_{index}",
            family="read",
            intent=f"concurrent_{index}",
            project_root=telemetry_project,
        ):
            pass

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(emit, range(40)))

    rows = _records(telemetry_project / ".marivo" / "telemetry" / "events.jsonl")
    assert len(rows) == 80


def test_writer_failure_is_isolated_and_reported_on_next_success(
    telemetry_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.telemetry as telemetry

    real_open = telemetry.os.open

    def fail_open(*args: object, **kwargs: object) -> int:
        del args, kwargs
        raise OSError("telemetry disk unavailable")

    monkeypatch.setattr(telemetry.os, "open", fail_open)
    telemetry.track_event(
        "marivo.analysis.failed_write",
        family="read",
        intent="failed_write",
        project_root=telemetry_project,
    )
    monkeypatch.setattr(telemetry.os, "open", real_open)
    telemetry.track_event(
        "marivo.analysis.recovered_write",
        family="read",
        intent="recovered_write",
        project_root=telemetry_project,
    )

    path = telemetry_project / ".marivo" / "telemetry" / "events.jsonl"
    attrs = _attrs(_log_record(_records(path)[0]))
    assert attrs["marivo.telemetry.dropped_since_last_write"] == "1"


@pytest.mark.parametrize(
    ("setting", "environment", "enabled"),
    [
        ('[project]\nname="test"\n\n[telemetry]\nenabled="off"\n', None, False),
        ('[project]\nname="test"\n\n[telemetry]\nenabled="off"\n', "on", True),
        ('[project]\nname="test"\n', "off", False),
        ('[project]\nname="test"\n', "0", True),
    ],
)
def test_telemetry_enablement_precedence(
    telemetry_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
    environment: str | None,
    enabled: bool,
) -> None:
    from marivo.telemetry import track_operation

    (telemetry_project / "marivo.toml").write_text(setting, encoding="utf-8")
    if environment is None:
        monkeypatch.delenv("MARIVO_TELEMETRY", raising=False)
    else:
        monkeypatch.setenv("MARIVO_TELEMETRY", environment)
    with track_operation(
        "marivo.analysis.demo",
        family="read",
        intent="demo",
        project_root=telemetry_project,
    ):
        pass
    assert (telemetry_project / ".marivo" / "telemetry" / "events.jsonl").exists() is enabled


def _expected_instrumented(descriptors: tuple[object, ...]) -> set[str]:
    from marivo.introspection.live.reflect import import_registered_callable

    expected: set[str] = set()
    for descriptor in descriptors:
        path = getattr(descriptor, "callable_path", None)
        if not isinstance(path, str):
            continue
        target = import_registered_callable(path)
        if isinstance(target, (property, type)):
            continue
        capability_id = descriptor.id if hasattr(descriptor, "id") else descriptor.canonical_id
        expected.add(capability_id)
    return expected


def test_registry_callables_are_telemetry_covered() -> None:
    import marivo.datasource as md
    import marivo.semantic as ms
    from marivo.analysis._capabilities.registry import REGISTRY as ANALYSIS_REGISTRY
    from marivo.datasource._capabilities.registry import REGISTRY as DATASOURCE_REGISTRY
    from marivo.semantic._capabilities.registry import REGISTRY as SEMANTIC_REGISTRY

    assert set(mv.__marivo_telemetry_capabilities__) == _expected_instrumented(
        ANALYSIS_REGISTRY._descriptors
    )
    assert set(md.__marivo_telemetry_capabilities__) == _expected_instrumented(
        DATASOURCE_REGISTRY._descriptors
    )
    assert set(ms.__marivo_telemetry_capabilities__) == _expected_instrumented(
        SEMANTIC_REGISTRY._descriptors
    )
    assert inspect.signature(mv.Session.observe).parameters["analysis_purpose"].default is None


def test_datasource_authoring_error_preserves_code_and_stage() -> None:
    from marivo.datasource.errors import (
        DatasourceAuthoringError,
        DatasourceObservedEffects,
        repair,
    )

    error = DatasourceAuthoringError(
        code="scope_missing",
        stage="preflight",
        expected="explicit scope",
        received="none",
        reason="blocked",
        effect_observed=DatasourceObservedEffects(query_executed=False),
        repair=repair(
            kind="rescope",
            canonical_id="inspect",
            action="Provide an explicit scope.",
        ),
    )
    assert error.code == "scope_missing"
    assert error.stage == "preflight"
