"""Generalized live help target resolver and suggestion index."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, NoReturn, TypedDict

import pytest
from typing_extensions import NotRequired

from marivo._authoring.model import (
    AuthoringCapability,
    AuthoringEffects,
)
from marivo.introspection.live.model import SURFACE_LIMITS
from marivo.introspection.live.resolve import (
    LiveSurface,
    ResolvedLiveTarget,
    ResolveKind,
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


def _raise_fake(target: object, suggestions: tuple[str, ...]) -> NoReturn:
    raise _FakeError(f"unregistered: {suggestions}")


class _FakeRegistry:
    surface: Literal["semantic"] = "semantic"

    def __init__(self, caps: tuple[AuthoringCapability, ...]) -> None:
        self._caps = caps
        self._by_id = {c.canonical_id: c for c in caps}

    def canonical_ids(self) -> tuple[str, ...]:
        return tuple(c.canonical_id for c in self._caps)

    def discovery_ids(self) -> tuple[str, ...]:
        return self.canonical_ids()

    def by_canonical_id(self, canonical_id: str) -> AuthoringCapability:
        return self._by_id[canonical_id]

    def by_callable(self, obj: object) -> AuthoringCapability:
        raise KeyError(obj)


def _cap(canonical_id: str, summary: str) -> AuthoringCapability:
    return AuthoringCapability(
        canonical_id=canonical_id,
        kind="callable",
        surface="semantic",
        public_entrypoint=f"ms.{canonical_id}",
        summary=summary,
        effects=AuthoringEffects(data_access="none", connection="none"),
    )


def _surface(
    caps: tuple[AuthoringCapability, ...],
    *,
    enrich: Callable[[object], ResolvedLiveTarget[AuthoringCapability] | None] | None = None,
) -> LiveSurface[AuthoringCapability]:
    registry = _FakeRegistry(caps)
    index = build_suggestion_index(registry)
    return LiveSurface(
        registry=registry,
        type_index={},
        error_types={"FakeError": _FakeError},
        error_base=_FakeError,
        default_suggestions=("preview",),
        suggestion_index=index,
        help_target_error=_raise_fake,
        enrich=enrich,
    )


PREVIEW = _cap("preview", "Scoped runtime preview of one loaded semantic object.")
READINESS = _cap("readiness", "Analysis readiness for one or more refs.")


def test_suggestions_rank_exact_token_first_and_are_bounded() -> None:
    surface = _surface((PREVIEW, READINESS))
    assert surface.suggestion_index is not None
    suggestions = suggestions_for("preview", surface.suggestion_index)
    assert suggestions
    assert suggestions[0] == "preview"
    assert len(suggestions) <= SURFACE_LIMITS.help_suggestion_limit


def test_suggestions_empty_for_blank_query() -> None:
    surface = _surface((PREVIEW,))
    assert surface.suggestion_index is not None
    assert suggestions_for("   ", surface.suggestion_index) == ()


class _ResolvedPayload(TypedDict):
    kind: ResolveKind
    surface: Literal["semantic"]
    canonical_id: NotRequired[str]
    descriptor: NotRequired[AuthoringCapability]
    type_name: NotRequired[str]
    error_name: NotRequired[str]
    attribute_name: NotRequired[str]
    attribute_owner: NotRequired[type[object]]


_VALID_RESOLVED_CASES: tuple[_ResolvedPayload, ...] = (
    {
        "kind": "descriptor",
        "surface": "semantic",
        "canonical_id": "preview",
        "descriptor": PREVIEW,
    },
    {"kind": "type_contract", "surface": "semantic", "type_name": "PreviewResult"},
    {
        "kind": "attribute",
        "surface": "semantic",
        "type_name": "PreviewResult",
        "attribute_name": "rows",
        "attribute_owner": object,
    },
    {"kind": "error_contract", "surface": "semantic", "error_name": "FakeError"},
)


@pytest.mark.parametrize("payload", _VALID_RESOLVED_CASES)
def test_resolved_target_accepts_only_documented_valid_shapes(payload: _ResolvedPayload) -> None:
    assert ResolvedLiveTarget(**payload).kind == payload["kind"]


def test_resolve_rejects_surface_alias_and_name_prefix_from_target_string() -> None:
    surface = _surface((PREVIEW, READINESS))
    for target in ("ms.preview", "semantic ms.preview", "semantic preview", "Preview"):
        with pytest.raises(_FakeError):
            resolve_live_target(target, surface)


def test_resolve_rejects_instances_before_enrichment() -> None:
    def enrich(target: object) -> NoReturn:
        raise AssertionError("Instances must not reach enrichment")

    surface = _surface((PREVIEW,), enrich=enrich)
    with pytest.raises(_FakeError):
        resolve_live_target(_FakeError(), surface)


def test_resolve_unregistered_string_raises_surface_error() -> None:
    surface = _surface((PREVIEW, READINESS))
    with pytest.raises(_FakeError):
        resolve_live_target("not_a_target", surface)


@pytest.mark.parametrize("payload", _VALID_RESOLVED_CASES)
def test_resolved_target_rejects_missing_required_payload(payload: _ResolvedPayload) -> None:
    required = {
        "descriptor": ("canonical_id", "descriptor"),
        "type_contract": ("type_name",),
        "attribute": ("type_name", "attribute_name", "attribute_owner"),
        "error_contract": ("error_name",),
    }[payload["kind"]]
    for field in required:
        invalid = dict(payload)
        del invalid[field]
        with pytest.raises(ValueError):
            ResolvedLiveTarget(**invalid)  # type: ignore[arg-type]  # Exercise malformed payloads.


@pytest.mark.parametrize("payload", _VALID_RESOLVED_CASES)
def test_resolved_target_rejects_every_undocumented_payload(payload: _ResolvedPayload) -> None:
    allowed = {
        "descriptor": {"canonical_id", "descriptor"},
        "type_contract": {"type_name"},
        "attribute": {"type_name", "attribute_name", "attribute_owner"},
        "error_contract": {"error_name"},
    }[payload["kind"]]
    sentinels: dict[str, object] = {
        "attribute_owner": object,
        "attribute_name": "unexpected",
        "canonical_id": "unexpected",
        "descriptor": PREVIEW,
        "type_name": "Unexpected",
        "error_name": "UnexpectedError",
    }
    for field, sentinel in sentinels.items():
        if field in allowed:
            continue
        invalid = dict(payload)
        invalid[field] = sentinel
        with pytest.raises(ValueError):
            ResolvedLiveTarget(**invalid)  # type: ignore[arg-type]  # Exercise malformed payloads.
