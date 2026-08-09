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
- ``_authoring_metrics``      — derived-metric compositions and cumulative anchors
"""

from __future__ import annotations

from marivo.semantic._authoring_declarations import (
    aggregate,
    count,
    domain,
    metric,
    weighted_mean,
    where,
)
from marivo.semantic._authoring_decorators import (
    dimension,
    dimension_column,
    entity,
    event,
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
)
from marivo.semantic._authoring_temporal import (
    PeriodCorrespondence,
    calendar_grain,
    period_calendar,
    period_correspondence,
    temporal_set,
    work_schedule,
)
from marivo.semantic._authoring_values import (
    ai_context,
    datetime,
    from_sql,
    hour_prefix,
    join_on,
    semi_additive,
    snapshot,
    strptime,
    timestamp,
    validity,
)
from marivo.semantic._expression_binding import bind
from marivo.semantic.event import all_rows, participant, participant_role
from marivo.semantic.ir import AggregateFoldInput, AggregateFoldValue
from marivo.semantic.state_model import (
    inception,
    lifecycle_state,
    model_state,
    state_model,
    transition,
)

__all__ = [
    "AggregateFoldInput",
    "AggregateFoldValue",
    "GrainToDate",
    "PeriodCorrespondence",
    "aggregate",
    "ai_context",
    "all_rows",
    "bind",
    "calendar_grain",
    "count",
    "cumulative",
    "datetime",
    "dimension",
    "dimension_column",
    "domain",
    "entity",
    "event",
    "from_sql",
    "grain_to_date",
    "hour_prefix",
    "inception",
    "join_on",
    "lifecycle_state",
    "linear",
    "measure",
    "measure_column",
    "metric",
    "model_state",
    "participant",
    "participant_role",
    "period_calendar",
    "period_correspondence",
    "ratio",
    "relationship",
    "semi_additive",
    "snapshot",
    "state_model",
    "strptime",
    "temporal_set",
    "time_dimension",
    "time_dimension_column",
    "timestamp",
    "trailing",
    "transition",
    "validity",
    "weighted_mean",
    "where",
    "work_schedule",
]
