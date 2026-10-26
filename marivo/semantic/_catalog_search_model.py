"""Local-only optional model boundary and rebuildable vector cache."""

from __future__ import annotations

import importlib
import math
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Protocol, cast

import numpy as np
import numpy.typing as npt

from marivo.semantic.catalog_search import (
    _PROJECTION_VERSION,
    HybridSearchConfig,
    LocalReranker,
    SearchDocument,
    _atomic_json,
    _digest,
    _hub_model_id,
    _projection,
    _read_json,
)
from marivo.semantic.errors import CatalogSearchError


class _Tokenizer(Protocol):
    def __call__(
        self,
        text: list[str],
        text_pair: list[str] | None = None,
        *,
        truncation: bool,
        return_attention_mask: bool,
    ) -> Mapping[str, object]: ...


class _Encoder(Protocol):
    max_seq_length: int
    tokenizer: _Tokenizer

    def encode(
        self,
        sentences: list[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> npt.NDArray[np.float32]: ...


class _EncoderFactory(Protocol):
    def __call__(
        self,
        model_name_or_path: str,
        *,
        revision: str,
        local_files_only: bool,
        trust_remote_code: bool,
        device: str,
    ) -> _Encoder: ...


class _CrossEncoder(Protocol):
    tokenizer: _Tokenizer

    def predict(
        self,
        sentences: list[list[str]],
        *,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> npt.NDArray[np.float32]: ...


class _CrossEncoderFactory(Protocol):
    def __call__(
        self,
        model_name_or_path: str,
        *,
        revision: str,
        local_files_only: bool,
        trust_remote_code: bool,
        device: str,
        max_length: int,
    ) -> _CrossEncoder: ...


def _failure(stage: str, config: HybridSearchConfig, received: str) -> CatalogSearchError:
    reranker = (
        f"; reranker={config.reranker.model}@{config.reranker.revision}"
        if isinstance(config.reranker, LocalReranker)
        else ""
    )
    return CatalogSearchError(
        stage=stage,
        message=f"Configured hybrid Catalog search failed during {stage}.",
        expected="an installed semantic-search runtime, locally prepared fixed model revision, and complete finite model outputs within the configured token limit",
        received=received,
        action="Install marivo[semantic-search], explicitly prepare the configured model revision in the local Hugging Face cache, or correct the model/input configuration and reload. Hybrid search does not fall back to lexical.",
        configuration=f"{config.embedding_model}@{config.embedding_revision}; calibration={config.calibration_id}{reranker}",
    )


@lru_cache(maxsize=4)
def _load_encoder(model: str, revision: str) -> _Encoder:
    _hub_model_id(model)
    module = importlib.import_module("sentence_transformers")
    # The optional third-party package has no stable typing boundary. This cast
    # narrows only its constructor to the exact operations consumed below.
    constructor = cast("_EncoderFactory", module.SentenceTransformer)
    return constructor(
        model, revision=revision, local_files_only=True, trust_remote_code=False, device="cpu"
    )


@lru_cache(maxsize=2)
def _load_reranker(model: str, revision: str, max_tokens: int) -> _CrossEncoder:
    _hub_model_id(model)
    module = importlib.import_module("sentence_transformers")
    # Same explicit optional-runtime constructor boundary as the embedding model.
    constructor = cast("_CrossEncoderFactory", module.CrossEncoder)
    return constructor(
        model,
        revision=revision,
        local_files_only=True,
        trust_remote_code=False,
        device="cpu",
        max_length=max_tokens,
    )


def _check_lengths(
    tokenizer: _Tokenizer,
    texts: list[str],
    *,
    max_tokens: int,
    config: HybridSearchConfig,
    identities: tuple[str, ...],
    pairs: list[str] | None = None,
) -> None:
    result = tokenizer(texts, text_pair=pairs, truncation=False, return_attention_mask=False)
    ids = result.get("input_ids")
    if not isinstance(ids, list) or len(ids) != len(texts):
        raise _failure(
            "tokenization", config, "tokenizer did not return one input_ids sequence per input"
        )
    for index, sequence in enumerate(ids):
        if not isinstance(sequence, list) or any(not isinstance(token, int) for token in sequence):
            raise _failure("tokenization", config, "tokenizer returned invalid input_ids")
        if len(sequence) > max_tokens:
            raise CatalogSearchError(
                stage="input_length",
                message="A search model input exceeds its declared token budget; it was not silently truncated.",
                expected=f"at most {max_tokens} tokens including prefixes and special tokens",
                received=f"{identities[index]}: {len(sequence)} tokens",
                action="Use a model with a larger supported input length or shorten the captured search projection, then reload and restart the search.",
                configuration=f"{config.embedding_model}@{config.embedding_revision}",
            )


def _vectors(raw: object, count: int, config: HybridSearchConfig) -> tuple[tuple[float, ...], ...]:
    if (
        not isinstance(raw, np.ndarray)
        or raw.ndim != 2
        or raw.shape[0] != count
        or raw.shape[1] < 1
    ):
        raise _failure(
            "encoding",
            config,
            "embedding output must have shape (input_count, positive_dimensions)",
        )
    rows: list[tuple[float, ...]] = []
    for array in raw:
        values = tuple(float(value) for value in array)
        norm = math.sqrt(sum(value * value for value in values))
        if not math.isfinite(norm) or norm <= 0.0:
            raise _failure(
                "encoding", config, "embedding output contains a non-finite or zero vector"
            )
        rows.append(tuple(value / norm for value in values))
    return tuple(rows)


def _cached_vector(path: Path) -> tuple[float, ...] | None:
    try:
        raw = _read_json(path)
        if not isinstance(raw, list) or not raw:
            return None
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in raw
        ):
            return None
        values = tuple(float(value) for value in raw if isinstance(value, (int, float)))
        norm = math.sqrt(sum(value * value for value in values))
        return values if abs(norm - 1.0) < 1e-5 else None
    except (OSError, ValueError):
        return None


def _embedding_projection(document: SearchDocument) -> str:
    # Template v1 keeps every own constraint and one-hop authored descriptions.
    # Inherited guardrails remain in the full lexical document and Entry context;
    # they are not recursively repeated in each dependent model input.
    selected = SearchDocument(
        document.id,
        document.kind,
        document.path,
        document.name,
        document.domain,
        document.entity,
        tuple(
            (label, text) for label, text in document.fields if not label.startswith("guardrail.")
        ),
    )
    return _projection(selected)


def vector_search(
    documents: tuple[SearchDocument, ...],
    query: str,
    *,
    workspace_dir: Path,
    snapshot: str,
    config: HybridSearchConfig,
) -> tuple[tuple[str, float], ...]:
    """Score the complete filtered matrix for recall and fused-union gating.

    The caller applies the fixed vector recall window separately. Keeping every
    scalar allows lexical-only candidates to receive the same relevance gate.
    """
    if not documents:
        return ()
    cache = workspace_dir / ".marivo/semantic/search"
    encoding = {
        "model": config.embedding_model,
        "revision": config.embedding_revision,
        "document_prefix": config.document_prefix,
        "max_tokens": config.max_tokens,
        "normalization": "l2-float32-v1",
        "projection": _PROJECTION_VERSION,
        "embedding_projection": "own-constraints-one-hop-descriptions-v1",
    }
    keys = {
        document.id: _digest({"encoding": encoding, "text": _projection(document)})
        for document in documents
    }
    vectors: dict[str, tuple[float, ...]] = {}
    missing: list[SearchDocument] = []
    for document in documents:
        vector = _cached_vector(cache / "vectors" / f"{keys[document.id]}.json")
        if vector is None:
            missing.append(document)
        else:
            vectors[document.id] = vector
    try:
        encoder = _load_encoder(config.embedding_model, config.embedding_revision)
        max_tokens = min(config.max_tokens, encoder.max_seq_length)
        for offset in range(0, len(missing), config.batch_size):
            batch = missing[offset : offset + config.batch_size]
            texts = [config.document_prefix + _embedding_projection(document) for document in batch]
            _check_lengths(
                encoder.tokenizer,
                texts,
                max_tokens=max_tokens,
                config=config,
                identities=tuple(document.id for document in batch),
            )
            encoded = _vectors(
                encoder.encode(
                    texts,
                    batch_size=config.batch_size,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                ),
                len(batch),
                config,
            )
            for document, vector in zip(batch, encoded, strict=True):
                vectors[document.id] = vector
                _atomic_json(cache / "vectors" / f"{keys[document.id]}.json", vector)
        _check_lengths(
            encoder.tokenizer,
            [config.query_prefix + query],
            max_tokens=max_tokens,
            config=config,
            identities=("query",),
        )
        query_vector = _vectors(
            encoder.encode(
                [config.query_prefix + query],
                batch_size=1,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            ),
            1,
            config,
        )[0]
        dimensions = {len(vector) for vector in vectors.values()}
        if dimensions != {len(query_vector)}:
            raise _failure("index", config, "cached document and query vector dimensions differ")
        # No manifest exists until every current member and query has encoded.
        index_key = _digest({"snapshot": snapshot, "members": keys, "encoding": encoding})
        _atomic_json(
            cache / "indices" / f"{index_key}.json",
            {
                "snapshot": snapshot,
                "members": keys,
                "encoding": encoding,
                "dimension": len(query_vector),
            },
        )
        ranked = [
            (identity, sum(left * right for left, right in zip(vector, query_vector, strict=True)))
            for identity, vector in vectors.items()
        ]
        return tuple(sorted(ranked, key=lambda item: (-item[1], item[0])))
    except CatalogSearchError:
        raise
    except Exception as exc:
        # External model runtimes raise provider-specific exception families.
        # Preserve stage/configuration and never publish an incomplete ranking.
        raise _failure("embedding", config, f"{type(exc).__name__}: {exc}") from exc


def rerank_documents(
    query: str, documents: tuple[SearchDocument, ...], config: HybridSearchConfig
) -> dict[str, float]:
    """Rerank the whole fused union; failure rejects the complete search."""
    if not isinstance(config.reranker, LocalReranker):
        return {}
    try:
        reranker = _load_reranker(
            config.reranker.model, config.reranker.revision, config.reranker.max_tokens
        )
        texts = [_projection(document) for document in documents]
        _check_lengths(
            reranker.tokenizer,
            [query] * len(texts),
            pairs=texts,
            max_tokens=config.reranker.max_tokens,
            config=config,
            identities=tuple(document.id for document in documents),
        )
        raw = reranker.predict(
            [[query, text] for text in texts], convert_to_numpy=True, show_progress_bar=False
        )
        if (
            not isinstance(raw, np.ndarray)
            or raw.shape != (len(documents),)
            or not np.isfinite(raw).all()
        ):
            raise _failure(
                "reranking", config, "reranker must return one finite scalar per candidate"
            )
        return {document.id: float(score) for document, score in zip(documents, raw, strict=True)}
    except CatalogSearchError:
        raise
    except Exception as exc:
        raise _failure("reranking", config, f"{type(exc).__name__}: {exc}") from exc
