"""Closed policy values captured in semantic definitions."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Literal, TypeAlias

from marivo._definition.types import InputContext

_TOKEN = object()


@dataclass(frozen=True, slots=True)
class AdditiveOverV1:
    coordinates: tuple[str, ...]
    version: Literal[1] = 1
    _context: InputContext = field(default_factory=InputContext)
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.additive_over(...) to construct an additivity policy")


@dataclass(frozen=True, slots=True)
class AdditiveAllV1:
    exceptions: tuple[str, ...]
    version: Literal[1] = 1
    _context: InputContext = field(default_factory=InputContext)
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.additive_all() or ms.additive_except(...)")


@dataclass(frozen=True, slots=True)
class NonAdditiveV1:
    version: Literal[1] = 1
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.non_additive() to construct an additivity policy")


AdditivityPolicy: TypeAlias = AdditiveOverV1 | AdditiveAllV1 | NonAdditiveV1


@dataclass(frozen=True, slots=True)
class NullInputPolicyV1:
    kind: Literal["reject", "ignore"]
    version: Literal[1] = 1
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.nulls.reject() or ms.nulls.ignore()")


@dataclass(frozen=True, slots=True)
class EmptyContributionPolicyV1:
    kind: Literal["zero", "null"]
    version: Literal[1] = 1
    _token: InitVar[object] = None

    def __post_init__(self, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("Use ms.empty.zero() or ms.empty.null()")
