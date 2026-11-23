"""Authoring decorators and builders for marivo.semantic v1.1.

All authoring symbols are re-exported from private ``_authoring_*`` submodules
so that ``from marivo.semantic.authoring import ...`` and
``from marivo.semantic import authoring; authoring.<name>`` continue to work
unchanged.  Implementations live in:

- ``_authoring_context``      — loader-context integration, ref/location plumbing
- ``_authoring_validation``   — pure parameter validation and normalization
- ``_authoring_values``       — value-object constructors (ai_context, parse, ...)
- ``_authoring_declarations`` — top-level domain and tier-1 metric declarations
- ``_authoring_decorators``   — entity-scoped field decorators
- ``_authoring_metrics``      — retired Metric factory diagnostics
"""

from __future__ import annotations

from typing import NoReturn

from marivo.semantic._expression_binding import bind
from marivo.semantic._rescale import rescale
from marivo.semantic.authoring.additivity import (
    non_additive,
)
from marivo.semantic.authoring.context import _register_authoring_file
from marivo.semantic.authoring.declarations import (
    aggregate as aggregate,
)
from marivo.semantic.authoring.declarations import (
    count as count,
)
from marivo.semantic.authoring.declarations import (
    domain,
    metric,
)
from marivo.semantic.authoring.declarations import (
    weighted_mean as weighted_mean,
)
from marivo.semantic.authoring.declarations import (
    where as where,
)
from marivo.semantic.authoring.entity import entity
from marivo.semantic.authoring.fields import (
    dimension,
    dimension_column,
    measure,
    measure_column,
    time_dimension,
    time_dimension_column,
)
from marivo.semantic.authoring.metrics import (
    linear as linear,
)
from marivo.semantic.authoring.metrics import (
    ratio as ratio,
)
from marivo.semantic.authoring.relationship import relationship
from marivo.semantic.authoring.retired import retired_factory
from marivo.semantic.authoring.values import (
    additive_all,
    additive_except,
    additive_over,
    ai_context,
    date,
    hour_prefix,
    join_on,
    snapshot,
    strptime,
    timestamp,
    validity,
)


class _RetiredPolicy:
    """A diagnostic-only old namespace; every operation rejects."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *_args: object, **kwargs: object) -> NoReturn:

        retired_factory(self._name, kwargs)

    def ignore(self) -> NoReturn:
        self()

    def reject(self) -> NoReturn:
        self()

    def null(self) -> NoReturn:
        self()

    def zero(self) -> NoReturn:
        self()

    def undefined(self) -> NoReturn:
        self()

    def error(self) -> NoReturn:
        self()


empty = _RetiredPolicy("empty")
nulls = _RetiredPolicy("nulls")

__all__ = [
    "additive_all",
    "additive_except",
    "additive_over",
    "ai_context",
    "bind",
    "date",
    "dimension",
    "dimension_column",
    "domain",
    "entity",
    "hour_prefix",
    "join_on",
    "measure",
    "measure_column",
    "metric",
    "non_additive",
    "relationship",
    "rescale",
    "snapshot",
    "strptime",
    "time_dimension",
    "time_dimension_column",
    "timestamp",
    "validity",
]


_register_authoring_file(__file__)
