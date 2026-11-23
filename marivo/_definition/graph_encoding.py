"""Stable encodings of definitions, with native recipe tokens supplied by the caller."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import TypeAlias, TypeGuard

from ibis.common.deferred import Resolver

from marivo._definition.expressions import MetricExpression
from marivo._definition.graph_traversal import DefinitionNode, _is_node
from marivo._definition.observation_time import ContributionClockBinding
from marivo._definition.population_types import PredicateExpression
from marivo._definition.rows import RowExpression
from marivo._definition.types import (
    EntityIdentityRead,
    LiteralValue,
    literal_token,
)
from marivo._definition.windows import SampleWindowContext
from marivo._kernel.errors import invalid
from marivo._kernel.refs import Ref
from marivo._kernel.time_types import Grain as TemporalGrain
from marivo._kernel.time_types import InstantAnchor, grain_token


def canonical_value(value: object) -> object:
    """Convert supported typed payloads to deterministic JSON values."""
    if isinstance(value, Enum):
        return canonical_value(value.value)
    if isinstance(value, TemporalGrain):
        return grain_token(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            invalid("canonical payload rejects non-finite floats")
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: canonical_value(getattr(value, field.name))
            for field in fields(value)
            if field.name not in {"_from_default_time", "_context"}
        }
    if isinstance(value, (tuple, list)):
        return [canonical_value(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            invalid("canonical mappings require string keys")
        return {key: canonical_value(value[key]) for key in sorted(value)}
    invalid(f"unsupported canonical payload type: {type(value).__name__}")


def canonical_bytes(value: object) -> bytes:
    """Encode one supported payload as stable UTF-8 JSON bytes."""
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=False,
    ).encode("utf-8")


def fingerprint(value: object) -> str:
    """Return the lowercase SHA-256 fingerprint of canonical bytes."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


_PLAIN_LITERAL_TYPES = frozenset({str, bool, int, float, Decimal, date, datetime, InstantAnchor})
_EncodingPayloads: TypeAlias = dict[int, tuple[object, dict[str, object]]]

# Keep computation and observation identities stable when internal names change.
_DATACLASS_IDENTITY_TYPES: dict[type[object], str] = {
    ContributionClockBinding: "TimeBinding",
    EntityIdentityRead: "IdentityRead",
    SampleWindowContext: "SampleWindow",
}


def dataclass_identity_name(value: object) -> str:
    """Return the stable identity label for an internal dataclass value."""
    return _DATACLASS_IDENTITY_TYPES.get(type(value), type(value).__name__)


def _plain_literal(value: object) -> TypeGuard[LiteralValue]:
    return value is None or type(value) in _PLAIN_LITERAL_TYPES


def encoded_fingerprint(value: object) -> str:
    """Hash an already encoded definition without a second Python tree walk.

    Only values from the definition encoder are admitted here. Their keys
    and values are JSON-compatible; JSON's recursive key ordering matches the
    public canonical encoder while preserving its exact byte representation.
    """
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class DefinitionEncoder:
    """Encode one graph while sharing immutable payloads in that invocation."""

    def __init__(
        self, recipe_tokens: Callable[[Resolver, tuple[str, ...]], tuple[str, ...]]
    ) -> None:
        self._recipe_tokens = recipe_tokens
        self._memo: dict[int, str] = {}
        self._payloads: _EncodingPayloads = {}

    def encode(self, value: object) -> object:
        # Calendar cells and literal leaves do not need the full definition-node
        # dispatch. Exact types leave Enum and other subclass semantics unchanged.
        if _plain_literal(value):
            return list(literal_token(value))
        if type(value) is tuple:
            return [self.encode(item) for item in value]
        if _is_node(value):
            return self.fingerprint(value)
        if isinstance(value, Ref):
            return ["ref", value.key]
        if isinstance(value, timedelta):
            return ["timedelta", value.days, value.seconds, value.microseconds]
        if isinstance(value, TemporalGrain):
            return canonical_value(value)
        if value is None or isinstance(value, (str, int, float, Decimal, date)):
            return list(literal_token(value))
        if isinstance(value, tuple):
            return [self.encode(item) for item in value]
        if is_dataclass(value) and not isinstance(value, type):
            key = id(value)
            cached = self._payloads.get(key)
            if cached is not None:
                return cached[1]
            encoded = {
                "type": dataclass_identity_name(value),
                **{
                    field.name: self.encode(getattr(value, field.name))
                    for field in fields(value)
                    if field.name not in {"_from_default_time", "_context"}
                },
            }
            # Captured dataclasses are immutable. Keep the input alive against ID
            # reuse and share this read-only payload only until this encoding ends.
            self._payloads[key] = (value, encoded)
            return encoded
        invalid(f"unsupported definition value type: {type(value).__name__}")

    def fingerprint(self, node: DefinitionNode) -> str:
        cached = self._memo.get(id(node))
        if cached is None:
            cached = encoded_fingerprint(self.encode_node(node))
            self._memo[id(node)] = cached
        return cached

    def encode_node(self, node: DefinitionNode) -> dict[str, object]:
        if isinstance(node, (RowExpression, PredicateExpression, MetricExpression)):
            inputs = (
                node.inputs
                if isinstance(node, (RowExpression, PredicateExpression))
                else node.operands
            )
            return {
                "node": type(node).__name__,
                "recipe": self._recipe_tokens(
                    node.recipe, tuple(f"slot_{index}" for index in range(len(inputs)))
                ),
                "inputs": [self.encode(item) for item in inputs],
            }
        return {
            "node": type(node).__name__,
            **{
                field.name: self.encode(getattr(node, field.name))
                for field in fields(node)
                if field.name != "origins"
            },
        }
