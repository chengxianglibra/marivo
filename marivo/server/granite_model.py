"""Prepare the pinned Granite model used by HTTP vector search."""

from __future__ import annotations

import os
import sys
from hashlib import sha256
from http.client import HTTPException, IncompleteRead
from pathlib import Path
from ssl import SSLEOFError
from tempfile import mkstemp
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

_REVISION = "835ad14087e140460703cf0fae09f97d469d65c2"
_URL = (
    f"https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2/resolve/{_REVISION}"
)
_ASSETS = (
    (
        "onnx/model_quint8_avx2.onnx",
        "model.onnx",
        98_247_878,
        "a6022dd8220ea6f6595562a1328ee216f4a94faa55362f2f4747c80f1e78772e",
    ),
    (
        "tokenizer.json",
        "tokenizer.json",
        25_301_672,
        "4f2842d568e2724370aec203652a42ac783c7937f8347a1a2cc7506d71f1582f",
    ),
)
_CHUNK_BYTES = 1024 * 1024


def _cache_directory() -> Path:
    if override := os.environ.get("MARIVO_VECTOR_MODEL_CACHE_DIR"):
        root = Path(override).expanduser()
    else:
        cache_home = os.environ.get("XDG_CACHE_HOME")
        root = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
        root = root / "marivo" / "models"
    return root / f"granite-97m-multilingual-r2-{_REVISION}"


def _verified(path: Path, size: int, digest: str) -> bool:
    checksum = sha256()
    try:
        if path.stat().st_size != size:
            return False
        with path.open("rb") as file:
            while block := file.read(_CHUNK_BYTES):
                checksum.update(block)
    except FileNotFoundError:
        return False
    return checksum.hexdigest() == digest


def _download(remote_path: str, path: Path, size: int, digest: str) -> None:
    checksum = sha256()
    received = 0
    descriptor, name = mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(name)
    try:
        with (
            os.fdopen(descriptor, "wb") as file,
            urlopen(f"{_URL}/{remote_path}", timeout=30) as response,
        ):
            while block := response.read(_CHUNK_BYTES):
                received += len(block)
                if received > size:
                    break
                checksum.update(block)
                file.write(block)
        if received != size or checksum.hexdigest() != digest:
            raise ValueError("The downloaded bytes failed size or SHA-256 verification.")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _fetch(remote_path: str, path: Path, size: int, digest: str) -> None:
    try:
        _download(remote_path, path, size, digest)
    except HTTPError as error:
        if error.code != 429 and not 500 <= error.code < 600:
            raise
        error.close()
    except (URLError, TimeoutError, ConnectionError, IncompleteRead, SSLEOFError):
        pass
    else:
        return
    sleep(1)
    _download(remote_path, path, size, digest)


def prepare_granite_model() -> Path:
    """Return the verified local model, downloading missing files as needed."""
    directory = _cache_directory()
    for remote_path, filename, size, digest in _ASSETS:
        path = directory / filename
        try:
            if _verified(path, size, digest):
                continue
            directory.mkdir(parents=True, exist_ok=True)
            print(f"Preparing Granite vector search model asset {filename}…", file=sys.stderr)
            _fetch(remote_path, path, size, digest)
        except (HTTPException, OSError, ValueError) as error:
            raise ValueError(
                f"Could not prepare Granite model asset {filename} in {directory}. {error} "
                "Check connectivity to huggingface.co and write access to the model cache, "
                "or use --experimental-granite-model PATH with local assets."
            ) from error
    return directory / "model.onnx"
