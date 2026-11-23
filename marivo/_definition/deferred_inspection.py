"""Closed facts from source-free Deferred recipe inspection."""

from dataclasses import dataclass, field
from typing import Final

from ibis.common.deferred import Resolver

from marivo._definition.deferred_values import DeclarationRecord, SemanticInput
from marivo._definition.types import ExactRef, InputContext
from marivo._kernel.refs import Ref, SemanticKindTag

SEMANTIC_METHODS: Final = frozenset(
    {
        "sum",
        "mean",
        "min",
        "max",
        "median",
        "quantile",
        "count",
        "count_distinct",
        "first",
        "last",
        "approx_median",
        "approx_nunique",
        "weighted_mean",
        "approx_quantile",
        "via",
        "at",
        "within",
        "where",
        "per",
        "window",
    }
)


@dataclass(frozen=True, slots=True)
class SemanticUse:
    """One captured input occurrence, including repeated shared inputs."""

    path: tuple[str, ...]
    input: SemanticInput


@dataclass(frozen=True, slots=True)
class RecipeUse:
    """One operation occurrence with its original Ibis resolver and role path."""

    path: tuple[str, ...]
    symbol: str
    recipe: Resolver


@dataclass(frozen=True, slots=True)
class DeferredInspection:
    """Source-free capture evidence; this does not certify execution eligibility."""

    recipe: Resolver
    declaration: DeclarationRecord | None
    dependencies: tuple[ExactRef, ...]
    uses: tuple[SemanticUse, ...]
    operations: tuple[RecipeUse, ...]
    declarations: tuple[DeclarationRecord, ...]
    fingerprint: str
    symbols: tuple[Ref[SemanticKindTag], ...] = ()
    context: InputContext = field(default_factory=InputContext)
    identity_keys: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def semantic_calls(self) -> tuple[RecipeUse, ...]:
        return tuple(use for use in self.operations if use.symbol in SEMANTIC_METHODS)
