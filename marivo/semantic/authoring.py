"""Authoring decorators and builders for marivo.semantic v1.1.

All authoring symbols are re-exported from private ``_authoring_*`` submodules
so that ``from marivo.semantic.authoring import ...`` and
``from marivo.semantic import authoring; authoring.<name>`` continue to work
unchanged.  Implementations live in:

- ``_authoring_context``      — loader-context integration, ref/location plumbing
- ``_authoring_validation``   — pure parameter validation and normalization
- ``_authoring_values``       — value-object constructors (ai_context, parse, ref, ...)
- ``_authoring_declarations`` — top-level domain and tier-1 metric declarations
- ``_authoring_decorators``   — entity-scoped field decorators
- ``_authoring_metrics``      — derived-metric compositions and cumulative anchors
"""

from __future__ import annotations

from marivo.semantic._authoring_declarations import aggregate, count, domain, metric
from marivo.semantic._authoring_decorators import (
    dimension,
    dimension_column,
    entity,
    measure,
    measure_column,
    relationship,
    time_dimension,
    time_dimension_column,
)
from marivo.semantic._authoring_metrics import (  # noqa: F401
    CumulativeComposition,
    GrainToDate,
    Trailing,
    _compute_composition_hash,
    cumulative,
    grain_to_date,
    linear,
    ratio,
    trailing,
    weighted_average,
)
from marivo.semantic._authoring_values import (
    ai_context,
    datetime,
    from_sql,
    hour_prefix,
    join_on,
    ref,
    semi_additive,
    snapshot,
    strptime,
    timestamp,
    validity,
)
from marivo.semantic.refs import DomainRef

__all__ = [
    "DomainRef",
    "aggregate",
    "ai_context",
    "count",
    "cumulative",
    "datetime",
    "dimension",
    "dimension_column",
    "domain",
    "entity",
    "from_sql",
    "grain_to_date",
    "hour_prefix",
    "join_on",
    "linear",
    "measure",
    "measure_column",
    "metric",
    "ratio",
    "ref",
    "relationship",
    "semi_additive",
    "snapshot",
    "strptime",
    "time_dimension",
    "time_dimension_column",
    "timestamp",
    "trailing",
    "validity",
    "weighted_average",
]
