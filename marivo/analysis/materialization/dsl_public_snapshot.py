"""Canonical, closed continuation snapshot for the admitted public J1–J4 slice."""

from __future__ import annotations

import json
import math
from dataclasses import fields, is_dataclass
from enum import Enum

from marivo.analysis.datasets.descriptors import (
    _CORE_TOKEN,
    DatasetFieldId,
    _Descriptor,
    _DifferenceQuantity,
    _EntityDomain,
    _GroupDomain,
    _make_shape_id,
    _ObservedQuantity,
    _RowStatisticQuantity,
    _SelectedEntityDomain,
    _SingletonDomain,
)
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.analysis.datasets.handles import (
    CanonicalValue,
    DefinitionInput,
    LogicalInputToken,
    LogicalRootHandle,
    _make_logical_root,
)
from marivo.analysis.materialization.dsl_j1_artifact import J1Node, _context
from marivo.analysis.observation.contracts import ContractEvidence, MetricComponentPlan
from marivo.analysis.observation.dsl_j1 import (
    _IDS,
    FrozenDimensionRowFacts,
    FrozenEntityRowFacts,
    J1Context,
    J1Difference,
    J1Group,
    J1Members,
    J1Observed,
    J1Read,
    J1SelectedCategory,
    J1SelectedDifference,
    J1Statistic,
    J3Observed,
    J4Association,
    J4CoefficientSelection,
    J4CoefficientStatistic,
)
from marivo.refs import Ref, RefPayloadV1, SemanticKind, _create_ref
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.ir import TargetDimensionContract
from marivo.semantic.validator import Registry, normalize_target_dimension, normalize_target_entity

_SCHEMA = "marivo.analysis.public_continuation/v1"
_NODE_TYPES = (
    J1Members,
    J1Read,
    J1SelectedCategory,
    J1Group,
    J1Observed,
    J3Observed,
    J1Statistic,
    J1Difference,
    J1SelectedDifference,
    J4Association,
    J4CoefficientSelection,
    J4CoefficientStatistic,
)
_CLASSES: dict[str, type[object]] = {
    item.__name__: item
    for item in (
        *_NODE_TYPES,
        _EntityDomain,
        _SelectedEntityDomain,
        _GroupDomain,
        _SingletonDomain,
        _ObservedQuantity,
        _RowStatisticQuantity,
        _DifferenceQuantity,
        DatasetFieldId,
        TargetDimensionContract,
        ContractEvidence,
        MetricComponentPlan,
        RefPayloadV1,
    )
}


def _invalid(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="a canonical v1 public continuation snapshot bound to this Artifact",
        received=received,
        repair="Recover the exact public Artifact; do not reconstruct a result from displayed rows.",
        location="analysis.artifact",
    )


def _encode(value: object) -> object:
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _invalid("non-finite snapshot value")
        return {"float": value.hex()}
    if isinstance(value, Enum):
        if not isinstance(value, SemanticKind):
            raise _invalid("unsupported enum")
        return {"semantic_kind": value.value}
    if type(value) is Ref:
        return {"ref": [value.kind.value, value.path]}
    if isinstance(value, tuple):
        return {"tuple": [_encode(item) for item in value]}
    if isinstance(value, J1Context):
        return {"context": True}
    if isinstance(value, LogicalRootHandle):
        if value.realizations or value.payload is not None:
            raise _invalid("noncanonical J1 root")
        return {
            "root": {
                "shape": [
                    value.shape_id.family_id,
                    value.shape_id.local_shape_id,
                    value.shape_id.semantic_version,
                ],
                "row": value.row_contract_fingerprint,
                "rows": value.row_set_contract_fingerprint,
                "fingerprint": value.definition_fingerprint,
                "operator": value.operator_id,
                "parameters": _encode(value.parameters),
                "dependencies": _encode(value.dependency_facts),
                "versions": _encode(value.contract_versions),
                "requirements": _encode(value.requirements),
                "inputs": [[item.role, _encode(item.root)] for item in value.inputs],
            }
        }
    if is_dataclass(value) and type(value).__name__ in _CLASSES:
        return {
            "class": type(value).__name__,
            "fields": {
                item.name: _encode(getattr(value, item.name)) for item in fields(value) if item.init
            },
        }
    raise _invalid(f"unsupported snapshot value {type(value).__name__}")


def _required_map(value: object, keys: frozenset[str]) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or any(type(key) is not str for key in value)
    ):
        raise _invalid("wrong snapshot fields")
    return value


def _canonical_value(value: object) -> CanonicalValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, tuple):
        return tuple(_canonical_value(item) for item in value)
    raise _invalid("noncanonical root parameter")


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
        raise _invalid("invalid root string facts")
    return value


def _version_tuple(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, tuple) or not all(
        isinstance(item, tuple) and len(item) == 2 and all(isinstance(part, str) for part in item)
        for item in value
    ):
        raise _invalid("invalid root versions")
    return value


def _decode(value: object, context: J1Context, roots: dict[str, LogicalRootHandle]) -> object:
    if value is None or type(value) in (str, int, bool):
        return value
    if not isinstance(value, dict):
        raise _invalid("invalid snapshot value")
    if set(value) == {"float"} and isinstance(value["float"], str):
        number = float.fromhex(value["float"])
        if not math.isfinite(number):
            raise _invalid("non-finite snapshot float")
        return number
    if set(value) == {"semantic_kind"} and isinstance(value["semantic_kind"], str):
        return SemanticKind(value["semantic_kind"])
    if set(value) == {"ref"}:
        raw = value["ref"]
        if (
            not isinstance(raw, list)
            or len(raw) != 2
            or not all(isinstance(item, str) for item in raw)
        ):
            raise _invalid("invalid Ref")
        return _create_ref(SemanticKind(raw[0]), raw[1])
    if set(value) == {"tuple"}:
        raw = value["tuple"]
        if not isinstance(raw, list):
            raise _invalid("invalid tuple")
        return tuple(_decode(item, context, roots) for item in raw)
    if set(value) == {"context"} and value["context"] is True:
        return context
    if set(value) == {"root"}:
        raw = _required_map(
            value["root"],
            frozenset(
                {
                    "shape",
                    "row",
                    "rows",
                    "fingerprint",
                    "operator",
                    "parameters",
                    "dependencies",
                    "versions",
                    "requirements",
                    "inputs",
                }
            ),
        )
        fingerprint = raw["fingerprint"]
        if not isinstance(fingerprint, str):
            raise _invalid("missing definition fingerprint")
        if fingerprint in roots:
            return roots[fingerprint]
        shape = raw["shape"]
        inputs = raw["inputs"]
        if not isinstance(shape, list) or len(shape) != 3 or not isinstance(inputs, list):
            raise _invalid("invalid root shape or inputs")
        children: list[DefinitionInput] = []
        for entry in inputs:
            if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str):
                raise _invalid("invalid root input")
            child = _decode(entry[1], context, roots)
            if not isinstance(child, LogicalRootHandle):
                raise _invalid("invalid logical predecessor")
            children.append(
                DefinitionInput(entry[0], LogicalInputToken(child.definition_fingerprint), child)
            )
        parameters = _decode(raw["parameters"], context, roots)
        dependencies = _decode(raw["dependencies"], context, roots)
        versions = _decode(raw["versions"], context, roots)
        requirements = _decode(raw["requirements"], context, roots)
        if not all(
            isinstance(item, tuple) for item in (parameters, dependencies, versions, requirements)
        ):
            raise _invalid("invalid root parameters")
        if (
            not isinstance(raw["row"], str)
            or not isinstance(raw["rows"], str)
            or not isinstance(raw["operator"], str)
        ):
            raise _invalid("invalid root contract")
        root = _make_logical_root(
            session_id=context.session_id,
            store_id=context.store_id,
            shape_id=_make_shape_id(shape[0], shape[1], shape[2], ids=_IDS),
            row_contract_fingerprint=raw["row"],
            row_set_contract_fingerprint=raw["rows"],
            operator_id=raw["operator"],
            inputs=tuple(children),
            parameters=_canonical_value(parameters),
            dependency_facts=_string_tuple(dependencies),
            contract_versions=_version_tuple(versions),
            requirements=_string_tuple(requirements),
        )
        if root.definition_fingerprint != fingerprint:
            raise _invalid("definition fingerprint drift")
        roots[fingerprint] = root
        return root
    if set(value) == {"class", "fields"}:
        name = value["class"]
        raw_fields = value["fields"]
        if not isinstance(name, str) or not isinstance(raw_fields, dict) or name not in _CLASSES:
            raise _invalid("unknown continuation variant")
        cls = _CLASSES[name]
        if not is_dataclass(cls):
            raise _invalid("invalid continuation class")
        expected = {item.name for item in fields(cls) if item.init}
        if set(raw_fields) != expected:
            raise _invalid("wrong variant fields")
        decoded = {key: _decode(item, context, roots) for key, item in raw_fields.items()}
        if issubclass(cls, _Descriptor):
            decoded["_token"] = _CORE_TOKEN
        return cls(**decoded)
    raise _invalid("unknown snapshot tag")


def encode_public_node(node: J1Node) -> str:
    """Encode an admitted public node and only the row facts needed to continue it."""
    context = _context(node)
    root = node.root
    while root.inputs:
        predecessor = root.inputs[0].root
        if not isinstance(predecessor, LogicalRootHandle):
            raise _invalid("materialized root inside definition")
        root = predecessor
    if (
        not isinstance(root.parameters, tuple)
        or not root.parameters
        or not isinstance(root.parameters[0], str)
    ):
        raise _invalid("missing member Entity")
    member_path = root.parameters[0]
    entity = (
        normalize_target_entity(context.registry, member_path)
        if context.row_entity is None
        else context.row_entity
    )
    row_entity = FrozenEntityRowFacts(member_path, entity.primary_key, entity.identity_signature)
    dimension_paths: set[str] = set()
    visited: set[LogicalRootHandle] = set()

    def collect(candidate: LogicalRootHandle) -> None:
        if candidate in visited:
            return
        visited.add(candidate)

        def strings(item: object) -> None:
            if isinstance(item, str) and item in context.registry.dimensions:
                dimension_paths.add(item)
            elif isinstance(item, tuple):
                for part in item:
                    strings(part)

        strings(candidate.parameters)
        for child in candidate.inputs:
            if isinstance(child.root, LogicalRootHandle):
                collect(child.root)

    collect(node.root)
    dimensions = (
        tuple(
            FrozenDimensionRowFacts(
                path, normalize_target_dimension(context.registry, path).logical_type
            )
            for path in sorted(dimension_paths)
        )
        if not context.row_dimensions
        else context.row_dimensions
    )
    payload = {
        "schema": _SCHEMA,
        "entity": [
            row_entity.path,
            list(row_entity.primary_key),
            [list(item) for item in row_entity.identity_signature],
        ],
        "dimensions": [[item.path, item.logical_type] for item in dimensions],
        "node": _encode(node),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(serialized.encode("utf-8")) > 262144:
        raise _invalid("oversized public continuation snapshot")
    return serialized


def decode_public_node(text: str, session_id: str, store_id: str) -> J1Node:
    """Rebuild a validated source-free node without consulting current semantics."""
    try:
        payload = json.loads(text)
        raw = _required_map(payload, frozenset({"schema", "entity", "dimensions", "node"}))
        if (
            raw["schema"] != _SCHEMA
            or json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True) != text
        ):
            raise _invalid("unsupported or noncanonical snapshot")
        entity = raw["entity"]
        dimensions = raw["dimensions"]
        if not isinstance(entity, list) or len(entity) != 3 or not isinstance(dimensions, list):
            raise _invalid("invalid frozen row facts")
        frozen_entity = FrozenEntityRowFacts(
            entity[0], tuple(entity[1]), tuple(tuple(item) for item in entity[2])
        )
        frozen_dimensions = tuple(FrozenDimensionRowFacts(item[0], item[1]) for item in dimensions)
        context = J1Context(
            Registry(),
            CompiledExpressionSidecar({}, {}, frozenset()),
            session_id,
            store_id,
            frozen_entity,
            frozen_dimensions,
        )
        node = _decode(raw["node"], context, {})
        if not isinstance(node, _NODE_TYPES):
            raise _invalid("unsupported public root")
        return node
    except (TypeError, ValueError, KeyError, IndexError, AttributeError) as exc:
        raise _invalid("malformed continuation snapshot") from exc
