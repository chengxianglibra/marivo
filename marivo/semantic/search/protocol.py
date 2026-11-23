"""Shared embedding operations without a dependency on a model runtime."""

from collections.abc import Iterable
from typing import Protocol, TypeVar

_BatchT = TypeVar("_BatchT", covariant=True)


class EmbeddingBackend(Protocol[_BatchT]):
    """Encode text in one model's native batch representation."""

    @property
    def model_fingerprint(self) -> str: ...

    @property
    def dimensions(self) -> int | None: ...

    def encode_texts(self, texts: list[str]) -> _BatchT: ...


def valid_embedding_shape(shape: tuple[int, ...], rows: int, dimensions: int | None = None) -> bool:
    """Check the correspondence between index rows and encoded vectors."""
    return (
        len(shape) == 2
        and shape[0] == rows
        and shape[1] > 0
        and (dimensions is None or shape[1] == dimensions)
    )


def rank_similarity(
    scores: Iterable[tuple[str, float]], *, minimum: float | None = None
) -> tuple[tuple[str, float], ...]:
    """Order similarity scores by descending value with stable identity ties."""
    selected = (item for item in scores if minimum is None or item[1] >= minimum)
    return tuple(sorted(selected, key=lambda item: (-item[1], item[0])))
