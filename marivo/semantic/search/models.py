"""Captured search documents, settings and ranking results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

_RANKING_VERSION = "catalog-search-v2"


@dataclass(frozen=True)
class SearchDocument:
    """A captured object projection; field labels retain their source identity."""

    id: str
    kind: str
    path: str
    name: str
    domain: str | None
    entity: str | None
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class LexicalSearchConfig:
    """Frozen, model-free search configuration."""

    candidate_window: int = 30
    mode: Literal["lexical"] = "lexical"


@dataclass(frozen=True)
class DisabledReranker:
    """Explicit absence of reranking."""

    mode: Literal["disabled"] = "disabled"


@dataclass(frozen=True)
class LocalReranker:
    """An explicitly prepared, revision-pinned local CrossEncoder."""

    model: str
    revision: str
    max_tokens: int = 512
    mode: Literal["local"] = "local"


Reranker: TypeAlias = DisabledReranker | LocalReranker


@dataclass(frozen=True)
class HybridSearchConfig:
    """Frozen local model settings and an explicitly calibrated relevance gate."""

    embedding_model: str
    embedding_revision: str
    calibration_id: str
    min_similarity: float
    query_prefix: str = ""
    document_prefix: str = ""
    max_tokens: int = 512
    batch_size: int = 16
    candidate_window: int = 30
    reranker: Reranker = DisabledReranker()
    min_reranker_score: float = 0.0
    mode: Literal["hybrid"] = "hybrid"


SearchConfig: TypeAlias = LexicalSearchConfig | HybridSearchConfig


@dataclass(frozen=True)
class BrowseRetrieval:
    """Deterministic identity-ordered browsing without model calls."""

    mode: Literal["browse"] = "browse"
    candidate_window: int = 0
    ranking_version: str = _RANKING_VERSION


@dataclass(frozen=True)
class RelatedRetrieval:
    """Loaded graph navigation, with no query score or model call."""

    candidate_window: int
    mode: Literal["related"] = "related"
    ranking_version: str = _RANKING_VERSION


@dataclass(frozen=True)
class LexicalRetrieval:
    """Lexical retrieval metadata; window is independent of page size."""

    candidate_window: int = 30
    mode: Literal["lexical"] = "lexical"
    ranking_version: str = _RANKING_VERSION


@dataclass(frozen=True)
class HybridRetrieval:
    """Actual local model, gate and reranking identity for a fixed ranking."""

    embedding_model: str
    embedding_revision: str
    calibration_id: str
    reranker: Reranker
    candidate_window: int = 30
    mode: Literal["hybrid"] = "hybrid"
    ranking_version: str = _RANKING_VERSION


Retrieval: TypeAlias = BrowseRetrieval | RelatedRetrieval | LexicalRetrieval | HybridRetrieval


@dataclass(frozen=True)
class SearchHit:
    """Query metadata, kept separate from captured Entry definitions."""

    id: str
    score: float
    channels: tuple[str, ...]
    snippets: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SearchResult:
    """One page of a fixed candidate ranking."""

    items: tuple[SearchHit, ...]
    candidate_count: int
    retrieval: Retrieval
    next_cursor: str | None
