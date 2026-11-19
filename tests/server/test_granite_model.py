"""Managed Granite assets are verified before the read service can use them."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.error import URLError

import pytest

from marivo.server import granite_model

_FILES = {"model.onnx": b"pinned model", "tokenizer.json": b"pinned tokenizer"}


@pytest.fixture
def downloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setenv("MARIVO_VECTOR_MODEL_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        granite_model,
        "_ASSETS",
        tuple((name, name, len(data), sha256(data).hexdigest()) for name, data in _FILES.items()),
    )
    requested: list[str] = []

    def serve_asset(url: str, *, timeout: int) -> BytesIO:
        filename = url.rsplit("/", 1)[-1]
        requested.append(filename)
        return BytesIO(_FILES[filename])

    monkeypatch.setattr(granite_model, "urlopen", serve_asset)
    return requested


def test_reuses_verified_assets_and_repairs_corruption(
    tmp_path: Path, downloads: list[str]
) -> None:
    model = granite_model.prepare_granite_model()
    assert model.is_relative_to(tmp_path)
    for name, data in _FILES.items():
        assert model.with_name(name).read_bytes() == data
    assert downloads == ["model.onnx", "tokenizer.json"]

    downloads.clear()
    assert granite_model.prepare_granite_model() == model
    assert downloads == []

    model.write_bytes(b"damaged data")
    assert granite_model.prepare_granite_model() == model
    assert model.read_bytes() == _FILES["model.onnx"]
    assert downloads == ["model.onnx"]


def test_rejects_unverified_payload_without_publishing(
    tmp_path: Path, downloads: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(granite_model, "urlopen", lambda *a, **kw: BytesIO(b"bad download"))
    with pytest.raises(ValueError, match="verification"):
        granite_model.prepare_granite_model()
    assert list(tmp_path.glob("*/*")) == []


def test_retries_a_transient_asset_download_failure(
    downloads: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = False

    def disconnect_once(url: str, *, timeout: int) -> BytesIO:
        nonlocal failed
        if not failed:
            failed = True
            raise URLError("connection closed before response")
        filename = url.rsplit("/", 1)[-1]
        downloads.append(filename)
        return BytesIO(_FILES[filename])

    monkeypatch.setattr(granite_model, "urlopen", disconnect_once)
    monkeypatch.setattr(granite_model, "sleep", lambda _: None)
    model = granite_model.prepare_granite_model()
    assert failed and downloads == ["model.onnx", "tokenizer.json"]
    assert model.read_bytes() == _FILES["model.onnx"]
