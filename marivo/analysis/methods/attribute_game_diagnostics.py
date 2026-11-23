"""Attach owning Attribute context to native numeric failures."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from marivo.analysis.methods.carriers.stages import StageOutput
from marivo.analysis.model.attribute_failures import (
    AttributeFailureContext,
    AttributeNumericWitness,
    AttributeParentFiber,
)
from marivo.analysis.model.data_types import DataColumn
from marivo.analysis.model.frozen_keys import FrozenKey
from marivo.semantic.errors import SemanticExpressionError


@contextmanager
def attribute_numeric_context(
    inputs: tuple[StageOutput, ...],
    metric: str,
    parent: FrozenKey,
    columns: tuple[DataColumn, ...],
) -> Iterator[None]:
    """Keep the numeric owner's failure attached to its actual attribute parent."""
    try:
        yield
    except SemanticExpressionError as error:
        error.details["attribute_context"] = AttributeFailureContext(
            tuple(
                leaf.use
                for source in inputs
                for root in source.attribute_inputs
                for leaf in root.layout.leaves
                if leaf.use.metric == metric
            ),
            AttributeParentFiber(parent, columns),
            AttributeNumericWitness((), "contribution", ()),
        )
        error.received = f"metric={metric}; parent={parent!r}; {error.received}"
        raise
