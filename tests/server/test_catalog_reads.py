"""Catalog task ownership and failure diagnostics at the worker boundary."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from unittest.mock import create_autospec

import pytest

from marivo.datasource.ir import AiContextIR
from marivo.semantic.catalog_entries import DatasourceDefinition
from marivo.server import granite_search
from marivo.server.catalog_projection import _datasource_properties
from marivo.server.catalog_reads import CatalogReads
from marivo.server.wire import JsonObject, Limits, ServiceError


def test_datasource_projection_filters_unapproved_or_unsafe_shapes() -> None:
    definition = DatasourceDefinition(
        context=AiContextIR(),
        backend_type="trino",
        wall_clock_timezone=None,
        fields=(
            ("host", "user:password@example.test"),
            ("port", True),
            ("catalog", "hive"),
            ("path", "/unrelated/private"),
            ("client_tags", ("standby", "token=private")),
            ("session_properties", {"password": "hidden"}),
        ),
        env_refs=(("user", "PRIVATE_USER"),),
        entities=(),
    )
    assert _datasource_properties(definition) == {
        "backend_type": "trino",
        "wall_clock_timezone": None,
        "properties": {"catalog": "hive"},
        "has_omitted_fields": True,
    }
    # Even allowlisted keys are rejected before materializing nested or oversized values.
    nested = replace(definition, fields=(("client_tags", (("hidden",),)),))
    assert _datasource_properties(nested)["properties"] == {}
    oversized = replace(definition, fields=(("client_tags", ("ok",) * 51),))
    assert _datasource_properties(oversized)["properties"] == {}
    unknown = replace(definition, backend_type="unknown", fields=(("host", "host.test"),))
    assert _datasource_properties(unknown)["properties"] == {}
    assert _datasource_properties(unknown)["has_omitted_fields"] is True


def test_disconnected_reader_does_not_cancel_shared_catalog_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        granite_search, "GraniteEncoder", create_autospec(granite_search.GraniteEncoder)
    )

    async def scenario() -> None:
        catalog = CatalogReads(tmp_path, Limits(), granite_model=tmp_path / "model.onnx")
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
        catalog = CatalogReads(tmp_path, Limits(), granite_model=tmp_path / "model.onnx")
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
    assert "KeyboardInterrupt: catalog crash witness" in stderr
    assert any(record.levelno == logging.ERROR for record in caplog.records)
