"""Private immutable definition roots and canonical realization identity."""

from __future__ import annotations

from typing import TypeAlias

from marivo.analysis.datasets.descriptors import (
    _canonical_digest,
    _CanonicalValue,
)
from marivo.analysis.datasets.errors import DatasetConstructionError, DatasetDefinitionError

CanonicalValue: TypeAlias = _CanonicalValue


def _definition_error(expected: str, received: str) -> DatasetDefinitionError:
    return DatasetDefinitionError(
        expected=expected,
        received=received,
        repair="Reconstruct the definition through its registered owner using canonical values.",
        location="dataset.definition",
    )


def _digest(value: CanonicalValue) -> str:
    try:
        return _canonical_digest(value)
    except DatasetConstructionError as exc:
        assert exc.expected is not None and exc.received is not None
        raise _definition_error(exc.expected, exc.received) from exc
