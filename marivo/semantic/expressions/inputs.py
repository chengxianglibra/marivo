"""Controlled expression inputs for source-free recipe inspection."""

from __future__ import annotations

from ibis.common.deferred import Deferred, Resolver

from marivo._definition.types import InputContext
from marivo._kernel.paths import _RelationshipValue


class _OmittedTime:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<omitted>"


_OMITTED_TIME = _OmittedTime()


class RecipeInput:
    """Nominal bridge from typed expression values to controlled Ibis recipes."""

    __slots__ = ()

    @property
    def _recipe(self) -> Resolver:
        raise NotImplementedError


class CatalogInput:
    """A named input from a completed Catalog."""

    __slots__ = ()

    @property
    def _context(self) -> InputContext:
        raise NotImplementedError


class CatalogRelationshipInput(_RelationshipValue):
    """A named Relationship whose receiving scope owns the exact identity."""

    __slots__ = ()


def _as_deferred(value: RecipeInput) -> Deferred:
    return Deferred(value._recipe)
