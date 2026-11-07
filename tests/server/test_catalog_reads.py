"""Catalog task ownership and failure diagnostics at the worker boundary."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from marivo.server.catalog_reads import CatalogReads
from marivo.server.http import _map_error
from marivo.server.wire import JsonObject, Limits, ServiceError


def test_disconnected_reader_does_not_cancel_shared_catalog_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        catalog = CatalogReads(tmp_path, Limits())
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def worker() -> JsonObject:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"entries": []}

        monkeypatch.setattr(catalog, "_run_worker", worker)
        first = asyncio.create_task(catalog.get())
        second: asyncio.Task[object] | None = None
        try:
            await asyncio.wait_for(started.wait(), 5)
            second = asyncio.create_task(catalog.get())
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            with pytest.raises(ServiceError) as failure:
                await catalog.get(reload=True)
            assert failure.value.code == "catalog_load_in_progress"
            release.set()
            snapshot = await asyncio.wait_for(second, 5)
            assert snapshot is catalog.current
            assert calls == 1
            assert await catalog.get() is snapshot
        finally:
            release.set()
            first.cancel()
            if second is not None:
                second.cancel()
            await asyncio.gather(
                first, *([second] if second is not None else []), return_exceptions=True
            )
            await catalog.close()

    asyncio.run(scenario())


def test_catalog_worker_crash_keeps_traceback_out_of_http_error(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "marivo.toml").write_text('[project]\nname = "catalog-crash"\n')
    models = tmp_path / "models/semantic/sales"
    models.mkdir(parents=True)
    (models / "_domain.py").write_text(
        "import marivo.semantic as ms\n"
        "ms.domain(name='sales', owner='Finance')\n"
        'raise KeyboardInterrupt("catalog crash witness")\n'
    )

    async def scenario() -> None:
        catalog = CatalogReads(tmp_path, Limits())
        try:
            with pytest.raises(ServiceError) as failure:
                await catalog.get()
            assert failure.value.code == "catalog_load_failed"
            assert "catalog crash witness" not in str(failure.value.payload())
            assert catalog.current is None
        finally:
            await catalog.close()

    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        asyncio.run(scenario())
    stderr = capfd.readouterr().err
    assert "Traceback (most recent call last)" in stderr
    assert "KeyboardInterrupt: catalog crash witness" in stderr
    assert "Catalog worker exited with status" in caplog.text


def test_unexpected_request_error_logs_operation_and_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken_read() -> None:
        raise RuntimeError("request crash witness")

    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        try:
            broken_read()
        except RuntimeError as error:
            failure = _map_error(error, "frame")
    assert failure.code == "internal_error"
    assert "request crash witness" not in str(failure.payload())
    assert "Read service failure during frame" in caplog.text
    assert "Traceback (most recent call last)" in caplog.text
    assert "broken_read" in caplog.text
    assert "RuntimeError: request crash witness" in caplog.text
