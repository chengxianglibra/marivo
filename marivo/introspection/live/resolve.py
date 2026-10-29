"""Generic target resolution and lexical suggestions for live help surfaces."""

from __future__ import annotations

import difflib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Generic, Literal, NoReturn, TypeVar

from marivo.introspection.live.model import (
    SURFACE_LIMITS,
    HelpSurface,
    LiveSurfaceRegistry,
    ResolvableHelpDescriptor,
)
from marivo.introspection.live.targets import canonical_target

ResolveKind = Literal[
    "descriptor",
    "type_contract",
    "error_contract",
    "attribute",
]


DescriptorT = TypeVar("DescriptorT", bound=ResolvableHelpDescriptor)


@dataclass(frozen=True)
class ResolvedLiveTarget(Generic[DescriptorT]):
    """Closed kind/payload carrier for one resolved help target."""

    kind: ResolveKind
    surface: HelpSurface
    canonical_id: str | None = None
    descriptor: DescriptorT | None = None
    type_name: str | None = None
    error_name: str | None = None
    attribute_name: str | None = None
    attribute_owner: type | None = None

    def __post_init__(self) -> None:
        """Reject every payload combination outside the documented matrix."""
        required: dict[ResolveKind, tuple[str, ...]] = {
            "attribute": ("type_name", "attribute_name", "attribute_owner"),
            "descriptor": ("canonical_id", "descriptor"),
            "type_contract": ("type_name",),
            "error_contract": ("error_name",),
        }
        payload_names = (
            "attribute_owner",
            "attribute_name",
            "canonical_id",
            "descriptor",
            "type_name",
            "error_name",
        )
        required_names = required[self.kind]
        missing = tuple(name for name in required_names if getattr(self, name) is None)
        unexpected = tuple(
            name
            for name in payload_names
            if name not in required_names and getattr(self, name) is not None
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
        if self.error_name is not None:
            return f"ResolvedLiveTarget(kind={self.kind}, error={self.error_name!r})"
        return f"ResolvedLiveTarget(kind={self.kind})"


def _default_help_target_error(target: object, suggestions: tuple[str, ...]) -> NoReturn:
    raise KeyError(target)


@dataclass(frozen=True)
class LiveSurface(Generic[DescriptorT]):
    """Immutable bundle of surface-owned inputs to neutral resolution."""

    registry: LiveSurfaceRegistry[DescriptorT]
    type_index: Mapping[type, str]
    error_types: Mapping[str, type]
    error_base: type
    default_suggestions: tuple[str, ...] = ()
    help_target_error: Callable[[object, tuple[str, ...]], NoReturn] = _default_help_target_error
    enrich: Callable[[object], ResolvedLiveTarget[DescriptorT] | None] | None = None
    string_target_index: Mapping[str, str] | None = None
    suggestion_index: LiveSuggestionIndex | None = None
    descriptor_kind: Callable[[DescriptorT], str] | None = None
    attribute_applicability: Mapping[type, str] = field(default_factory=dict)
    type_targets: Mapping[type, str] = field(default_factory=dict)


def resolve_live_target(
    target: object,
    surface: LiveSurface[DescriptorT],
) -> ResolvedLiveTarget[DescriptorT]:
    """Resolve an allowlisted target without invoking domain behavior."""
    if isinstance(target, str):
        return _resolve_string(target, surface)
    if not isinstance(target, type) and not callable(target):
        _raise(surface, target)
    if surface.enrich is not None:
        enriched = surface.enrich(target)
        if enriched is not None:
            return enriched
    if isinstance(target, type):
        return _resolve_type(target, surface)
    if callable(target):
        return _resolve_callable(target, surface)
    _raise(surface, target)


def _resolved_descriptor(
    descriptor: DescriptorT,
    surface: LiveSurface[DescriptorT],
) -> ResolvedLiveTarget[DescriptorT]:
    return ResolvedLiveTarget(
        kind="descriptor",
        surface=surface.registry.surface,
        canonical_id=descriptor.canonical_id,
        descriptor=descriptor,
    )


def _resolve_string(
    target: str,
    surface: LiveSurface[DescriptorT],
) -> ResolvedLiveTarget[DescriptorT]:
    resolved = try_resolve_live_string_target(target, surface)
    if resolved is not None:
        return resolved
    _raise(surface, target)


def try_resolve_live_string_target(
    target: str,
    surface: LiveSurface[DescriptorT],
) -> ResolvedLiveTarget[DescriptorT] | None:
    """Resolve one exact string target without computing repair suggestions."""
    if not target:
        return None
    for candidate in _help_target_candidates(target):
        try:
            return _resolved_descriptor(surface.registry.by_canonical_id(candidate), surface)
        except KeyError:
            pass
        if surface.string_target_index is not None:
            canonical_id = surface.string_target_index.get(candidate)
            if canonical_id is not None:
                return _resolved_descriptor(
                    surface.registry.by_canonical_id(canonical_id),
                    surface,
                )
        for type_obj, type_name in surface.type_index.items():
            if surface.type_targets.get(type_obj, canonical_target(type_name)) == candidate:
                return ResolvedLiveTarget(
                    kind="type_contract",
                    surface=surface.registry.surface,
                    type_name=type_name,
                )
        error_type = next(
            (
                value
                for name, value in surface.error_types.items()
                if canonical_target(name) == candidate
            ),
            None,
        )
        if error_type is not None:
            return ResolvedLiveTarget(
                kind="error_contract",
                surface=surface.registry.surface,
                error_name=error_type.__name__,
            )
        for type_obj, type_name in surface.type_index.items():
            parent, separator, member = candidate.rpartition(".")
            if (
                separator
                and surface.type_targets.get(type_obj, canonical_target(type_name)) == parent
            ):
                from marivo.introspection.live.attributes import public_attributes

                if member in public_attributes(type_obj):
                    return ResolvedLiveTarget(
                        kind="attribute",
                        surface=surface.registry.surface,
                        type_name=type_name,
                        attribute_name=member,
                        attribute_owner=type_obj,
                    )
    return None


def _normalize_help_target(target: str) -> str:
    """Retain exact input; public targets have no implicit aliases."""
    return target


def _help_target_candidates(target: str) -> tuple[str, ...]:
    return (target,)


def build_string_target_index(
    registry: LiveSurfaceRegistry[DescriptorT],
    *,
    public_type_names: frozenset[str],
) -> Mapping[str, str]:
    """Index exact owner-declared canonical targets only."""
    return MappingProxyType({target: target for target in registry.canonical_ids()})


def _resolve_callable(
    target: object,
    surface: LiveSurface[DescriptorT],
) -> ResolvedLiveTarget[DescriptorT]:
    try:
        return _resolved_descriptor(surface.registry.by_callable(target), surface)
    except KeyError:
        _raise(surface, target)


def _resolve_type(
    target: type,
    surface: LiveSurface[DescriptorT],
) -> ResolvedLiveTarget[DescriptorT]:
    if issubclass(target, surface.error_base):
        # Private failure subclasses share their nearest registered error
        # contract. Never advertise a new string target just from a class name.
        error_name = next(
            (
                name
                for base in target.__mro__
                for name, registered in surface.error_types.items()
                if base is registered
            ),
            None,
        )
        if error_name is None:
            _raise(surface, target)
        return ResolvedLiveTarget(
            kind="error_contract",
            surface=surface.registry.surface,
            error_name=error_name,
        )
    # Constructor classes are registered as descriptors so their focused help
    # includes the live callable contract. Prefer that descriptor over a
    # generic type contract, keeping ``marivo.help(SomeType)`` identical to
    # ``marivo.help("SomeType")`` when both are public entry points.
    try:
        return _resolved_descriptor(surface.registry.by_callable(target), surface)
    except KeyError:
        pass
    type_name = surface.type_index.get(target)
    if type_name is not None:
        if target in surface.type_targets:
            return ResolvedLiveTarget(
                kind="type_contract",
                surface=surface.registry.surface,
                type_name=type_name,
            )
        try:
            return _resolved_descriptor(
                surface.registry.by_canonical_id(canonical_target(type_name)), surface
            )
        except KeyError:
            pass
        return ResolvedLiveTarget(
            kind="type_contract",
            surface=surface.registry.surface,
            type_name=type_name,
        )
    _raise(surface, target)


def _raise(surface: LiveSurface[DescriptorT], target: object) -> NoReturn:
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


def build_suggestion_index(
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
    # A typo within an exact namespace should lead to that namespace's closest
    # leaf before a shorter family/root name that shares more generic tokens.
    parent, separator, leaf = query.rpartition(".")
    if separator:
        scored = [
            (
                score
                + (1000.0 if parent == target.rpartition(".")[0] else 0.0)
                + (
                    200.0 * ratio
                    if parent == target.rpartition(".")[0]
                    and (
                        ratio := difflib.SequenceMatcher(
                            None, leaf, target.rpartition(".")[2]
                        ).ratio()
                    )
                    >= 0.7
                    else 0.0
                ),
                target,
            )
            for score, target in scored
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
        if normalized_query == normalized_target:
            # An exact canonical-id match always outranks fuzzy/prefix matches.
            score += 1000.0
        elif normalized_query in normalized_target:
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
