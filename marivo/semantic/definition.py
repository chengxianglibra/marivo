"""Safe, immutable descriptions of loaded semantic definitions.

These values describe declarations; they never execute expressions or certify data.
Nested variants are returned values, not authoring constructors or a second DSL.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo._temporal import Grain
from marivo.refs import (
    EntityKind,
    FieldKind,
    MeasureKind,
    MetricKind,
    Ref,
    RefPayloadV1,
    SemanticKindTag,
    TimeDimensionKind,
)
from marivo.render import Card, RenderableResult
from marivo.semantic.ir import AggKind, CumulativeAnchor, SourceLocation, TimeFoldIR, WhereValue

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class _Value(RenderableResult):
    def _repr_identity(self) -> str:
        return type(self).__name__.removeprefix("_")

    def _card(self) -> Card:
        return Card(identity=self._repr_identity(), available=(".show()",))


@dataclass(frozen=True, repr=False)
class _Column(_Value):
    entity: Ref[EntityKind]
    name: str
    kind: Literal["column"] = "column"


@dataclass(frozen=True, repr=False)
class _Field(_Value):
    ref: Ref[FieldKind]
    entity: Ref[EntityKind]
    kind: Literal["field"] = "field"


@dataclass(frozen=True, repr=False)
class _Literal(_Value):
    value_type: Literal["str", "int", "float", "bool", "none"]
    kind: Literal["redacted"] = "redacted"


@dataclass(frozen=True, repr=False)
class _Cast(_Value):
    operand: ExpressionNode
    data_type: str
    kind: Literal["cast"] = "cast"


@dataclass(frozen=True, repr=False)
class _Unary(_Value):
    operator: Literal["positive", "negative", "invert"]
    operand: ExpressionNode
    kind: Literal["unary"] = "unary"


@dataclass(frozen=True, repr=False)
class _Binary(_Value):
    operator: Literal[
        "+",
        "-",
        "*",
        "/",
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "&",
        "|",
        "^",
    ]
    left: ExpressionNode
    right: ExpressionNode
    kind: Literal["binary"] = "binary"


@dataclass(frozen=True, repr=False)
class _IfElse(_Value):
    condition: ExpressionNode
    when_true: ExpressionNode
    when_false: ExpressionNode
    kind: Literal["ifelse"] = "ifelse"


ExpressionNode: TypeAlias = _Column | _Field | _Literal | _Cast | _Unary | _Binary | _IfElse


@dataclass(frozen=True, repr=False)
class _SupportedExpression(_Value):
    expression: ExpressionNode
    status: Literal["supported"] = "supported"
    kind: Literal["expression"] = "expression"


@dataclass(frozen=True, repr=False)
class _UnsupportedExpression(_Value):
    reason: Literal["unsupported_syntax", "description_unavailable", "limit_exceeded"]
    status: Literal["unsupported"] = "unsupported"
    kind: Literal["expression"] = "expression"


ExpressionDescription: TypeAlias = _SupportedExpression | _UnsupportedExpression


@dataclass(frozen=True, repr=False)
class _Aggregate(_Value):
    operation: AggKind
    target: Ref[MeasureKind | EntityKind]
    filter: tuple[tuple[Ref[FieldKind], WhereValue], ...]
    kind: Literal["aggregate"] = "aggregate"


@dataclass(frozen=True, repr=False)
class _WeightedMean(_Value):
    value: Ref[MeasureKind]
    weight: Ref[MeasureKind]
    filter: tuple[tuple[Ref[FieldKind], WhereValue], ...]
    kind: Literal["weighted_mean"] = "weighted_mean"


@dataclass(frozen=True, repr=False)
class _Ratio(_Value):
    numerator: Ref[MetricKind]
    denominator: Ref[MetricKind]
    kind: Literal["ratio"] = "ratio"


@dataclass(frozen=True, repr=False)
class _Linear(_Value):
    terms: tuple[tuple[Literal["+", "-"], Ref[MetricKind]], ...]
    kind: Literal["linear"] = "linear"


@dataclass(frozen=True, repr=False)
class _Cumulative(_Value):
    base: Ref[MetricKind]
    over: Ref[TimeDimensionKind] | None
    anchor: CumulativeAnchor
    kind: Literal["cumulative"] = "cumulative"


DefinitionNode: TypeAlias = (
    _Aggregate | _WeightedMean | _Ratio | _Linear | _Cumulative | ExpressionDescription
)


@dataclass(frozen=True, repr=False)
class _TemporalRule(_Value):
    over: Ref[TimeDimensionKind]
    fold: TimeFoldIR


@dataclass(frozen=True, repr=False)
class _TemporalRules(_Value):
    declared: _TemporalRule | None = None
    override: TimeFoldIR | None = None
    effective: _TemporalRule | None = None
    source: Literal[
        "declared", "measure", "metric_override", "not_applicable", "context_required"
    ] = "not_applicable"


def _ref(value: Ref[SemanticKindTag]) -> dict[str, JsonValue]:
    return dict(RefPayloadV1.from_ref(value).to_dict())


def _expression(value: ExpressionNode) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {"kind": value.kind}
    if isinstance(value, _Column):
        result.update(entity=_ref(value.entity), name=value.name)
    elif isinstance(value, _Field):
        result.update(ref=_ref(value.ref), entity=_ref(value.entity))
    elif isinstance(value, _Literal):
        result["value_type"] = value.value_type
    elif isinstance(value, _Cast):
        result.update(operand=_expression(value.operand), data_type=value.data_type)
    elif isinstance(value, _Unary):
        result.update(operator=value.operator, operand=_expression(value.operand))
    elif isinstance(value, _Binary):
        result.update(
            operator=value.operator, left=_expression(value.left), right=_expression(value.right)
        )
    else:
        result.update(
            condition=_expression(value.condition),
            when_true=_expression(value.when_true),
            when_false=_expression(value.when_false),
        )
    return result


def _filters(values: tuple[tuple[Ref[FieldKind], WhereValue], ...]) -> list[JsonValue]:
    result: list[JsonValue] = []
    for dimension, value in values:
        if isinstance(value, tuple):
            result.append({"dimension": _ref(dimension), "operator": "in", "values": list(value)})
        else:
            result.append({"dimension": _ref(dimension), "operator": "eq", "value": value})
    return result


def _anchor(value: CumulativeAnchor) -> dict[str, JsonValue]:
    if value == "all_history":
        return {"kind": "all_history"}
    if value[0] == "trailing":
        return {"kind": "trailing", "count": value[1], "unit": value[2]}
    grain = value[1]
    if isinstance(grain, str):
        payload: dict[str, JsonValue] = {"kind": "builtin", "unit": grain, "count": 1}
    elif isinstance(grain, Grain) and grain.kind == "semantic" and grain.calendar is not None:
        payload = {"kind": "semantic", "calendar": _ref(grain.calendar), "level": grain.level}
    else:
        payload = {"kind": "builtin", "unit": grain.unit, "count": grain.count}
    return {"kind": "grain_to_date", "grain": payload}


def _node(value: DefinitionNode) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {"kind": value.kind}
    if isinstance(value, _Aggregate):
        operation = value.operation
        result.update(
            operation={"kind": operation[0], "q": operation[1]}
            if isinstance(operation, tuple)
            else {"kind": operation},
            target=_ref(value.target),
            target_kind=value.target.kind.value,
            filter=_filters(value.filter),
        )
    elif isinstance(value, _WeightedMean):
        result.update(
            value=_ref(value.value), weight=_ref(value.weight), filter=_filters(value.filter)
        )
    elif isinstance(value, _Ratio):
        result.update(numerator=_ref(value.numerator), denominator=_ref(value.denominator))
    elif isinstance(value, _Linear):
        result["terms"] = [{"sign": sign, "metric": _ref(ref)} for sign, ref in value.terms]
    elif isinstance(value, _Cumulative):
        result.update(
            base=_ref(value.base),
            over={"selection": "explicit", "ref": _ref(value.over)}
            if value.over is not None
            else {"selection": "default", "resolution": "context_required"},
            anchor=_anchor(value.anchor),
        )
    elif isinstance(value, _SupportedExpression):
        result.update(status=value.status, expression=_expression(value.expression))
    else:
        result.update(status=value.status, reason=value.reason)
    return result


def _fold(value: TimeFoldIR) -> dict[str, JsonValue]:
    return (
        {"kind": value.kind, "q": value.q} if value.kind == "percentile" else {"kind": value.kind}
    )


def _rule(value: _TemporalRule | None) -> dict[str, JsonValue]:
    return (
        {"status": "not_declared"}
        if value is None
        else {"status": "declared", "over": _ref(value.over), "fold": _fold(value.fold)}
    )


@dataclass(frozen=True, repr=False)
class SemanticDefinition(RenderableResult):
    """Read-only definition of one object in a loaded Catalog.

    Parameters are returned by ``entry.details().definition``: ``ref`` identifies
    the object, ``catalog_definition_fingerprint`` identifies its snapshot,
    ``node`` is its closed direct definition, ``source_location`` locates its
    declaration, and ``temporal`` separates declared and effective time rules.
    This is a description, not an executable formula or a readiness result.

    Example::

        definition = catalog.metrics.get('sales.revenue').details().definition
        definition.show()
        payload = definition.to_dict()
    """

    ref: Ref[SemanticKindTag]
    catalog_definition_fingerprint: str
    node: DefinitionNode
    source_location: SourceLocation
    temporal: _TemporalRules = _TemporalRules()

    def _repr_identity(self) -> str:
        return f"SemanticDefinition ref={self.ref.key} kind={self.node.kind}"

    def _card(self) -> Card:
        return Card(identity=self._repr_identity(), available=(".show()", ".to_dict()")).field(
            "definition", json.dumps(self.to_dict(), ensure_ascii=True, allow_nan=False)
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Return a fresh JSON-safe whitelist projection of this definition.

        No parameters. Returns a ``marivo.semantic_definition/v1`` mapping with
        exact RefPayloadV1 references and explicit availability states.
        Example: ``json.dumps(definition.to_dict(), allow_nan=False)``.
        Constraints: literals are redacted; no data, source text, SQL provenance,
        credentials, or connection configuration is exported. This does not query.
        """
        result: dict[str, JsonValue] = {
            "schema": "marivo.semantic_definition/v1",
            "ref": _ref(self.ref),
            "catalog_definition_fingerprint": self.catalog_definition_fingerprint,
            "source_location": {
                "file": self.source_location.file,
                "line": self.source_location.line,
            },
            "node": _node(self.node),
            "temporal": {
                "declared": _rule(self.temporal.declared),
                "override": {"status": "not_declared"}
                if self.temporal.override is None
                else {"status": "declared", "fold": _fold(self.temporal.override)},
                "effective": {"status": self.temporal.source}
                if self.temporal.effective is None
                else {
                    "status": "resolved",
                    "source": self.temporal.source,
                    "over": _ref(self.temporal.effective.over),
                    "fold": _fold(self.temporal.effective.fold),
                },
            },
        }
        _validate_json(result, self.ref, self.source_location)
        return result


def _validate_json(value: JsonValue, ref: Ref[SemanticKindTag], location: SourceLocation) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        from marivo.semantic.errors import SemanticDefinitionReadError

        raise SemanticDefinitionReadError(
            ref=ref.key, location=location, received="non-finite numeric definition parameter"
        )
    if isinstance(value, dict):
        for item in value.values():
            _validate_json(item, ref, location)
    elif isinstance(value, list):
        for item in value:
            _validate_json(item, ref, location)
