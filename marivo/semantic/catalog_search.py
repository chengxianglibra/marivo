"""Snapshot-local lexical and optional hybrid Catalog retrieval.

This module consumes captured documents only. It never imports Catalog, opens a
business datasource, downloads a model, or changes a semantic definition.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from collections import Counter
from contextlib import suppress
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal, TypeAlias

from marivo._compat import tomllib
from marivo.config import PROJECT_MANIFEST
from marivo.semantic.errors import CatalogSearchError

_RANKING_VERSION = "catalog-search-v2"
_PROJECTION_VERSION = "catalog-search-document-v1"
_CACHE_VERSION = 1


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


def _config_error(field: str, expected: str, received: object) -> CatalogSearchError:
    return CatalogSearchError(
        stage="configuration",
        message=f"Invalid marivo.toml [semantic.search] setting: {field}.",
        expected=expected,
        received=repr(received),
        action="Correct [semantic.search] in marivo.toml and reload the Catalog.",
    )


def _hub_model_id(value: str) -> str:
    """Require a pinned Hub identity, never a local path that ignores revision."""
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", value)
        or Path(value).exists()
    ):
        raise _config_error(
            "model", "a namespace/model Hub identifier without a conflicting local path", value
        )
    return value


def _table(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise _config_error(field, "a TOML table", value)
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _text(table: dict[str, object], key: str, *, default: str | None = None) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or (default is None and not value.strip()):
        raise _config_error(key, "non-empty text" if default is None else "text", value)
    return value


def _positive_integer(table: dict[str, object], key: str, default: int) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _config_error(key, "a positive integer", value)
    return value


def _number(table: dict[str, object], key: str) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _config_error(key, "an explicitly calibrated finite number", value)
    return float(value)


def _revision(table: dict[str, object], key: str) -> str:
    revision = _text(table, key)
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise _config_error(key, "a fixed 40-character lowercase model commit hash", revision)
    return revision


def load_search_config(workspace_dir: Path) -> SearchConfig:
    """Read and freeze search settings without importing or invoking a model.

    ``[semantic.search]`` defaults to lexical. Hybrid requires embedding_model,
    embedding_revision and ``[semantic.search.relevance]`` calibration_id and
    min_similarity. Prefixes default to empty text. Model files must already be
    present in the local Hugging Face cache at the fixed revision.
    """
    manifest = workspace_dir / PROJECT_MANIFEST
    if not manifest.is_file():
        return LexicalSearchConfig()
    try:
        with manifest.open("rb") as stream:
            raw: object = tomllib.load(stream)
    except (OSError, ValueError) as exc:
        raise _config_error("manifest", "readable valid TOML", str(exc)) from exc
    root = _table(raw, "manifest")
    semantic = _table(root.get("semantic", {}), "semantic")
    table = _table(semantic.get("search", {}), "semantic.search")
    mode = table.get("mode", "hybrid" if "embedding_model" in table else "lexical")
    window = _positive_integer(table, "candidate_window", 30)
    if mode == "lexical":
        if "embedding_model" in table or "reranker" in table:
            raise _config_error("mode", "hybrid when model settings are present", mode)
        return LexicalSearchConfig(candidate_window=window)
    if mode != "hybrid":
        raise _config_error("mode", "'lexical' or 'hybrid'", mode)
    gate = _table(table.get("relevance"), "semantic.search.relevance")
    similarity = _number(gate, "min_similarity")
    if not -1.0 <= similarity <= 1.0:
        raise _config_error(
            "min_similarity", "a calibrated cosine cutoff between -1 and 1", similarity
        )
    reranker: Reranker = DisabledReranker()
    rerank_score = 0.0
    if "reranker" in table:
        rerank = _table(table["reranker"], "semantic.search.reranker")
        reranker = LocalReranker(
            model=_hub_model_id(_text(rerank, "model")),
            revision=_revision(rerank, "revision"),
            max_tokens=_positive_integer(rerank, "max_tokens", 512),
        )
        rerank_score = _number(gate, "min_reranker_score")
    return HybridSearchConfig(
        embedding_model=_hub_model_id(_text(table, "embedding_model")),
        embedding_revision=_revision(table, "embedding_revision"),
        calibration_id=_text(gate, "calibration_id"),
        min_similarity=similarity,
        query_prefix=_text(table, "query_prefix", default=""),
        document_prefix=_text(table, "document_prefix", default=""),
        max_tokens=_positive_integer(table, "max_tokens", 512),
        batch_size=_positive_integer(table, "batch_size", 16),
        candidate_window=window,
        reranker=reranker,
        min_reranker_score=rerank_score,
    )


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def _terms(text: str) -> tuple[str, ...]:
    text = _normalized(re.sub(r"([a-z])([A-Z])", r"\1 \2", text))
    words = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", text)
    terms: list[str] = []
    for word in words:
        if "\u3400" <= word[0] <= "\u9fff":
            terms.extend(word[index : index + 2] for index in range(max(1, len(word) - 1)))
        else:
            terms.append(word)
    return tuple(terms)


def _fields(document: SearchDocument) -> tuple[tuple[str, str], ...]:
    return (("id", document.id), ("path", document.path), ("name", document.name), *document.fields)


def _projection(document: SearchDocument) -> str:
    return "\n".join(f"{label}: {text}" for label, text in _fields(document) if text)


def _lexical(
    documents: tuple[SearchDocument, ...], query: str, window: int
) -> tuple[tuple[str, float], ...]:
    query_terms = set(_terms(query))
    if not query_terms:
        return ()
    tokens = {document.id: set(_terms(_projection(document))) for document in documents}
    frequency = Counter(term for terms in tokens.values() for term in terms)
    ranked: list[tuple[str, float]] = []
    normalized = _normalized(query)
    for document in documents:
        score = 0.0
        matched: set[str] = set()
        for label, text in _fields(document):
            lowered = _normalized(text)
            field_terms = set(_terms(text))
            weight = 4.0 if label in {"id", "path", "name"} or "alias" in label else 1.0
            for term in sorted(query_terms & field_terms):
                matched.add(term)
                score += weight * math.log(1.0 + (len(documents) + 1.0) / (frequency[term] + 1.0))
            if normalized and normalized in lowered:
                score += weight * 3.0
                matched.update(query_terms)
        # Bounded spelling repair applies only to identifier tokens, not prose.
        if re.fullmatch(r"[a-zA-Z0-9_.:-]+", query):
            similarity = max(
                SequenceMatcher(None, normalized, _normalized(value)).ratio()
                for value in (document.path, document.name, document.path.rsplit(".", 1)[-1])
            )
            if similarity >= 0.78:
                score += similarity * 4.0
                matched.update(query_terms)
        coverage = len(matched) / len(query_terms)
        if score > 0.0 and coverage >= 0.4:
            ranked.append((document.id, score * coverage))
    return tuple(sorted(ranked, key=lambda item: (-item[1], item[0]))[:window])


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _atomic_json(path: Path, value: object, *, replace_existing: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".building-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if replace_existing:
            os.replace(temporary, path)
        else:
            with suppress(FileExistsError):
                # A complete result is immutable once published. Concurrent
                # first queries reuse the winner rather than replacing cursors.
                os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_json(path: Path) -> object:
    with path.open(encoding="utf-8") as stream:
        value: object = json.load(stream)
    return value


def _snippets(document: SearchDocument, query: str) -> tuple[tuple[str, str], ...]:
    terms = set(_terms(query))
    snippets: list[tuple[str, str]] = []
    for label, text in _fields(document):
        overlap = terms & set(_terms(text))
        if not overlap:
            continue
        lowered = _normalized(text)
        positions = [lowered.find(term) for term in overlap if term in lowered]
        start = max(0, min(positions, default=0) - 50)
        excerpt = text[start : start + 240]
        snippets.append(
            (label, ("…" if start else "") + excerpt + ("…" if start + 240 < len(text) else ""))
        )
        if len(snippets) == 3:
            break
    if not snippets:
        # A vector hit has no model-supplied token attribution. These are real
        # source excerpts, rather than an invented explanation of its similarity.
        snippets = [
            (label, text[:240] + ("…" if len(text) > 240 else ""))
            for label, text in document.fields[:2]
        ]
    return tuple(snippets)


def _retrieval(config: SearchConfig, query: str) -> Retrieval:
    if not query:
        return BrowseRetrieval()
    if isinstance(config, LexicalSearchConfig):
        return LexicalRetrieval(candidate_window=config.candidate_window)
    return HybridRetrieval(
        embedding_model=config.embedding_model,
        embedding_revision=config.embedding_revision,
        calibration_id=config.calibration_id,
        reranker=config.reranker,
        candidate_window=config.candidate_window,
    )


def _rank(
    documents: tuple[SearchDocument, ...],
    query: str,
    *,
    workspace_dir: Path,
    snapshot: str,
    config: SearchConfig,
) -> tuple[SearchHit, ...]:
    if not query:
        return tuple(SearchHit(document.id, 0.0, (), ()) for document in documents)
    lexical = _lexical(documents, query, config.candidate_window)
    lexical_scores = dict(lexical)
    exact = {
        document.id
        for document in documents
        if _normalized(query) in {_normalized(document.id), _normalized(document.path)}
    }
    vectors: tuple[tuple[str, float], ...] = ()
    all_vector_scores: dict[str, float] = {}
    rerank_scores: dict[str, float] = {}
    if isinstance(config, HybridSearchConfig):
        from marivo.semantic._catalog_search_model import vector_search

        scored = vector_search(
            documents, query, workspace_dir=workspace_dir, snapshot=snapshot, config=config
        )
        all_vector_scores = dict(scored)
        vectors = scored[: config.candidate_window]
    vector_scores = dict(vectors)
    fused: dict[str, float] = {}
    for channel in (lexical, vectors):
        for rank, (identity, _) in enumerate(channel, 1):
            fused[identity] = fused.get(identity, 0.0) + 1.0 / (60 + rank)
    for identity in exact:
        fused.setdefault(identity, 0.0)
    by_id = {document.id: document for document in documents}
    if (
        isinstance(config, HybridSearchConfig)
        and isinstance(config.reranker, LocalReranker)
        and fused
    ):
        from marivo.semantic._catalog_search_model import rerank_documents

        rerank_scores = rerank_documents(
            query, tuple(by_id[identity] for identity in sorted(fused)), config
        )
    hits: list[SearchHit] = []
    for identity, score in fused.items():
        if isinstance(config, HybridSearchConfig) and identity not in exact:
            # Apply the configured gate to the whole fused union. Lexical recall
            # remains useful outside the vector window, but cannot bypass the
            # calibrated semantic rejection rule merely by sharing words.
            if all_vector_scores.get(identity, -1.0) < config.min_similarity:
                continue
            if rerank_scores and rerank_scores[identity] < config.min_reranker_score:
                continue
        channels = tuple(
            channel
            for channel, scores in (("lexical", lexical_scores), ("vector", vector_scores))
            if identity in scores
        )
        hits.append(
            SearchHit(
                identity,
                rerank_scores.get(identity, score),
                channels,
                _snippets(by_id[identity], query),
            )
        )
    hits.sort(key=lambda hit: (hit.id not in exact, -hit.score, hit.id))
    return tuple(hits)


def _decode_hits(raw: object) -> tuple[SearchHit, ...]:
    if not isinstance(raw, list):
        raise ValueError("Invalid ranking records")
    hits: list[SearchHit] = []
    for value in raw:
        if not isinstance(value, dict):
            raise ValueError("Invalid ranking record")
        identity, score = value.get("id"), value.get("score")
        channels, snippets = value.get("channels"), value.get("snippets")
        if (
            not isinstance(identity, str)
            or isinstance(score, bool)
            or not isinstance(score, (float, int))
            or not math.isfinite(score)
        ):
            raise ValueError("Invalid ranking identity or score")
        if not isinstance(channels, list) or any(
            not isinstance(channel, str) for channel in channels
        ):
            raise ValueError("Invalid ranking channels")
        if not isinstance(snippets, list):
            raise ValueError("Invalid ranking snippets")
        pairs: list[tuple[str, str]] = []
        for pair in snippets:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or not isinstance(pair[1], str)
            ):
                raise ValueError("Invalid ranking snippet")
            pairs.append((pair[0], pair[1]))
        hits.append(
            SearchHit(
                identity,
                float(score),
                tuple(channel for channel in channels if isinstance(channel, str)),
                tuple(pairs),
            )
        )
    if len({hit.id for hit in hits}) != len(hits):
        raise ValueError("Duplicate ranking identity")
    return tuple(hits)


def _cursor_error(received: str) -> CatalogSearchError:
    return CatalogSearchError(
        stage="cursor",
        message="This search cursor no longer identifies the same fixed ranking.",
        expected="a cursor from the same Catalog snapshot, query, filters and frozen search configuration with an intact result cache",
        received=received,
        action="Run catalog.search with the original query and filters without cursor to start a new first page.",
    )


def _read_ranking(path: Path, request_key: str, allowed: set[str]) -> tuple[SearchHit, ...]:
    cached = _read_json(path)
    if not isinstance(cached, dict) or cached.get("key") != request_key:
        raise ValueError("Invalid ranking binding")
    hits = _decode_hits(cached.get("items"))
    if any(hit.id not in allowed for hit in hits):
        raise ValueError("Ranking is outside the filtered snapshot")
    if cached.get("digest") != _digest([asdict(hit) for hit in hits]):
        raise ValueError("Ranking digest mismatch")
    return hits


def search_documents(
    documents: tuple[SearchDocument, ...],
    *,
    workspace_dir: Path,
    snapshot: str,
    query: str,
    kind: str | None,
    domain: str | None,
    entity: str | None,
    limit: int,
    cursor: str | None,
    config: SearchConfig,
) -> SearchResult:
    """Search a captured snapshot, prefilter both channels, then paginate.

    Rankings are atomic project-local derived files. Cursors never rerun a model
    and are rejected when their binding or cached ranking changes. ``limit`` only
    controls the returned page, not recall, fusion, gating, or candidate count.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise CatalogSearchError(
            stage="arguments",
            message="Invalid search page size.",
            expected="a positive integer limit",
            received=repr(limit),
            action="Pass a positive limit to catalog.search.",
        )
    query = query.strip()
    filtered = tuple(
        sorted(
            (
                document
                for document in documents
                if (kind is None or document.kind == kind)
                and (domain is None or document.domain == domain)
                and (entity is None or document.entity == entity)
            ),
            key=lambda document: document.id,
        )
    )
    request_key = _digest(
        {
            "version": _CACHE_VERSION,
            "ranking": _RANKING_VERSION,
            "projection": _PROJECTION_VERSION,
            "snapshot": snapshot,
            "documents": [
                asdict(document) for document in sorted(documents, key=lambda document: document.id)
            ],
            "query": query,
            "kind": kind,
            "domain": domain,
            "entity": entity,
            "config": asdict(config),
        }
    )
    ranking_path = workspace_dir / ".marivo/semantic/search/results" / f"{request_key}.json"
    offset = 0
    cursor_hash: str | None = None
    if cursor is not None:
        try:
            payload: object = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            if (
                not isinstance(payload, list)
                or len(payload) != 4
                or payload[0] != _CACHE_VERSION
                or payload[1] != request_key
                or not isinstance(payload[2], str)
                or isinstance(payload[3], bool)
                or not isinstance(payload[3], int)
                or payload[3] < 1
            ):
                raise ValueError("Cursor binding mismatch")
            cursor_hash, offset = payload[2], payload[3]
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise _cursor_error("invalid or mismatched cursor") from exc
    hits: tuple[SearchHit, ...] | None = None
    invalid_cache = False
    allowed = {document.id for document in filtered}
    if ranking_path.is_file():
        try:
            hits = _read_ranking(ranking_path, request_key, allowed)
        except (ValueError, OSError):
            hits = None
            invalid_cache = True
    if hits is None:
        if cursor is not None:
            raise _cursor_error("missing or invalid result cache")
        try:
            if invalid_cache:
                # Never unlink an observed corrupt path: another process may
                # already have atomically published a valid ranking there.
                raise CatalogSearchError(
                    stage="cache",
                    message="The captured search result cache is invalid.",
                    expected="an intact immutable ranking cache",
                    received=str(ranking_path),
                    action=f"Remove the invalid derived file {ranking_path} and restart without cursor. Definitions are unaffected.",
                )
            hits = _rank(
                filtered, query, workspace_dir=workspace_dir, snapshot=snapshot, config=config
            )
            _atomic_json(
                ranking_path,
                {
                    "key": request_key,
                    "items": [asdict(hit) for hit in hits],
                    "digest": _digest([asdict(hit) for hit in hits]),
                },
                replace_existing=False,
            )
            hits = _read_ranking(ranking_path, request_key, allowed)
        except (OSError, ValueError) as exc:
            raise CatalogSearchError(
                stage="cache",
                message="Search state could not be published atomically.",
                expected="a writable project-local .marivo/semantic/search directory",
                received=str(exc),
                action="Repair project directory permissions and retry the same search.",
            ) from exc
    digest = _digest([asdict(hit) for hit in hits])
    if cursor is not None and (cursor_hash != digest or offset >= len(hits)):
        raise _cursor_error("cached ranking changed or cursor offset is outside it")
    end = min(offset + limit, len(hits))
    next_cursor = (
        base64.urlsafe_b64encode(
            json.dumps([_CACHE_VERSION, request_key, digest, end], separators=(",", ":")).encode()
        ).decode()
        if end < len(hits)
        else None
    )
    return SearchResult(hits[offset:end], len(hits), _retrieval(config, query), next_cursor)
