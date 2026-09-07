"""Sealed private Entity sampling requests; physical execution belongs to Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import SupportsIndex

from marivo._compat import Never
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import CanonicalValue

_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class EntitySamplingPolicy:
    """One immutable engine-chosen Entity sampling request, produced by engine_sample."""

    target_rows: int
    seed: int | None

    def __init__(self, *, target_rows: int, seed: int | None, _token: object = None) -> None:
        if _token is not _TOKEN:
            raise _error("a helper-produced sampling policy", "direct construction")
        object.__setattr__(self, "target_rows", target_rows)
        object.__setattr__(self, "seed", seed)

    def __repr__(self) -> str:
        target = (
            str(self.target_rows) if self.target_rows.bit_length() <= 256 else "<large integer>"
        )
        return f"EntitySamplingPolicy(target_rows={target}, seeded={self.seed is not None})"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise _error("a helper-produced sampling policy", "generic serialization")

    @property
    def identity_payload(self) -> CanonicalValue:
        return ("engine_sample", self.target_rows, self.seed)


def _error(expected: str, received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected=expected,
        received=received,
        repair="Call engine_sample(target_rows=positive_integer, seed=integer_or_none).",
        location="population.sample",
    )


def engine_sample(*, target_rows: int, seed: int | None = None) -> EntitySamplingPolicy:
    """Request an engine-selected sample of Entity identities without executing.

    Args: target_rows: Positive exact integer planning target. seed: Exact integer or None.
    Returns: Immutable EntitySamplingPolicy. Example: ``engine_sample(target_rows=100, seed=42)``.
    Constraints: Cardinality and seeded capability are resolved by the admitted engine.
    """
    if type(target_rows) is not int or target_rows <= 0:
        raise _error("a positive exact integer target_rows", type(target_rows).__name__)
    if seed is not None and type(seed) is not int:
        raise _error("an exact integer seed or None", type(seed).__name__)
    return EntitySamplingPolicy(target_rows=target_rows, seed=seed, _token=_TOKEN)
