from __future__ import annotations

import pytest

from app.adapters.local.local_telemetry import LocalTelemetry
from app.contracts.values import TelemetryEvent

noop_telemetry_factories = [
    ("LocalTelemetry-none", lambda p: LocalTelemetry(sink="none")),
    ("LocalTelemetry-file", lambda p: LocalTelemetry(sink="file", log_path=p / "telemetry.jsonl")),
]


@pytest.mark.parametrize("name,factory", noop_telemetry_factories)
def test_emit_does_not_crash(name, factory, tmp_path):
    tel = factory(tmp_path)
    tel.emit(TelemetryEvent(name="test_event", properties={"key": "value"}))


@pytest.mark.parametrize("name,factory", noop_telemetry_factories)
def test_file_sink_writes(name, factory, tmp_path):
    if "file" not in name:
        pytest.skip("only for file sink")
    tel = factory(tmp_path)
    tel.emit(TelemetryEvent(name="test_event", properties={"key": "value"}))
    content = (tmp_path / "telemetry.jsonl").read_text()
    assert "test_event" in content


@pytest.mark.parametrize("name,factory", noop_telemetry_factories)
def test_none_sink_no_file(name, factory, tmp_path):
    if "file" in name:
        pytest.skip("only for none sink")
    tel = factory(tmp_path)
    tel.emit(TelemetryEvent(name="test_event", properties={}))
    assert not (tmp_path / "telemetry.jsonl").exists()
