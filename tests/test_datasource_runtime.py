from pathlib import Path

import pytest

import marivo.datasource as md
from marivo.datasource import runtime


class FakeBackend:
    def __init__(self) -> None:
        self.disconnect_calls = 0

    def disconnect(self) -> None:
        self.disconnect_calls += 1


def test_use_backend_disconnects_after_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(runtime, "_build_backend_from_store", lambda name, project_root: backend)

    service = runtime.DatasourceConnectionService(project_root=tmp_path)
    with service.use_backend("warehouse") as received:
        assert received is backend
        assert backend.disconnect_calls == 0

    assert backend.disconnect_calls == 1


def test_use_backend_disconnects_after_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = FakeBackend()
    monkeypatch.setattr(runtime, "_build_backend_from_store", lambda name, project_root: backend)
    service = runtime.DatasourceConnectionService(project_root=tmp_path)

    with pytest.raises(RuntimeError, match="boom"), service.use_backend("warehouse"):
        raise RuntimeError("boom")

    assert backend.disconnect_calls == 1


def test_session_backend_is_reused_until_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created: list[FakeBackend] = []

    def build(name: str, project_root: Path | None) -> FakeBackend:
        backend = FakeBackend()
        created.append(backend)
        return backend

    monkeypatch.setattr(runtime, "_build_backend_from_store", build)
    service = runtime.DatasourceConnectionService(project_root=tmp_path)

    first = service.session_backend("warehouse")
    second = service.session_backend("warehouse")

    assert first is second
    assert len(created) == 1
    service.close_all()
    assert created[0].disconnect_calls == 1


def test_datasource_module_exposes_runtime_service() -> None:
    assert md.DatasourceConnectionService is runtime.DatasourceConnectionService
