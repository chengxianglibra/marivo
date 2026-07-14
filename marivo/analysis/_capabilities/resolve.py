"""Canonical help target resolution and lexical suggestions.

Consumes the immutable :data:`REGISTRY` and :data:`SURFACE_LIMITS` to resolve
``mv.help(target)`` arguments into typed :class:`ResolvedHelpTarget` values
and produce deterministic bounded suggestions for invalid targets.

Resolution is explicit: no arbitrary attribute walking, no fuzzy alias
matching, no popularity or artifact-state heuristics.  Every accepted target
is either a registered canonical string, a registered callable/type, or a
live-enrichment allowlist member (semantic object/ref or AnalysisError).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from marivo.analysis._capabilities.model import (
    SURFACE_LIMITS,
    CapabilityDescriptor,
)
from marivo.analysis._capabilities.registry import (
    REGISTRY,
)
from marivo.analysis.errors import AnalysisError, HelpTargetError

# ---------------------------------------------------------------------------
# Registered public types (exact identity match)
# ---------------------------------------------------------------------------

# Late imports kept inside the mapping builder to avoid circular import
# issues at module load time (frames import errors, etc.).


def _build_type_registry() -> MappingProxyType[type, str]:
    """Build the immutable type-to-name index for public analysis types."""
    from marivo.analysis.frames.association import AssociationResult
    from marivo.analysis.frames.attribution import AttributionFrame
    from marivo.analysis.frames.base import BaseFrame
    from marivo.analysis.frames.candidate import CandidateSet
    from marivo.analysis.frames.component import ComponentFrame
    from marivo.analysis.frames.coverage import CoverageFrame
    from marivo.analysis.frames.delta import DeltaFrame
    from marivo.analysis.frames.forecast import ForecastFrame
    from marivo.analysis.frames.hypothesis import HypothesisTestResult
    from marivo.analysis.frames.metric import MetricFrame
    from marivo.analysis.frames.quality import QualityReport
    from marivo.analysis.session.core import Session

    return MappingProxyType(
        {
            Session: "Session",
            BaseFrame: "BaseFrame",
            MetricFrame: "MetricFrame",
            DeltaFrame: "DeltaFrame",
            AttributionFrame: "AttributionFrame",
            CandidateSet: "CandidateSet",
            ForecastFrame: "ForecastFrame",
            QualityReport: "QualityReport",
            HypothesisTestResult: "HypothesisTestResult",
            AssociationResult: "AssociationResult",
            ComponentFrame: "ComponentFrame",
            CoverageFrame: "CoverageFrame",
        }
    )


_TYPE_REGISTRY: MappingProxyType[type, str] = _build_type_registry()


# ---------------------------------------------------------------------------
# ResolvedHelpTarget union
# ---------------------------------------------------------------------------

ResolveKind = Literal[
    "descriptor",
    "type_contract",
    "semantic_briefing",
    "error_contract",
    "error_briefing",
]


@dataclass(frozen=True)
class ResolvedHelpTarget:
    """A resolved help target carrying the descriptor or briefing payload.

    Exactly one of ``descriptor``, ``type_name``, ``semantic_id``, or
    ``error_name`` is non-None depending on ``kind``.

    Parameters
    ----------
    kind:
        Closed resolution kind.
    descriptor:
        The registry descriptor (for ``kind == "descriptor"``).
    type_name:
        Registered public type name (for ``kind == "type_contract"``).
    semantic_id:
        Semantic ref id string (for ``kind == "semantic_briefing"``).
    error_name:
        Error class name without ``Error`` suffix (for ``error_contract``
        and ``error_briefing``).
    error_kind:
        The ``AnalysisError.kind`` property value for error instances.
    """

    kind: ResolveKind
    descriptor: CapabilityDescriptor | None = None
    type_name: str | None = None
    semantic_id: str | None = None
    error_name: str | None = None
    error_kind: str | None = None

    def __repr__(self) -> str:
        if self.descriptor is not None:
            return f"ResolvedHelpTarget(kind={self.kind}, target={self.descriptor.help_target!r})"
        if self.type_name is not None:
            return f"ResolvedHelpTarget(kind={self.kind}, type={self.type_name!r})"
        if self.semantic_id is not None:
            return f"ResolvedHelpTarget(kind={self.kind}, ref={self.semantic_id!r})"
        if self.error_name is not None:
            return f"ResolvedHelpTarget(kind={self.kind}, error={self.error_name!r})"
        return f"ResolvedHelpTarget(kind={self.kind})"


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_help_target(target: object) -> ResolvedHelpTarget:
    """Resolve a help target to a typed :class:`ResolvedHelpTarget`.

    Accepts canonical strings, registered callables/types, public analysis
    objects, semantic objects/refs, and AnalysisError subclasses/instances.
    Rejects every other object with :class:`HelpTargetError`.

    Parameters
    ----------
    target:
        Canonical target string, callable, type, runtime object, semantic
        object/ref, or AnalysisError subclass/instance.

    Returns
    -------
    ResolvedHelpTarget
        Typed resolution result.

    Raises
    ------
    HelpTargetError
        If ``target`` is not a registered canonical target.
    """
    # -- Canonical strings ------------------------------------------------
    if isinstance(target, str):
        return _resolve_string(target)

    # -- AnalysisError instances (before generic type check) -------------
    if isinstance(target, AnalysisError):
        return ResolvedHelpTarget(
            kind="error_briefing",
            error_name=type(target).__name__,
            error_kind=target.kind,
        )

    # -- Semantic objects/refs (live-enrichment allowlist) ---------------
    # Checked before callable() because SemanticRef subclasses are callable.
    from marivo.refs import SemanticRef

    if isinstance(target, SemanticRef):
        return ResolvedHelpTarget(
            kind="semantic_briefing",
            semantic_id=target.id,
        )

    from marivo.semantic.catalog import CatalogObject

    if isinstance(target, CatalogObject):
        return ResolvedHelpTarget(
            kind="semantic_briefing",
            semantic_id=target.id,
        )

    # -- Callables (functions, methods) ----------------------------------
    if callable(target):
        return _resolve_callable(target)

    # -- Types ------------------------------------------------------------
    if isinstance(target, type):
        return _resolve_type(target)

    # -- Runtime objects (registered type instances) ----------------------
    return _resolve_object(target)


def _resolve_string(target: str) -> ResolvedHelpTarget:
    """Resolve a canonical string target."""
    if not target:
        raise _help_target_error(target)

    # Try help_target first (canonical grammar for mv.help).
    try:
        desc = REGISTRY.by_help_target(target)
        return ResolvedHelpTarget(kind="descriptor", descriptor=desc)
    except KeyError:
        pass

    # Try capability id.
    try:
        desc = REGISTRY.by_id(target)
        return ResolvedHelpTarget(kind="descriptor", descriptor=desc)
    except KeyError:
        pass

    # Try matching a registered public type name.
    for _type_obj, type_name in _TYPE_REGISTRY.items():
        if type_name == target:
            return ResolvedHelpTarget(kind="type_contract", type_name=type_name)

    # Try matching an AnalysisError subclass name.
    from marivo.analysis.errors import AnalysisError

    if target.endswith("Error"):
        # Search all AnalysisError subclasses.
        import inspect

        for _, cls in inspect.getmembers(
            __import__("marivo.analysis.errors", fromlist=["errors"]),
            inspect.isclass,
        ):
            if issubclass(cls, AnalysisError) and cls.__name__ == target:
                return ResolvedHelpTarget(
                    kind="error_contract",
                    error_name=cls.__name__,
                )

    raise _help_target_error(target)


def _resolve_callable(target: object) -> ResolvedHelpTarget:
    """Resolve a callable (unbound function or bound method) to a descriptor."""
    try:
        desc = REGISTRY.by_callable(target)
        return ResolvedHelpTarget(kind="descriptor", descriptor=desc)
    except KeyError:
        pass

    # If it's a type that is also callable, resolve as type.
    if isinstance(target, type):
        return _resolve_type(target)

    raise _help_target_error(target)


def _resolve_type(target: type) -> ResolvedHelpTarget:
    """Resolve a type by exact identity match against registered types."""
    # AnalysisError subclasses -> static error contract.
    if isinstance(target, type) and issubclass(target, AnalysisError):
        return ResolvedHelpTarget(
            kind="error_contract",
            error_name=target.__name__,
        )

    # Registered public types -> type contract.
    name = _TYPE_REGISTRY.get(target)
    if name is not None:
        return ResolvedHelpTarget(kind="type_contract", type_name=name)

    raise _help_target_error(target)


def _resolve_object(target: object) -> ResolvedHelpTarget:
    """Resolve a runtime object by registered type identity."""
    obj_type = type(target)

    # Registered public type instances -> type contract.
    name = _TYPE_REGISTRY.get(obj_type)
    if name is not None:
        return ResolvedHelpTarget(kind="type_contract", type_name=name)

    raise _help_target_error(target)


# ---------------------------------------------------------------------------
# HelpTargetError construction
# ---------------------------------------------------------------------------


_DEFAULT_SUGGESTIONS: tuple[str, ...] = (
    "observe",
    "compare",
    "attribute",
    "forecast",
    "help",
)


def _help_target_error(target: object) -> HelpTargetError:
    """Build a HelpTargetError with deterministic suggestions."""
    query = target if isinstance(target, str) else type(target).__name__
    suggestions = suggestions_for(query)
    if not suggestions:
        suggestions = _DEFAULT_SUGGESTIONS
    return HelpTargetError(target=target, suggestions=suggestions)


# ---------------------------------------------------------------------------
# Lexical suggestions
# ---------------------------------------------------------------------------

# Normalized token index: maps normalized token -> set of canonical help_targets.
# Built once at module load from descriptor id, public_entrypoint, summary, and
# public type/error names.


@dataclass(frozen=True)
class _SuggestionIndex:
    """Immutable lexical suggestion index over the capability registry."""

    # All canonical target strings in registry order.
    _all_targets: tuple[str, ...]
    # Pre-normalized tokens per target for overlap computation.
    _target_tokens: MappingProxyType[str, frozenset[str]]
    # Normalized target strings for substring matching.
    _normalized_targets: MappingProxyType[str, str]


def _normalize_token(text: str) -> str:
    """Lowercase and strip non-alphanumeric characters from a token."""
    return "".join(c.lower() for c in text if c.isalnum())


def _tokenize(text: str) -> frozenset[str]:
    """Split text into normalized alphanumeric tokens.

    Splits on dots, underscores, and other non-alphanumeric characters as
    word boundaries, then also emits the full concatenated form so that
    multi-word targets match both individual parts and the full name.
    """
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

    # Also emit the full concatenated token (e.g. "pointanomalies" from
    # "discover.point_anomalies") so that the full identifier matches.
    full = _normalize_token(text)
    if full:
        tokens.add(full)

    return frozenset(tokens)


def _build_suggestion_index() -> _SuggestionIndex:
    """Build the immutable lexical suggestion index."""
    target_tokens: dict[str, frozenset[str]] = {}
    normalized_targets: dict[str, str] = {}

    all_targets = REGISTRY.help_targets

    for target in all_targets:
        desc = REGISTRY.by_help_target(target)

        # Collect all text sources for tokenization.
        texts: list[str] = [
            desc.id,
            desc.help_target,
            desc.public_entrypoint,
            desc.summary,
        ]

        # Collect all tokens from all text sources.
        all_tokens: set[str] = set()
        for text in texts:
            all_tokens.update(_tokenize(text))

        target_tokens[target] = frozenset(all_tokens)
        normalized_targets[target] = _normalize_token(target)

    return _SuggestionIndex(
        _all_targets=all_targets,
        _target_tokens=MappingProxyType(target_tokens),
        _normalized_targets=MappingProxyType(normalized_targets),
    )


_SUGGESTION_INDEX: _SuggestionIndex = _build_suggestion_index()


def suggestions_for(query: str) -> tuple[str, ...]:
    """Return ranked canonical target strings for a lexical query.

    Ranking (highest first):
    1. Exact token match
    2. Normalized substring match
    3. Token overlap
    4. :class:`difflib.SequenceMatcher` distance

    Ties are broken by canonical id (alphabetical).
    Results are sliced to :data:`SURFACE_LIMITS.help_suggestion_limit`.

    Parameters
    ----------
    query:
        Free-form search string from the caller.

    Returns
    -------
    tuple[str, ...]
        Canonical help_target strings ranked by relevance, bounded by
        ``SURFACE_LIMITS.help_suggestion_limit``.
    """
    if not query or not query.strip():
        return ()

    query_tokens = _tokenize(query)
    normalized_query = _normalize_token(query)

    if not query_tokens and not normalized_query:
        return ()

    scored: list[tuple[float, str]] = []

    for target in _SUGGESTION_INDEX._all_targets:
        score = _score_target(
            target=target,
            query_tokens=query_tokens,
            normalized_query=normalized_query,
        )
        if score > 0.0:
            scored.append((score, target))

    # Sort by score descending, then by target ascending (tie-break).
    scored.sort(key=lambda pair: (-pair[0], pair[1]))

    # Slice once with the configured limit.
    limit = SURFACE_LIMITS.help_suggestion_limit
    return tuple(target for _, target in scored[:limit])


def _score_target(
    target: str,
    query_tokens: frozenset[str],
    normalized_query: str,
) -> float:
    """Compute a relevance score for a single target.

    Ranking (highest weight first):
    1. Exact token match (100 per match)
    2. Token-level fuzzy match via SequenceMatcher (ratio > 0.7, scaled)
    3. Normalized substring match on canonical target string
    4. Token overlap (Jaccard ratio)
    5. Full-string SequenceMatcher distance (fuzzy fallback)

    Returns 0.0 if no relevance signal is found.
    """
    target_tokens = _SUGGESTION_INDEX._target_tokens[target]
    normalized_target = _SUGGESTION_INDEX._normalized_targets[target]

    score = 0.0

    # 1. Exact token match — highest weight.
    exact_matches = query_tokens & target_tokens
    if exact_matches:
        score += 100.0 * len(exact_matches)

    # 2. Token-level fuzzy match.
    # Catches singular/plural (anomaly/anomalies) and near-miss typos.
    # Only applies to tokens of length >= 3 to avoid noise.
    if normalized_query and len(normalized_query) >= 3:
        best_token_ratio = 0.0
        for t_token in target_tokens:
            if len(t_token) < 3:
                continue
            if normalized_query == t_token:
                continue
            ratio = difflib.SequenceMatcher(None, normalized_query, t_token).ratio()
            if ratio > best_token_ratio:
                best_token_ratio = ratio
        if best_token_ratio >= 0.7:
            # Scale: 0.7 ratio -> 70 pts, 1.0 ratio -> 100 pts.
            score += best_token_ratio * 100.0

    # 3. Normalized substring match on canonical target string.
    if normalized_query and normalized_target:
        if normalized_query in normalized_target:
            idx = normalized_target.find(normalized_query)
            position_bonus = max(0.0, 50.0 - idx * 2.0)
            score += 50.0 + position_bonus
        elif normalized_target in normalized_query:
            score += 30.0

    # 4. Token overlap — partial word matching.
    if query_tokens and target_tokens:
        overlap = query_tokens & target_tokens
        if overlap:
            union = query_tokens | target_tokens
            overlap_ratio = len(overlap) / len(union) if union else 0.0
            score += 20.0 * overlap_ratio

    # 5. Full-string SequenceMatcher distance — fuzzy fallback.
    if score == 0.0 and normalized_query and normalized_target:
        ratio_full = difflib.SequenceMatcher(None, normalized_query, normalized_target).ratio()
        if ratio_full > 0.6:
            score += ratio_full * 10.0

    return score
