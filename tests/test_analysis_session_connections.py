from __future__ import annotations

from dataclasses import dataclass

from marivo.analysis.session._connections import AnalysisConnectionRuntime
from marivo.datasource.runtime import DatasourceConnectionService


@dataclass
class FakeBackend:
    name: str
    closed: bool = False

    def disconnect(self) -> None:
        self.closed = True


def test_datasource_connection_service_uses_explicit_backend_mapping(tmp_path) -> None:
    calls: list[str] = []

    def build() -> FakeBackend:
        calls.append("warehouse")
        return FakeBackend("warehouse")

    service = DatasourceConnectionService(project_root=tmp_path, backends={"warehouse": build})

    first = service.session_backend("warehouse")
    second = service.session_backend("warehouse")

    assert first is second
    assert calls == ["warehouse"]
    service.close_all()
    assert first.closed


def test_datasource_connection_service_uses_backend_factory(tmp_path) -> None:
    calls: list[str] = []
    service = DatasourceConnectionService(
        project_root=tmp_path,
        backend_factory=lambda name: calls.append(name) or FakeBackend(name),
    )

    assert service.session_backend("a").name == "a"
    assert service.session_backend("b").name == "b"
    assert calls == ["a", "b"]


def test_analysis_connection_runtime_keeps_capture_and_validation_state(tmp_path) -> None:
    runtime = AnalysisConnectionRuntime(
        DatasourceConnectionService(
            project_root=tmp_path,
            backends={"warehouse": lambda: FakeBackend("warehouse")},
        )
    )

    assert runtime.should_mark_validated("warehouse")
    runtime.mark_validated("warehouse")
    assert not runtime.should_mark_validated("warehouse")

    runtime.begin_query_capture()
    runtime.record_query({"sql": "select 1"})
    assert runtime.take_captured_queries() == [{"sql": "select 1"}]
    assert runtime.take_captured_queries() == []
