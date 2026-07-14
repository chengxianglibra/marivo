"""Tests for local OpenTelemetry-shaped usage telemetry."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

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


def _attr(record: dict[str, object], key: str) -> object:
    attrs = record["attributes"]
    assert isinstance(attrs, list)
    for item in attrs:
        assert isinstance(item, dict)
        if item["key"] != key:
            continue
        value = item["value"]
        assert isinstance(value, dict)
        return next(iter(value.values()))
    raise AssertionError(f"missing telemetry attribute {key!r}")


@pytest.fixture
def telemetry_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n', encoding="utf-8")
    monkeypatch.delenv("MARIVO_TELEMETRY", raising=False)
    yield tmp_path


def test_track_event_writes_otlp_jsonl_with_session_id(telemetry_project: Path) -> None:
    from marivo.telemetry import track_event

    session = SimpleNamespace(id="sess_test", project_root=telemetry_project)

    track_event(
        "marivo.analysis.observe",
        family="core",
        intent="observe",
        session=session,
        attributes={"marivo.analysis.semantic_kind": "panel"},
    )

    entry = _records(telemetry_project / ".marivo" / "telemetry" / "events.jsonl")[0]
    record = _log_record(entry)
    assert record["body"] == {"stringValue": "marivo.analysis.observe"}
    assert _attr(record, "marivo.event.name") == "marivo.analysis.observe"
    assert _attr(record, "marivo.intent.family") == "core"
    assert _attr(record, "marivo.intent.name") == "observe"
    assert _attr(record, "marivo.session.id") == "sess_test"
    assert _attr(record, "marivo.analysis.semantic_kind") == "panel"


def test_track_operation_records_error_and_reraises(telemetry_project: Path) -> None:
    from marivo.telemetry import track_operation

    session = SimpleNamespace(id="sess_test", project_root=telemetry_project)

    with (
        pytest.raises(ValueError, match="boom"),
        track_operation(
            "marivo.analysis.compare",
            family="core",
            intent="compare",
            session=session,
        ),
    ):
        raise ValueError("boom")

    entry = _records(telemetry_project / ".marivo" / "telemetry" / "events.jsonl")[0]
    record = _log_record(entry)
    assert _attr(record, "marivo.operation.status") == "error"
    assert _attr(record, "marivo.error.type") == "ValueError"
    assert _attr(record, "marivo.session.id") == "sess_test"


def test_cli_init_system_exit_records_error_telemetry(telemetry_project: Path) -> None:
    from marivo.cli import init_project

    (telemetry_project / "marivo.toml").write_text("this is not valid [[toml", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        init_project(project_dir=telemetry_project)

    assert exc_info.value.code == 1
    entry = _records(telemetry_project / ".marivo" / "telemetry" / "events.jsonl")[0]
    record = _log_record(entry)
    assert _attr(record, "marivo.event.name") == "marivo.cli.init"
    assert _attr(record, "marivo.operation.status") == "error"
    assert _attr(record, "marivo.error.type") == "SystemExit"


def test_env_off_disables_telemetry(
    telemetry_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.telemetry import track_event

    monkeypatch.setenv("MARIVO_TELEMETRY", "off")

    track_event("marivo.cli.init", family="cli", intent="init", project_root=telemetry_project)

    assert not (telemetry_project / ".marivo" / "telemetry" / "events.jsonl").exists()


def test_marivo_toml_off_disables_telemetry(telemetry_project: Path) -> None:
    from marivo.telemetry import track_event

    (telemetry_project / "marivo.toml").write_text(
        '[project]\nname = "test"\n\n[telemetry]\nenabled = "off"\n',
        encoding="utf-8",
    )

    track_event("marivo.cli.init", family="cli", intent="init", project_root=telemetry_project)

    assert not (telemetry_project / ".marivo" / "telemetry" / "events.jsonl").exists()


def test_env_on_overrides_marivo_toml_off(
    telemetry_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.telemetry import track_event

    (telemetry_project / "marivo.toml").write_text(
        '[project]\nname = "test"\n\n[telemetry]\nenabled = "off"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MARIVO_TELEMETRY", "on")

    track_event("marivo.cli.init", family="cli", intent="init", project_root=telemetry_project)

    entry = _records(telemetry_project / ".marivo" / "telemetry" / "events.jsonl")[0]
    assert _attr(_log_record(entry), "marivo.event.name") == "marivo.cli.init"


def test_legacy_numeric_env_value_does_not_disable_telemetry(
    telemetry_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.telemetry import track_event

    monkeypatch.setenv("MARIVO_TELEMETRY", "0")

    track_event("marivo.cli.init", family="cli", intent="init", project_root=telemetry_project)

    entry = _records(telemetry_project / ".marivo" / "telemetry" / "events.jsonl")[0]
    assert _attr(_log_record(entry), "marivo.event.name") == "marivo.cli.init"


def test_session_intent_error_records_local_telemetry_with_session_id(
    telemetry_project: Path,
) -> None:
    session = mv.session.get_or_create(
        name="demo", backend_factory=lambda _name: None, use_datasources=False
    )

    with pytest.raises(mv.errors.AnalysisError):
        session.assess_quality(object())

    entry = _records(telemetry_project / ".marivo" / "telemetry" / "events.jsonl")[-1]
    record = _log_record(entry)
    assert _attr(record, "marivo.event.name") == "marivo.analysis.assess_quality"
    assert _attr(record, "marivo.intent.family") == "core"
    assert _attr(record, "marivo.intent.name") == "assess_quality"
    assert _attr(record, "marivo.operation.status") == "error"
    assert _attr(record, "marivo.error.type") == "AnalysisError"
    assert _attr(record, "marivo.session.id") == session.id


def test_declared_intent_coverage_matches_requested_scope() -> None:
    from marivo.telemetry import TELEMETRY_INTENTS

    assert {
        "marivo.analysis.assess_quality",
        "marivo.analysis.attribute",
        "marivo.analysis.compare",
        "marivo.analysis.correlate",
        "marivo.analysis.discover.cross_sectional_outliers",
        "marivo.analysis.discover.driver_axes",
        "marivo.analysis.discover.interesting_slices",
        "marivo.analysis.discover.interesting_windows",
        "marivo.analysis.discover.period_shifts",
        "marivo.analysis.discover.point_anomalies",
        "marivo.analysis.forecast",
        "marivo.analysis.hypothesis_test",
        "marivo.analysis.observe",
        "marivo.analysis.frame.transform.bottomk",
        "marivo.analysis.frame.transform.filter",
        "marivo.analysis.frame.transform.normalize",
        "marivo.analysis.frame.transform.rank",
        "marivo.analysis.frame.transform.rollup",
        "marivo.analysis.frame.transform.slice",
        "marivo.analysis.frame.transform.topk",
        "marivo.analysis.frame.transform.window",
        "marivo.cli.init",
    } == TELEMETRY_INTENTS
