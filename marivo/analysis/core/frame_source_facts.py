"""Lossless inert source constraints; never output formulas or replay data."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from ibis.common.deferred import Attr, BinaryOperator, Call, Just, Resolver, Sequence, UnaryOperator

from marivo._definition.deferred import RelationshipParameter, canonical_recipe
from marivo._definition.expressions import (
    MeasureAssertion,
    PredicateExpression,
    RowExpression,
    RowField,
)
from marivo._definition.relations import (
    CalendarKeys,
    FrameKeyAxis,
    FrameKeys,
    IntervalKeys,
    LiteralPopulation,
    MembershipBinding,
    PopulationClassify,
    PopulationContains,
    PopulationFilter,
    PopulationImage,
    PopulationParameter,
    PopulationPreimage,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    PopulationSet,
    SourceIdentities,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    ExactRef,
    FieldRead,
    IdentityRead,
    IntervalCoordinate,
    Owner,
    literal_token,
)
from marivo._grouping import Grouping
from marivo.analysis.core.data_types import (
    DataColumn,
    DecimalDataType,
    DurationDataType,
    ScalarDataType,
    TimestampDataType,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.model import reject
from marivo.analysis.materialization.contracts import canonical_json

_SCOPE_RECORDS = (
    DataColumn,
    ScalarDataType,
    DecimalDataType,
    DurationDataType,
    TimestampDataType,
    FrameKeyAxis,
    FrameKeys,
    LiteralPopulation,
    PopulationProgram,
    PopulationSet,
    PopulationProject,
    PopulationProduct,
    PopulationClassify,
    PopulationFilter,
    PopulationImage,
    PopulationPreimage,
    SourceIdentities,
    CalendarKeys,
    IntervalKeys,
    PopulationContains,
    MembershipBinding,
    PopulationParameter,
    AxisBinding,
    FieldRead,
    IdentityRead,
    RelationshipParameter,
    Owner,
)

_SPECIAL = (Grouping, MembershipBinding, PopulationParameter)


def _special_scopes(recipe: Resolver) -> tuple[object, ...]:
    if isinstance(recipe, Just):
        return (_scope(recipe.value),) if isinstance(recipe.value, _SPECIAL) else ()
    if isinstance(recipe, BinaryOperator):
        return (*_special_scopes(recipe.left), *_special_scopes(recipe.right))
    if isinstance(recipe, UnaryOperator):
        return _special_scopes(recipe.arg)
    if isinstance(recipe, Sequence):
        return tuple(scope for item in recipe.values for scope in _special_scopes(item))
    if isinstance(recipe, Attr):
        return _special_scopes(recipe.obj)
    if isinstance(recipe, Call):
        return tuple(
            scope
            for item in (recipe.func, *recipe.args, *recipe.kwargs.values())
            for scope in _special_scopes(item)
        )
    return ()


def _scope(value: object) -> object:
    if isinstance(value, Grouping):
        return _scope(value._binding)
    if value is None or isinstance(
        value, (str, int, float, bool, Decimal, date, datetime, timedelta)
    ):
        return literal_token(value)
    if isinstance(value, ExactRef):
        return ("reference", value.ref.key, value.version)
    if isinstance(value, tuple):
        return tuple(_scope(item) for item in value)
    if isinstance(value, RowField):
        return {
            "scope": "field",
            "field": _scope(value.ref),
            "root": _scope(value.root),
            "read": _scope(value.read),
        }
    if isinstance(value, MeasureAssertion):
        return {
            "scope": "asserted_filter_operand",
            "unit": value.unit,
            "operand": _scope(value.operand),
        }
    if isinstance(value, (RowExpression, PredicateExpression)):
        return {
            "scope": "filter",
            "tokens": canonical_recipe(
                value.recipe, slots=tuple(f"slot_{i}" for i in range(len(value.inputs)))
            ),
            "inputs": tuple(_scope(item) for item in value.inputs),
            "special_scopes": _special_scopes(value.recipe),
        }
    if isinstance(value, IntervalCoordinate):
        return {
            "scope": "interval",
            "start": _scope(value.start),
            "end": _scope(value.end),
            "timezone": value.timezone,
        }
    if isinstance(value, CalendarCoordinate):
        return {
            "scope": "calendar",
            "identity": value.identity,
            "grain": value.grain,
            "timezone": value.timezone,
            "snapshot_digest": value.snapshot_digest,
            "cells": _scope(value.cells),
        }
    if isinstance(value, PopulationParameter):
        return {"scope": "PopulationParameter", "definition": _scope(value._definition)}
    if isinstance(value, MembershipBinding):
        return {
            "scope": "MembershipBinding",
            "fields": _scope(value.fields),
            "paths": _scope(value.paths),
            "unmatched": _scope(value.unmatched),
        }
    if isinstance(value, RelationshipParameter):
        return {
            "scope": "RelationshipParameter",
            "ref": _scope(value.ref),
            "from_entity": _scope(value.from_entity),
            "to_entity": _scope(value.to_entity),
            "cardinality": value.cardinality,
        }
    if type(value) in _SCOPE_RECORDS and is_dataclass(value) and not isinstance(value, type):
        return {
            "scope": type(value).__name__,
            **{field.name: _scope(getattr(value, field.name)) for field in fields(value)},
        }
    reject(
        "a supported inert source constraint",
        type(value).__name__,
        "Capture complete scalar filters and joint key restrictions; do not retain a producer graph.",
        "frame.audit.scope",
    )


def source_scope(value: object) -> str:
    """Encode a closed source constraint losslessly as non-executable scalar JSON."""
    return canonical_json(_scope(value))


def captured_source_scopes(graph: BoundGraph) -> tuple[str, ...]:
    """Read only the known source entry's captured restrictions, without execution."""
    observation = graph.signature.observation
    if observation is None:
        return ()
    scopes = (
        observation.condition,
        *(domain for pair in observation.count_populations for domain in pair),
        observation.coordinates.population,
        *observation.coordinates.memberships,
        *(rule for contribution in observation.contributions for rule in contribution.memberships),
        *(
            condition
            for contribution in observation.contributions
            for condition in (
                *contribution.intrinsic_conditions,
                *(
                    (contribution.common_condition,)
                    if contribution.common_condition is not None
                    else ()
                ),
            )
        ),
    )
    return tuple(dict.fromkeys(source_scope(scope) for scope in scopes if scope is not None))
