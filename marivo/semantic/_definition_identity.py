"""Canonical identity for one compiled semantic definition graph."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import PurePath

from marivo.refs import Ref, RefPayloadV1, SemanticKindTag
from marivo.semantic._expression_binding import CompiledExpressionSidecar

_EXCLUDED_FIELDS = frozenset({"location", "python_symbol", "file", "line"})


def _canonical(value: object) -> object:
    """Return a JSON-safe canonical value without process-local provenance."""
    if type(value) is Ref:
        return RefPayloadV1.from_ref(value).to_dict()
    if value is None or type(value) in {str, int, float, bool}:
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, PurePath):
        raise TypeError("absolute filesystem paths are not semantic definition identity")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _canonical(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if field.name not in _EXCLUDED_FIELDS
        }
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        canonical_items = [_canonical(item) for item in value]
        return sorted(
            canonical_items,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    raise TypeError(f"unsupported semantic definition identity value: {type(value).__name__}")


def definition_fingerprint(
    *,
    selected_root_roles: Sequence[str],
    filtered_domains: Sequence[str],
    definitions: Mapping[Ref[SemanticKindTag], object],
    dependencies: Mapping[Ref[SemanticKindTag], tuple[Ref[SemanticKindTag], ...]],
    sidecar: CompiledExpressionSidecar,
) -> str:
    """Hash the canonical compiled graph, excluding paths and runtime state."""
    records: list[dict[str, object]] = []
    for ref in sorted(definitions, key=lambda item: item.key):
        body = sidecar.bodies.get(ref)
        records.append(
            {
                "ref": RefPayloadV1.from_ref(ref).to_dict(),
                "definition": _canonical(definitions[ref]),
                "dependencies": [
                    RefPayloadV1.from_ref(dependency).to_dict()
                    for dependency in dependencies.get(ref, ())
                ],
                "expression": (
                    {
                        "body_ast_hash": body.body_ast_hash,
                        "parameter_count": body.parameter_count,
                        "bindings": [
                            {
                                "field_ref": binding.field_ref.to_dict(),
                                "entity_position": binding.entity_position,
                            }
                            for binding in body.bindings
                        ],
                    }
                    if body is not None
                    else None
                ),
            }
        )
    payload = {
        "schema": "marivo.semantic_definition_graph/v1",
        "selected_root_roles": list(selected_root_roles),
        "filtered_domains": sorted(filtered_domains),
        "definitions": records,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
