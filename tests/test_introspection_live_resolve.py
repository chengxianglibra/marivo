"""Generalized live help target resolver and suggestion index."""

from __future__ import annotations

import pytest

from marivo.introspection.live.model import (
    SURFACE_LIMITS,
    AuthoringEffects,
    LiveCapability,
)
from marivo.introspection.live.resolve import (
    LiveSurface,
    build_suggestion_index,
    resolve_live_target,
    suggestions_for,
)


class _FakeError(BaseException):
    def __init__(self, message: str = "bad target") -> None:
        super().__init__(message)
        self._message = message

    @property
    def kind(self) -> str:
        return "fake"


def _raise_fake(target: object, suggestions: tuple[str, ...]) -> None:
    raise _FakeError(f"unregistered: {suggestions}")


class _FakeRegistry:
    surface = "semantic"

    def __init__(self, caps: tuple[LiveCapability, ...]) -> None:
        self._caps = caps
        self._by_id = {c.canonical_id: c for c in caps}

    def canonical_ids(self) -> tuple[str, ...]:
        return tuple(c.canonical_id for c in self._caps)

    def by_canonical_id(self, canonical_id: str) -> LiveCapability:
        return self._by_id[canonical_id]

    def by_callable(self, obj: object) -> LiveCapability:
        raise KeyError(obj)


def _cap(canonical_id: str, summary: str) -> LiveCapability:
    return LiveCapability(
        canonical_id=canonical_id,
        kind="callable",
        surface="semantic",
        public_entrypoint=f"ms.{canonical_id}",
        summary=summary,
        effects=AuthoringEffects(data_access="none", connection="none"),
    )


def _surface(caps, **overrides) -> LiveSurface:
    registry = _FakeRegistry(caps)
    index = build_suggestion_index(registry)
    defaults: dict[str, object] = {
        "registry": registry,
        "type_index": {},
        "error_types": {"FakeError": _FakeError},
        "error_base": _FakeError,
        "default_suggestions": ("preview",),
        "suggestion_index": index,
        "help_target_error": _raise_fake,
        "enrich": None,
    }
    defaults.update(overrides)
    return LiveSurface(**defaults)


PREVIEW = _cap("preview", "Scoped runtime preview of one loaded semantic object.")
READINESS = _cap("readiness", "Analysis readiness for one or more refs.")


def test_resolve_canonical_string_to_descriptor():
    surface = _surface((PREVIEW, READINESS))
    resolved = resolve_live_target("preview", surface)
    assert resolved.kind == "descriptor"
    assert resolved.surface == "semantic"
    assert resolved.descriptor is PREVIEW


def test_resolve_callable_to_descriptor():
    def preview():  # registered callable stand-in
        ...

    cap = LiveCapability(
        canonical_id="preview",
        kind="callable",
        surface="semantic",
        public_entrypoint="ms.preview",
        summary="preview",
        effects=AuthoringEffects(data_access="none", connection="none"),
    )

    class _Registry(_FakeRegistry):
        def by_callable(self, obj):
            if obj is preview:
                return cap
            raise KeyError(obj)

    # Build a surface whose registry knows the callable.
    registry = _Registry((cap,))
    index = build_suggestion_index(registry)
    s = LiveSurface(
        registry=registry,
        type_index={},
        error_types={},
        error_base=_FakeError,
        default_suggestions=(),
        suggestion_index=index,
        help_target_error=_raise_fake,
        enrich=None,
    )
    resolved = resolve_live_target(preview, s)
    assert resolved.kind == "descriptor"
    assert resolved.descriptor is cap


def test_resolve_error_instance_via_enrich():
    from marivo.introspection.live.resolve import ResolvedLiveTarget

    def enrich(target: object):
        if isinstance(target, _FakeError):
            return ResolvedLiveTarget(
                kind="error_briefing",
                surface="semantic",
                error_name=type(target).__name__,
                error_kind=target.kind,
                original=target,
            )
        return None

    surface = _surface((PREVIEW,), enrich=enrich)
    resolved = resolve_live_target(_FakeError(), surface)
    assert resolved.kind == "error_briefing"
    assert resolved.error_name == "_FakeError"
    assert resolved.error_kind == "fake"


def test_resolve_error_type_to_error_contract():
    surface = _surface((PREVIEW,), error_types={"_FakeError": _FakeError})
    resolved = resolve_live_target("_FakeError", surface)
    assert resolved.kind == "error_contract"
    assert resolved.error_name == "_FakeError"


def test_resolve_unregistered_string_raises_surface_error():
    surface = _surface((PREVIEW, READINESS))
    with pytest.raises(_FakeError):
        resolve_live_target("not_a_target", surface)


def test_suggestions_rank_exact_token_first_and_are_bounded():
    surface = _surface((PREVIEW, READINESS))
    suggestions = suggestions_for("preview", surface.suggestion_index)
    assert suggestions
    assert suggestions[0] == "preview"
    assert len(suggestions) <= SURFACE_LIMITS.help_suggestion_limit


def test_suggestions_empty_for_blank_query():
    surface = _surface((PREVIEW,))
    assert suggestions_for("   ", surface.suggestion_index) == ()
