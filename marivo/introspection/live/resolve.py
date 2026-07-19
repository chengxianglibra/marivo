"""Generic target resolution and lexical suggestions for live help surfaces."""

from __future__ import annotations

import difflib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, NoReturn

from marivo.introspection.live.model import (
    SURFACE_LIMITS,
    HelpSurface,
    LiveSurfaceRegistry,
    ResolvableHelpDescriptor,
)

ResolveKind = Literal[
    "descriptor",
    "type_contract",
    "reference_briefing",
    "error_contract",
    "error_briefing",
]


@dataclass(frozen=True)
class ResolvedLiveTarget[DescriptorT: ResolvableHelpDescriptor]:
    """Closed kind/payload carrier for one resolved help target."""

    kind: ResolveKind
    surface: HelpSurface
    canonical_id: str | None = None
    descriptor: DescriptorT | None = None
    type_name: str | None = None
    reference_id: str | None = None
    error_name: str | None = None
    error_kind: str | None = None
    original: object | None = None

    def __post_init__(self) -> None:
        """Reject every payload combination outside the documented matrix."""
        required: dict[ResolveKind, tuple[str, ...]] = {
            "descriptor": ("canonical_id", "descriptor"),
            "type_contract": ("type_name",),
            "reference_briefing": ("reference_id", "original"),
            "error_contract": ("error_name",),
            "error_briefing": ("error_name", "original"),
        }
        optional: dict[ResolveKind, tuple[str, ...]] = {
            "error_briefing": ("error_kind",),
        }
        payload_names = (
            "canonical_id",
            "descriptor",
            "type_name",
            "reference_id",
            "error_name",
            "error_kind",
            "original",
        )
        required_names = required[self.kind]
        optional_names = optional.get(self.kind, ())
        missing = tuple(name for name in required_names if getattr(self, name) is None)
        unexpected = tuple(
            name
            for name in payload_names
            if name not in required_names
            and name not in optional_names
            and getattr(self, name) is not None
        )
        if missing or unexpected:
            raise ValueError(
                f"invalid {self.kind} payload: missing={missing!r}, unexpected={unexpected!r}"
            )

    def __repr__(self) -> str:
        if self.descriptor is not None:
            return f"ResolvedLiveTarget(kind={self.kind}, target={self.canonical_id!r})"
        if self.type_name is not None:
            return f"ResolvedLiveTarget(kind={self.kind}, type={self.type_name!r})"
        if self.reference_id is not None:
            return f"ResolvedLiveTarget(kind={self.kind}, ref={self.reference_id!r})"
        if self.error_name is not None:
            return f"ResolvedLiveTarget(kind={self.kind}, error={self.error_name!r})"
        return f"ResolvedLiveTarget(kind={self.kind})"


def _default_help_target_error(target: object, suggestions: tuple[str, ...]) -> NoReturn:
    raise KeyError(target)


@dataclass(frozen=True)
class LiveSurface[DescriptorT: ResolvableHelpDescriptor]:
    """Immutable bundle of surface-owned inputs to neutral resolution."""

    registry: LiveSurfaceRegistry[DescriptorT]
    type_index: Mapping[type, str]
    error_types: Mapping[str, type]
    error_base: type
    default_suggestions: tuple[str, ...] = ()
    help_target_error: Callable[[object, tuple[str, ...]], NoReturn] = _default_help_target_error
    enrich: Callable[[object], ResolvedLiveTarget[DescriptorT] | None] | None = None
    suggestion_index: LiveSuggestionIndex | None = None


def resolve_live_target[DescriptorT: ResolvableHelpDescriptor](
    target: object,
    surface: LiveSurface[DescriptorT],
) -> ResolvedLiveTarget[DescriptorT]:
    """Resolve an allowlisted target without invoking domain behavior."""
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


def _resolved_descriptor[DescriptorT: ResolvableHelpDescriptor](
    descriptor: DescriptorT,
    surface: LiveSurface[DescriptorT],
) -> ResolvedLiveTarget[DescriptorT]:
    return ResolvedLiveTarget(
        kind="descriptor",
        surface=surface.registry.surface,
        canonical_id=descriptor.canonical_id,
        descriptor=descriptor,
    )


def _resolve_string[DescriptorT: ResolvableHelpDescriptor](
    target: str,
    surface: LiveSurface[DescriptorT],
) -> ResolvedLiveTarget[DescriptorT]:
    if not target:
        _raise(surface, target)
    for candidate in _help_target_candidates(target):
        try:
            return _resolved_descriptor(surface.registry.by_canonical_id(candidate), surface)
        except KeyError:
            pass
        for type_name in surface.type_index.values():
            if type_name == candidate:
                return ResolvedLiveTarget(
                    kind="type_contract",
                    surface=surface.registry.surface,
                    type_name=type_name,
                )
        bare = candidate.rsplit(".", 1)[-1]
        error_type = surface.error_types.get(bare) or surface.error_types.get(candidate)
        if error_type is not None:
            return ResolvedLiveTarget(
                kind="error_contract",
                surface=surface.registry.surface,
                error_name=error_type.__name__,
            )
    _raise(surface, target)


# Prefixes users paste from help output / CLI invocations. Canonical ids never
# start with these, so stripping them is safe and lets
# ``mv.help("mv.session.get_or_create")`` or
# ``mv.help("analysis mv.session.get_or_create")`` resolve. See issue #32.
_HELP_TARGET_PREFIXES = (
    "analysis ",
    "semantic ",
    "datasource ",
    "mv.",
    "ms.",
    "md.",
)


def _normalize_help_target(target: str) -> str:
    normalized = target.strip()
    changed = True
    while changed:
        changed = False
        for prefix in _HELP_TARGET_PREFIXES:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :].strip()
                changed = True
                break
    return normalized


def _help_target_candidates(target: str) -> tuple[str, ...]:
    """Yield the exact target first, then the alias/name-stripped form."""
    normalized = _normalize_help_target(target)
    if normalized and normalized != target:
        return (target, normalized)
    return (target,)


def _resolve_callable[DescriptorT: ResolvableHelpDescriptor](
    target: object,
    surface: LiveSurface[DescriptorT],
) -> ResolvedLiveTarget[DescriptorT]:
    try:
        return _resolved_descriptor(surface.registry.by_callable(target), surface)
    except KeyError:
        _raise(surface, target)


def _resolve_type[DescriptorT: ResolvableHelpDescriptor](
    target: type,
    surface: LiveSurface[DescriptorT],
) -> ResolvedLiveTarget[DescriptorT]:
    if issubclass(target, surface.error_base):
        return ResolvedLiveTarget(
            kind="error_contract",
            surface=surface.registry.surface,
            error_name=target.__name__,
        )
    type_name = surface.type_index.get(target)
    if type_name is not None:
        return ResolvedLiveTarget(
            kind="type_contract",
            surface=surface.registry.surface,
            type_name=type_name,
        )
    _raise(surface, target)


def _resolve_object[DescriptorT: ResolvableHelpDescriptor](
    target: object,
    surface: LiveSurface[DescriptorT],
) -> ResolvedLiveTarget[DescriptorT]:
    type_name = surface.type_index.get(type(target))
    if type_name is not None:
        return ResolvedLiveTarget(
            kind="type_contract",
            surface=surface.registry.surface,
            type_name=type_name,
        )
    _raise(surface, target)


def _raise[DescriptorT: ResolvableHelpDescriptor](
    surface: LiveSurface[DescriptorT], target: object
) -> NoReturn:
    query = target if isinstance(target, str) else type(target).__name__
    suggestions: tuple[str, ...] = ()
    if surface.suggestion_index is not None:
        suggestions = suggestions_for(query, surface.suggestion_index)
    if not suggestions:
        suggestions = surface.default_suggestions
    surface.help_target_error(target, suggestions)


@dataclass(frozen=True)
class LiveSuggestionIndex:
    """Immutable lexical suggestion index over native descriptors."""

    _all_targets: tuple[str, ...]
    _target_tokens: MappingProxyType[str, frozenset[str]]
    _normalized_targets: MappingProxyType[str, str]


def _normalize_token(text: str) -> str:
    return "".join(character.lower() for character in text if character.isalnum())


def _tokenize(text: str) -> frozenset[str]:
    tokens: set[str] = set()
    current: list[str] = []
    for character in text:
        if character.isalnum():
            current.append(character)
        elif current:
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


def build_suggestion_index[DescriptorT: ResolvableHelpDescriptor](
    registry: LiveSurfaceRegistry[DescriptorT],
) -> LiveSuggestionIndex:
    """Build a deterministic suggestion index without copying descriptors."""
    target_tokens: dict[str, frozenset[str]] = {}
    normalized_targets: dict[str, str] = {}
    all_targets = registry.canonical_ids()
    for target in all_targets:
        descriptor = registry.by_canonical_id(target)
        texts = (
            descriptor.canonical_id,
            descriptor.public_entrypoint or "",
            descriptor.summary,
        )
        tokens: set[str] = set()
        for text in texts:
            tokens.update(_tokenize(text))
        target_tokens[target] = frozenset(tokens)
        normalized_targets[target] = _normalize_token(target)
    return LiveSuggestionIndex(
        _all_targets=all_targets,
        _target_tokens=MappingProxyType(target_tokens),
        _normalized_targets=MappingProxyType(normalized_targets),
    )


def suggestions_for(query: str, index: LiveSuggestionIndex) -> tuple[str, ...]:
    """Return deterministic bounded suggestions for a lexical query."""
    if not query or not query.strip():
        return ()
    query_tokens = _tokenize(query)
    normalized_query = _normalize_token(query)
    if not query_tokens and not normalized_query:
        return ()
    scored = [
        (score, target)
        for target in index._all_targets
        if (score := _score_target(target, query_tokens, normalized_query, index)) > 0.0
    ]
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return tuple(target for _, target in scored[: SURFACE_LIMITS.help_suggestion_limit])


def _score_target(
    target: str,
    query_tokens: frozenset[str],
    normalized_query: str,
    index: LiveSuggestionIndex,
) -> float:
    target_tokens = index._target_tokens[target]
    normalized_target = index._normalized_targets[target]
    score = 100.0 * len(query_tokens & target_tokens)
    if normalized_query and len(normalized_query) >= 3:
        ratios = (
            difflib.SequenceMatcher(None, normalized_query, token).ratio()
            for token in target_tokens
            if len(token) >= 3 and token != normalized_query
        )
        best_ratio = max(ratios, default=0.0)
        if best_ratio >= 0.7:
            score += best_ratio * 100.0
    if normalized_query and normalized_target:
        if normalized_query in normalized_target:
            position = normalized_target.find(normalized_query)
            score += 50.0 + max(0.0, 50.0 - position * 2.0)
        elif normalized_target in normalized_query:
            score += 30.0
    overlap = query_tokens & target_tokens
    if overlap:
        score += 20.0 * len(overlap) / len(query_tokens | target_tokens)
    if score == 0.0 and normalized_query and normalized_target:
        full_ratio = difflib.SequenceMatcher(None, normalized_query, normalized_target).ratio()
        if full_ratio > 0.6:
            score += full_ratio * 10.0
    return score
