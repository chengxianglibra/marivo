"""Immutable semantic state of one completed load."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from marivo._kernel.refs import Ref, SemanticKindTag
from marivo.datasource.spi import AiContextIR
from marivo.semantic._definition_identity import scoped_definition_fingerprint
from marivo.semantic.expressions.ir import CompiledExpressionSidecar
from marivo.semantic.static_check.registry import Registry


@dataclass(frozen=True, slots=True)
class CompiledRootIdentity:
    """Semantic source-root role and configured order, without machine paths."""

    role: str
    order: int


@dataclass(frozen=True, slots=True)
class CompiledSemanticState:
    """One immutable interpretation of the selected authored semantic world."""

    definition_fingerprint: str
    selected_roots: tuple[CompiledRootIdentity, ...]
    filtered_domains: tuple[str, ...]
    registry: Registry
    definitions: Mapping[Ref[SemanticKindTag], object]
    dependencies: Mapping[Ref[SemanticKindTag], tuple[Ref[SemanticKindTag], ...]]
    sidecar: CompiledExpressionSidecar
    _scoped_fingerprints: dict[Ref[SemanticKindTag], str] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def _definition_version(self, root: Ref[SemanticKindTag]) -> str:

        version = self._scoped_fingerprints.get(root)
        if version is None:
            version = scoped_definition_fingerprint(
                root=root,
                definitions=self.definitions,
                dependencies=self.dependencies,
                sidecar=self.sidecar,
            )
            self._scoped_fingerprints[root] = version
        return version

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected_roots", tuple(self.selected_roots))
        object.__setattr__(self, "filtered_domains", tuple(self.filtered_domains))
        object.__setattr__(self, "definitions", MappingProxyType(dict(self.definitions)))
        object.__setattr__(
            self,
            "dependencies",
            MappingProxyType({ref: tuple(values) for ref, values in self.dependencies.items()}),
        )


@dataclass(frozen=True, slots=True)
class PublishedDeclaration:
    """Definition row of one published pure declaration.

    Its identity is the computation it consumes; names and sites stay on the
    original declaration, which is never rewritten.
    """

    stage: Literal["measure", "metric"]
    name: str
    computation: str
    unit: str | None
    ai_context: AiContextIR
