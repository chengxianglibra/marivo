"""Snapshot-local lexical and optional hybrid Catalog retrieval.

This module consumes captured documents only. It never imports Catalog, opens a
business datasource, downloads a model, or changes a semantic definition."""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from collections import Counter
from dataclasses import asdict
from difflib import SequenceMatcher
from pathlib import Path

from marivo.semantic.errors import CatalogSearchError
from marivo.semantic.search import sentence_transformers_backend
from marivo.semantic.search.cache import (
    _atomic_json,
    _digest,
    _read_json,
)
from marivo.semantic.search.documents import (
    _PROJECTION_VERSION,
    _fields,
    _normalized,
    _projection,
    _terms,
    exact_identity_matches,
)
from marivo.semantic.search.models import (
    _RANKING_VERSION,
    BrowseRetrieval,
    HybridRetrieval,
    HybridSearchConfig,
    LexicalRetrieval,
    LexicalSearchConfig,
    LocalReranker,
    Retrieval,
    SearchConfig,
    SearchDocument,
    SearchHit,
    SearchResult,
)

_CACHE_VERSION = 1


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


def rank_documents(
    documents: tuple[SearchDocument, ...],
    query: str,
    *,
    workspace_dir: Path,
    snapshot: str,
    config: SearchConfig,
) -> tuple[SearchHit, ...]:
    """Rank selected documents without reading or writing the result cache."""
    if not query:
        return tuple(SearchHit(document.id, 0.0, (), ()) for document in documents)
    lexical = _lexical(documents, query, config.candidate_window)
    lexical_scores = dict(lexical)
    exact = exact_identity_matches(documents, query)
    vectors: tuple[tuple[str, float], ...] = ()
    all_vector_scores: dict[str, float] = {}
    rerank_scores: dict[str, float] = {}
    if isinstance(config, HybridSearchConfig):
        scored = sentence_transformers_backend.vector_search(
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
        rerank_scores = sentence_transformers_backend.rerank_documents(
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


def _ranking_key(
    documents: tuple[SearchDocument, ...],
    *,
    snapshot: str,
    query: str,
    kind: str | None,
    domain: str | None,
    entity: str | None,
    config: SearchConfig,
) -> str:
    return _digest(
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


def _read_cursor(cursor: str | None, request_key: str) -> tuple[str | None, int]:
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
    return cursor_hash, offset


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
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:  # type: ignore[redundant-expr]  # Preserve validation for untyped inputs.
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
    request_key = _ranking_key(
        documents,
        snapshot=snapshot,
        query=query,
        kind=kind,
        domain=domain,
        entity=entity,
        config=config,
    )
    ranking_path = workspace_dir / ".marivo/semantic/search/results" / f"{request_key}.json"
    cursor_hash, offset = _read_cursor(cursor, request_key)
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
            hits = rank_documents(
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
