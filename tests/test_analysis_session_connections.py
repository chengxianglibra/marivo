from __future__ import annotations

from dataclasses import dataclass

import marivo.analysis as mv
import marivo.semantic as ms
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


def test_analysis_connection_runtime_normalizes_event_watermark_receipts(tmp_path) -> None:
    request = mv.EventWatermarkRequest(
        event_ref=ms.ref.event("commerce.payment_succeeded"),
        event_fingerprint="sha256:event",
        source_entity_ref="commerce.event_log",
        occurred_at_ref="commerce.event_log.event_time",
        required_through="2026-07-03T00:00:00Z",
    )

    class WatermarkBackend(FakeBackend):
        def marivo_event_watermark(
            self,
            received: mv.EventWatermarkRequest,
        ) -> mv.EventWatermarkReceipt:
            assert received == request
            return mv.EventWatermarkReceipt(
                complete_through="2026-07-03T00:00:00Z",
                authority="warehouse_reconciliation",
                observed_at="2026-07-03T01:00:00Z",
            )

    runtime = AnalysisConnectionRuntime(
        DatasourceConnectionService(
            project_root=tmp_path,
            backends={"warehouse": lambda: WatermarkBackend("warehouse")},
        )
    )

    receipt = runtime.event_watermark("warehouse", request)

    assert isinstance(receipt, mv.EventWatermarkReceipt)
    assert receipt.authority == "warehouse_reconciliation"


def test_analysis_connection_runtime_rejects_malformed_event_watermark_receipts(
    tmp_path,
) -> None:
    class InvalidWatermarkBackend(FakeBackend):
        def marivo_event_watermark(
            self,
            _request: mv.EventWatermarkRequest,
        ) -> object:
            return {"complete_through": "2026-07-03T00:00:00Z"}

    runtime = AnalysisConnectionRuntime(
        DatasourceConnectionService(
            project_root=tmp_path,
            backends={"warehouse": lambda: InvalidWatermarkBackend("warehouse")},
        )
    )
    request = mv.EventWatermarkRequest(
        event_ref=ms.ref.event("commerce.payment_succeeded"),
        event_fingerprint="sha256:event",
        source_entity_ref="commerce.event_log",
        occurred_at_ref="commerce.event_log.event_time",
        required_through="2026-07-03T00:00:00Z",
    )

    assert runtime.event_watermark("warehouse", request) is None
