"""Canonical descriptor schema for agent-facing help surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from marivo.introspection.constraints import Constraint

SCHEMA_VERSION = "1"

Kind = Literal["callable", "class", "frame", "module", "topic", "surface", "unknown"]


@dataclass(frozen=True)
class MethodInfo:
    """L1 method summary for class and frame descriptors."""

    name: str
    summary: str


@dataclass(frozen=True)
class TopLevelEntry:
    """L1 entry shown by top-level surface help."""

    name: str
    kind: Kind
    summary: str


@dataclass(frozen=True)
class Descriptor:
    """Internal descriptor rendered by surface adapters."""

    surface: str
    kind: Kind
    symbol: str | None
    summary: str
    doc: str = ""
    signature: str | None = None
    constraints: tuple[Constraint, ...] = ()
    examples: tuple[str, ...] = ()
    see_also: tuple[str, ...] = ()
    methods: tuple[MethodInfo, ...] = ()
    next_intents: tuple[str, ...] = ()
    constructed_by: str | None = None
    entries: tuple[TopLevelEntry, ...] = ()
    content: dict[str, Any] = field(default_factory=dict)
    did_you_mean: tuple[str, ...] = ()
