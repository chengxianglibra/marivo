"""Experimental offline Granite encoder and snapshot-local exact index."""

from __future__ import annotations

import importlib
import logging
import sys
from contextlib import suppress
from ctypes import CDLL, c_size_t
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from platform import machine, system
from threading import Lock
from typing import TYPE_CHECKING, Protocol, cast

import numpy as np
import numpy.typing as npt

from marivo.semantic.spi import EmbeddingBackend, exact_identity_matches, rank_similarity
from marivo.server.granite_cache import CachedVectors, GraniteCache, fingerprint_model
from marivo.server.wire import HELP, ServiceError

if TYPE_CHECKING:
    from collections.abc import Callable

    from marivo.semantic.spi import SearchDocument

_DIMENSIONS = 384
_BATCH_SIZE = 8
_MAX_TOKENS = 256
_PAD = "<|endoftext|>"


class _Encoding(Protocol):
    @property
    def ids(self) -> list[int]: ...

    @property
    def attention_mask(self) -> list[int]: ...


class _Tokenizer(Protocol):
    def token_to_id(self, token: str) -> int | None: ...
    def enable_padding(self, *, pad_id: int, pad_token: str) -> None: ...
    def enable_truncation(self, max_length: int) -> None: ...
    def encode_batch(self, inputs: list[str]) -> list[_Encoding]: ...


class _SessionOptions(Protocol):
    intra_op_num_threads: int
    inter_op_num_threads: int

    def add_session_config_entry(self, key: str, value: str) -> None: ...


class _Session(Protocol):
    def run(self, outputs: None, inputs: dict[str, npt.NDArray[np.int64]]) -> list[object]: ...


def _encode_error() -> ServiceError:
    return ServiceError(
        "service_unavailable",
        "The experimental Granite search encoder failed.",
        "catalog.search",
        "Check the service logs and the local Granite INT8 model, tokenizer and ONNX Runtime.",
        HELP,
    )


def _release_tokenizer_scratch() -> None:
    # On glibc, large temporary tokenizer JSON allocations otherwise remain
    # resident alongside the ONNX session and its first inference buffers.
    if sys.platform == "linux":
        with suppress(AttributeError, OSError):
            trim = CDLL(None).malloc_trim
            trim.argtypes = [c_size_t]
            trim(0)


class GraniteEncoder:
    dimensions = _DIMENSIONS

    def __init__(self, model_path: Path, *, asset_fingerprint: str | None = None) -> None:
        self._model_path = model_path
        self._asset_fingerprint = asset_fingerprint
        self._lock = Lock()
        self._engine = self._load_engine() if asset_fingerprint is None else None

    @property
    def model_fingerprint(self) -> str:
        if self._asset_fingerprint is not None:
            return self._asset_fingerprint
        return fingerprint_model(self._model_path)

    def _load_engine(self) -> tuple[_Tokenizer, _Session]:
        model_path = self._model_path
        tokenizers = importlib.import_module("tokenizers")
        # Optional native dependencies are narrowed to the operations used by this encoder.
        tokenizer = cast(
            "_Tokenizer",
            tokenizers.Tokenizer.from_file(str(model_path.with_name("tokenizer.json"))),
        )
        pad_id = tokenizer.token_to_id(_PAD)
        if pad_id is None:
            raise ValueError("The Granite tokenizer has no padding token.")
        tokenizer.enable_padding(pad_id=pad_id, pad_token=_PAD)
        tokenizer.enable_truncation(_MAX_TOKENS)
        _release_tokenizer_scratch()
        runtime = importlib.import_module("onnxruntime")
        options = cast("_SessionOptions", runtime.SessionOptions())
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        if model_path.suffix == ".ort":
            options.add_session_config_entry("session.use_memory_mapped_ort_model", "1")
            options.add_session_config_entry("session.use_ort_model_bytes_for_initializers", "1")
        session = cast(
            "_Session",
            runtime.InferenceSession(
                str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
            ),
        )
        if (
            self._asset_fingerprint is not None
            and fingerprint_model(model_path) != self._asset_fingerprint
        ):
            raise ValueError("Granite model assets changed. Restart the read service.")
        return tokenizer, session

    def encode_texts(self, texts: list[str]) -> npt.NDArray[np.float32]:
        with self._lock:
            if self._engine is None:
                self._engine = self._load_engine()
            tokenizer, session = self._engine
            vectors = np.empty((len(texts), _DIMENSIONS), dtype=np.float32)
            # Granite INT8 shares activation scales across an input tensor.
            # Singleton inference makes each stored vector independent of its peers.
            for row, text in enumerate(texts):
                (tokens,) = tokenizer.encode_batch([text])
                (hidden,) = session.run(
                    None,
                    {
                        "input_ids": np.asarray([tokens.ids], dtype=np.int64),
                        "attention_mask": np.asarray([tokens.attention_mask], dtype=np.int64),
                    },
                )
                if (
                    not isinstance(hidden, np.ndarray)
                    or hidden.ndim != 3
                    or hidden.shape[0] != 1
                    or hidden.shape[-1] != _DIMENSIONS
                    or hidden.shape[1] == 0
                ):
                    raise ValueError("Expected Granite token embeddings with 384 dimensions.")
                vector = np.array(hidden[:, 0, :], dtype=np.float32, copy=True)
                norm = np.linalg.norm(vector, axis=1, keepdims=True)
                if not np.all(np.isfinite(norm) & (norm > 0)):
                    raise ValueError("Granite produced invalid embeddings.")
                vector /= norm
                vectors[row] = vector[0]
            return vectors


def _document_text(doc: SearchDocument) -> str:
    return "\n".join(
        (
            f"id: {doc.id}",
            f"path: {doc.path}",
            f"name: {doc.name}",
            *(f"{label}: {value}" for label, value in doc.fields if value),
        )
    )


@dataclass(frozen=True)
class GraniteIndex:
    encoder: EmbeddingBackend[npt.NDArray[np.float32]]
    matrix: npt.NDArray[np.float32]
    row_keys: tuple[bytes, ...]

    @classmethod
    def build(
        cls,
        encoder: EmbeddingBackend[npt.NDArray[np.float32]],
        documents: tuple[SearchDocument, ...],
        check_budget: Callable[[], None],
        *,
        cache: GraniteCache | None = None,
        previous: GraniteIndex | None = None,
    ) -> GraniteIndex:
        texts = tuple(_document_text(doc) for doc in documents)
        # The canonical object ID is part of the serialized input. Changes to
        # fields omitted from that input keep the same fingerprint.
        row_keys = tuple(sha256(text.encode("utf-8")).digest() for text in texts)
        reusable: GraniteIndex | CachedVectors | None = None
        if previous is not None and previous.encoder is encoder:
            reusable = previous
        key = None
        if cache is not None:
            recipe = (
                f"granite-cls-unit-f32-single-document-v2/{_MAX_TOKENS}/{system()}/{machine()}/"
                f"{version('onnxruntime')}/{version('tokenizers')}/{np.__version__}"
            )
            key = cache.key(encoder.model_fingerprint, recipe)
            if reusable is None:
                reusable = cache.load(key)
        if reusable is not None and reusable.row_keys == row_keys:
            return cls(encoder, reusable.matrix, row_keys)

        dimensions = encoder.dimensions
        if dimensions is None:
            raise ValueError("An index requires known embedding dimensions.")
        matrix = np.empty((len(texts), dimensions), dtype=np.float32)
        old_rows = (
            {row_key: row for row, row_key in enumerate(reusable.row_keys)}
            if reusable is not None
            else {}
        )
        pending: list[int] = []
        for row, row_key in enumerate(row_keys):
            old_row = old_rows.get(row_key)
            if reusable is not None and old_row is not None:
                matrix[row] = reusable.matrix[old_row]
            else:
                pending.append(row)
        # Release a previous on-disk matrix before any native model allocation.
        del reusable, old_rows
        for offset in range(0, len(pending), _BATCH_SIZE):
            check_budget()
            rows = pending[offset : offset + _BATCH_SIZE]
            matrix[rows] = encoder.encode_texts([texts[row] for row in rows])
        if cache is not None and key is not None:
            check_budget()
            try:
                cache.store(key, row_keys, matrix)
            except OSError:
                logging.getLogger("uvicorn.error").warning(
                    "Granite vector cache could not be written", exc_info=True
                )
        matrix.setflags(write=False)
        return cls(encoder, matrix, row_keys)

    def rank(
        self,
        query: str,
        selected: tuple[tuple[int, SearchDocument], ...],
        window: int,
        *,
        min_similarity: float,
    ) -> tuple[str, ...]:
        if not selected:
            return ()
        try:
            scores = self.matrix @ self.encoder.encode_texts([query])[0]
        except Exception as exc:
            logging.getLogger("uvicorn.error").exception("Granite query encoding failed")
            raise _encode_error() from exc
        candidates = rank_similarity(
            ((document.id, float(scores[row])) for row, document in selected),
            minimum=min_similarity,
        )
        ranked = [identity for identity, _ in candidates[:window]]
        exact = exact_identity_matches((document for _, document in selected), query)
        return (
            *(identity for identity in ranked if identity in exact),
            *sorted(exact.difference(ranked)),
            *(identity for identity in ranked if identity not in exact),
        )
