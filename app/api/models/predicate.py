"""Predicate semantic object models.

This module defines the API models for predicate objects,
following the contract defined in docs/semantic/predicate-schema-contract.zh.md.

Predicates define governed, reusable filter semantics consumed by metrics,
bindings, request scopes, and governance policies.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .base import (
    ObjectHeaderBase,
    PredicateOperator,
    PredicateTimePolicy,
    PredicateUsage,
    validate_contract_version,
    validate_ref_prefix,
)

# =============================================================================
# Predicate Header
# =============================================================================


class PredicateHeader(ObjectHeaderBase):
    """Header for a predicate object.

    Defines the stable identity and primary semantic subject of a predicate.
    """

    predicate_ref: str = Field(
        description="Stable predicate reference (e.g., 'predicate.exclude_test_data'). "
        "Must start with 'predicate.'."
    )
    subject_ref: str = Field(
        description="Primary semantic subject this predicate constrains. "
        "Must use 'entity.*' or 'subject.*'."
    )
    predicate_contract_version: str = Field(
        description="Contract version (e.g., 'predicate.v1'). Must start with 'predicate.'."
    )

    @field_validator("predicate_ref")
    @classmethod
    def validate_predicate_ref_prefix(cls, v: str) -> str:
        return validate_ref_prefix(v, "predicate", "predicate_ref")

    @field_validator("subject_ref")
    @classmethod
    def validate_subject_ref_prefix(cls, v: str) -> str:
        if not (v.startswith("entity.") or v.startswith("subject.")):
            raise ValueError(
                "subject_ref must start with 'entity.' or 'subject.' prefix, got: " + v
            )
        return v

    @field_validator("predicate_contract_version")
    @classmethod
    def validate_version_prefix(cls, v: str) -> str:
        return validate_contract_version(v, "predicate")


# =============================================================================
# Predicate Expression
# =============================================================================

# Allowed target_ref prefixes for PredicateAtom
_ALLOWED_ATOM_TARGET_PREFIXES = frozenset(
    {
        "dimension.",
        "entity.",
        "key.",
        "enum.",
        "subject.",
        "population.",
        "event.",
        "field.",
    }
)

# Explicitly forbidden target_ref prefixes (error message specificity)
_FORBIDDEN_ATOM_TARGET_PREFIXES = frozenset(
    {
        "time.",
        "metric.",
        "process.",
        "binding.",
        "predicate.",
        "grain.",
        "measure.",
        "compiler_profile.",
    }
)

_SCALAR_VALUE_TYPES = (str, int, float, bool)
_ARRAY_VALUE_ITEM_TYPES = (str, int, float, bool, type(None))
PredicateValue = str | int | float | bool | None | list[str | int | float | bool | None]


class PredicateAtom(BaseModel):
    """Atomic predicate: a single filter condition on a governed semantic ref."""

    target_ref: str = Field(
        description="Governed semantic ref being filtered. "
        "Must use an allowed prefix (dimension., entity., key., etc.). "
        "Must not use 'time.*' — time filtering belongs in time_scope."
    )
    op: PredicateOperator = Field(
        description="Comparison operator. v1 whitelist: eq, neq, in, not_in, gt, gte, "
        "lt, lte, between, is_null, is_not_null."
    )
    value: PredicateValue = Field(
        default=None,
        description="Filter value. Required for most operators; must be None for "
        "is_null/is_not_null. Must be a 2-element list for 'between', "
        "non-empty list for 'in'/'not_in'.",
    )

    @field_validator("target_ref")
    @classmethod
    def validate_target_ref_prefix(cls, v: str) -> str:
        for prefix in _FORBIDDEN_ATOM_TARGET_PREFIXES:
            if v.startswith(prefix):
                if prefix == "time.":
                    raise ValueError(
                        "target_ref must not reference 'time.*' — "
                        "time filtering belongs in time_scope, got: " + v
                    )
                raise ValueError(f"target_ref must not use '{prefix}' prefix, got: " + v)
        for prefix in _ALLOWED_ATOM_TARGET_PREFIXES:
            if v.startswith(prefix):
                return v
        raise ValueError(
            "target_ref must start with a governed semantic ref prefix "
            "(dimension., entity., key., enum., subject., population., event., field.), "
            "got: " + v
        )

    @model_validator(mode="after")
    def validate_operator_value_constraints(self) -> PredicateAtom:
        op = self.op
        val = self.value

        # is_null / is_not_null: value must be None
        if op in ("is_null", "is_not_null"):
            if val is not None:
                raise ValueError(f"value must be None for operator '{op}', got: {val!r}")
            return self

        # between: must be list with exactly 2 elements
        if op == "between":
            if not isinstance(val, list):
                raise ValueError(
                    f"value must be a list for operator 'between', got: {type(val).__name__}"
                )
            if len(val) != 2:
                raise ValueError(
                    f"value must have exactly 2 elements for operator 'between', got {len(val)}"
                )
            return self

        # in / not_in: must be non-empty list
        if op in ("in", "not_in"):
            if not isinstance(val, list):
                raise ValueError(
                    f"value must be a list for operator '{op}', got: {type(val).__name__}"
                )
            if len(val) == 0:
                raise ValueError(f"value must be a non-empty list for operator '{op}'")
            return self

        # Scalar operators: value must be non-None scalar
        if val is None:
            raise ValueError(f"value is required for operator '{op}'")
        if isinstance(val, list):
            raise ValueError(f"value must be a scalar for operator '{op}', got list")
        return self


class PredicateConjunction(BaseModel):
    """Conjunctive predicate: AND-combined list of predicate expressions."""

    op: Literal["and"] = Field(description="Conjunction operator. v1 only supports 'and'.")
    items: list[PredicateExpression] = Field(
        min_length=1, description="One or more predicate expressions combined with AND."
    )


# Pydantic union discrimination for PredicateExpression
PredicateExpression = Annotated[
    PredicateConjunction | PredicateAtom,
    Field(discriminator="op"),
]


# =============================================================================
# Predicate Payload
# =============================================================================


class PredicatePayload(BaseModel):
    """Payload for a predicate object.

    Contains the filter expression, allowed usage contexts, and time policy.
    """

    expression: PredicateExpression = Field(
        description="The filter expression. v1 supports PredicateAtom and "
        "PredicateConjunction(op='and') only."
    )
    allowed_usage: list[PredicateUsage] = Field(
        min_length=1,
        description="Allowed consumption contexts. Must be non-empty. "
        "Values: metric_qualifier, carrier_row_filter, request_scope, governance_policy.",
    )
    time_policy: PredicateTimePolicy = Field(
        default="non_time_only", description="Time policy. v1 only supports 'non_time_only'."
    )
