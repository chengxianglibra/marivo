"""Surface-parameterized live help target resolution and lexical suggestions.

Generalized from ``marivo/analysis/_capabilities/resolve.py`` so the datasource
and semantic surfaces can share one resolver without importing analysis. A
:class:`LiveSurface` bundles a registry plus the surface-specific type index,
error catalog, and live-enrichment hook; the resolver dispatches uniformly.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, NoReturn

from marivo.introspection.live.model import (
    SURFACE_LIMITS,
    HelpSurface,
    LiveCapability,
    LiveSurfaceRegistry,
)

ResolveKind = Literal[
    "descriptor",
    "type_contract",
    "semantic_briefing",
    "error_contract",
    "error_briefing",
]


@dataclass(frozen=True)
class ResolvedLiveTarget:
    """A resolved help target carrying the descriptor or briefing payload.

    Exactly one of ``descriptor``, ``type_name``, ``semantic_id``, or
    ``error_name`` is meaningful depending on ``kind``.
    """

    kind: ResolveKind
    surface: HelpSurface
    canonical_id: str | None = None
    descriptor: LiveCapability | None = None
    type_name: str | None = None
    semantic_id: str | None = None
    error_name: str | None = None
    error_kind: str | None = None
    original: object | None = None


def _default_help_target_error(target: object, suggestions: tuple[str, ...]) -> NoReturn:
    """Default help-target error: raise ``KeyError`` if a surface forgets to set one."""
    raise KeyError(target)


@dataclass(frozen=True)
class LiveSurface:
    """Bundle of surface-specific resolution inputs.

    Parameters
    ----------
    registry:
        The surface's capability registry.
    type_index:
        Mapping of registered public types to their display names.
    error_types:
        Mapping of error class names to error classes, for string error
        resolution.
    error_base:
        The surface's error base class; subclasses resolve as error contracts.
    default_suggestions:
        Fallback suggestions when no lexical match is found.
    help_target_error:
        Surface-owned error constructor called on resolution failure. Receives
        the original target and bounded lexical suggestions.
    enrich:
        Optional surface-specific live-enrichment hook for runtime objects
        (error instances, semantic refs/objects, datasource specs/inspections/
        snapshots). Returns ``None`` to defer to generic dispatch.
    suggestion_index:
        Prebuilt lexical suggestion index for the registry.
    """

    registry: LiveSurfaceRegistry
    type_index: Mapping[type, str]
    error_types: Mapping[str, type]
    error_base: type
    default_suggestions: tuple[str, ...] = ()
    help_target_error: Callable[[object, tuple[str, ...]], NoReturn] = _default_help_target_error
    enrich: Callable[[object], ResolvedLiveTarget | None] | None = None
    suggestion_index: LiveSuggestionIndex | None = None


def resolve_live_target(target: object, surface: LiveSurface) -> ResolvedLiveTarget:
    """Resolve a help target to a typed :class:`ResolvedLiveTarget`.

    Accepts canonical strings, registered callables/types, public runtime
    objects, and surface-owned errors. Rejects every other value by invoking
    ``surface.help_target_error`` (which raises a surface-owned typed error).
    """
    if isinstance(target, str):
        return _resolve_string(target, surface)

    if surface.enrich is not None:
        enriched = surface.enrich(target)
        if enriched is not None:
            return enriched

    if isinstance(target, type):
        return _resolve_type(target, surface)

    if callable(target):
        return _resolve_callable(target, surface)

    return _resolve_object(target, surface)


def _resolve_string(target: str, surface: LiveSurface) -> ResolvedLiveTarget:
    if not target:
        _raise(surface, target)

    try:
        desc = surface.registry.by_canonical_id(target)
        return ResolvedLiveTarget(
            kind="descriptor",
            surface=surface.registry.surface,
            canonical_id=desc.canonical_id,
            descriptor=desc,
        )
    except KeyError:
        pass

    for type_name in surface.type_index.values():
        if type_name == target:
            return ResolvedLiveTarget(
                kind="type_contract",
                surface=surface.registry.surface,
                type_name=type_name,
            )

    bare = target.rsplit(".", 1)[-1] if "." in target else target
    cls = surface.error_types.get(bare) or surface.error_types.get(target)
    if cls is not None:
        return ResolvedLiveTarget(
            kind="error_contract",
            surface=surface.registry.surface,
            error_name=cls.__name__,
        )

    _raise(surface, target)
    raise AssertionError("unreachable")  # for type checkers


def _resolve_callable(target: object, surface: LiveSurface) -> ResolvedLiveTarget:
    try:
        desc = surface.registry.by_callable(target)
        return ResolvedLiveTarget(
            kind="descriptor",
            surface=surface.registry.surface,
            canonical_id=desc.canonical_id,
            descriptor=desc,
        )
    except KeyError:
        pass
    _raise(surface, target)
    raise AssertionError("unreachable")


def _resolve_type(target: type, surface: LiveSurface) -> ResolvedLiveTarget:
    if issubclass(target, surface.error_base):
        return ResolvedLiveTarget(
            kind="error_contract",
            surface=surface.registry.surface,
            error_name=target.__name__,
        )
    name = surface.type_index.get(target)
    if name is not None:
        return ResolvedLiveTarget(
            kind="type_contract",
            surface=surface.registry.surface,
            type_name=name,
        )
    _raise(surface, target)
    raise AssertionError("unreachable")


def _resolve_object(target: object, surface: LiveSurface) -> ResolvedLiveTarget:
    name = surface.type_index.get(type(target))
    if name is not None:
        return ResolvedLiveTarget(
            kind="type_contract",
            surface=surface.registry.surface,
            type_name=name,
            original=target,
        )
    _raise(surface, target)
    raise AssertionError("unreachable")


def _raise(surface: LiveSurface, target: object) -> NoReturn:
    query = target if isinstance(target, str) else type(target).__name__
    suggestions: tuple[str, ...] = ()
    if surface.suggestion_index is not None:
        suggestions = suggestions_for(query, surface.suggestion_index)
    if not suggestions:
        suggestions = surface.default_suggestions
    surface.help_target_error(target, suggestions)


# ---------------------------------------------------------------------------
# Lexical suggestion index
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveSuggestionIndex:
    """Immutable lexical suggestion index over a capability registry."""

    _all_targets: tuple[str, ...]
    _target_tokens: MappingProxyType[str, frozenset[str]]
    _normalized_targets: MappingProxyType[str, str]


def _normalize_token(text: str) -> str:
    return "".join(c.lower() for c in text if c.isalnum())


def _tokenize(text: str) -> frozenset[str]:
    tokens: set[str] = set()
    current: list[str] = []
    for ch in text:
        if ch.isalnum():
            current.append(ch)
        else:
            if current:
                token = _normalize_token("".join(current))
                if token:
                    tokens.add(token)
                current = []
    if current:
        token = _normalize_token("".join(current))
        if token:
            tokens.add(token)
    full = _normalize_token(text)
    if full:
        tokens.add(full)
    return frozenset(tokens)


def build_suggestion_index(registry: LiveSurfaceRegistry) -> LiveSuggestionIndex:
    """Build the immutable lexical suggestion index for a registry."""
    target_tokens: dict[str, frozenset[str]] = {}
    normalized_targets: dict[str, str] = {}
    all_targets = registry.canonical_ids()
    for target in all_targets:
        desc = registry.by_canonical_id(target)
        texts = [
            desc.canonical_id,
            desc.public_entrypoint or "",
            desc.summary,
        ]
        all_tokens: set[str] = set()
        for text in texts:
            all_tokens.update(_tokenize(text))
        target_tokens[target] = frozenset(all_tokens)
        normalized_targets[target] = _normalize_token(target)
    return LiveSuggestionIndex(
        _all_targets=all_targets,
        _target_tokens=MappingProxyType(target_tokens),
        _normalized_targets=MappingProxyType(normalized_targets),
    )


def suggestions_for(query: str, index: LiveSuggestionIndex) -> tuple[str, ...]:
    """Return ranked canonical target strings for a lexical query.

    Ranking (highest first): exact token match, token-level fuzzy match,
    normalized substring match, token overlap, full-string fuzzy fallback.
    Ties break by canonical id ascending. Bounded by
    :data:`SURFACE_LIMITS.help_suggestion_limit`.
    """
    if not query or not query.strip():
        return ()
    query_tokens = _tokenize(query)
    normalized_query = _normalize_token(query)
    if not query_tokens and not normalized_query:
        return ()

    scored: list[tuple[float, str]] = []
    for target in index._all_targets:
        score = _score_target(target, query_tokens, normalized_query, index)
        if score > 0.0:
            scored.append((score, target))

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    limit = SURFACE_LIMITS.help_suggestion_limit
    return tuple(target for _, target in scored[:limit])


def _score_target(
    target: str,
    query_tokens: frozenset[str],
    normalized_query: str,
    index: LiveSuggestionIndex,
) -> float:
    """Compute a lexical relevance score for a single target."""
    target_tokens = index._target_tokens[target]
    normalized_target = index._normalized_targets[target]
    score = 0.0

    exact_matches = query_tokens & target_tokens
    if exact_matches:
        score += 100.0 * len(exact_matches)

    if normalized_query and len(normalized_query) >= 3:
        best_token_ratio = 0.0
        for t_token in target_tokens:
            if len(t_token) < 3 or normalized_query == t_token:
                continue
            ratio = difflib.SequenceMatcher(None, normalized_query, t_token).ratio()
            if ratio > best_token_ratio:
                best_token_ratio = ratio
        if best_token_ratio >= 0.7:
            score += best_token_ratio * 100.0

    if normalized_query and normalized_target:
        if normalized_query in normalized_target:
            idx = normalized_target.find(normalized_query)
            position_bonus = max(0.0, 50.0 - idx * 2.0)
            score += 50.0 + position_bonus
        elif normalized_target in normalized_query:
            score += 30.0

    if query_tokens and target_tokens:
        overlap = query_tokens & target_tokens
        if overlap:
            union = query_tokens | target_tokens
            overlap_ratio = len(overlap) / len(union) if union else 0.0
            score += 20.0 * overlap_ratio

    if score == 0.0 and normalized_query and normalized_target:
        ratio_full = difflib.SequenceMatcher(None, normalized_query, normalized_target).ratio()
        if ratio_full > 0.6:
            score += ratio_full * 10.0

    return score
