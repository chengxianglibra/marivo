"""One physical declaration captured by semantic authoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from marivo._kernel.refs import (
    Ref,
    SemanticKindTag,
)
from marivo.semantic.expressions.ir import ExpressionBody
from marivo.semantic.ir import (
    DimensionIR,
    DomainIR,
    EntityIR,
    MeasureIR,
    RelationshipIR,
)

DefinitionIR: TypeAlias = DomainIR | EntityIR | DimensionIR | MeasureIR | RelationshipIR


@dataclass(frozen=True, slots=True)
class PendingDefinition:
    """One exact authoring identity, private IR definition, and optional body."""

    ref: Ref[SemanticKindTag]
    definition: DefinitionIR
    expression_body: ExpressionBody | None
