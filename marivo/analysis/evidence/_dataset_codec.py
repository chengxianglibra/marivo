"""Closed canonical Finding-body codec; Store envelopes remain separate authority."""

from __future__ import annotations

import hashlib
from dataclasses import fields
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import UnionType
from typing import Literal, Union, get_args, get_origin, get_type_hints

from marivo.analysis.datasets import descriptors as d
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.materialization.contracts import (
    _array,
    _identity_payload,
    _obj,
    _text,
    canonical_json,
    digest,
    invalid,
    parse_json,
)
from marivo.analysis.refs import ArtifactRef
from marivo.refs import RefPayloadV1, SemanticKind

_BODY_FIELDS = "finding_type epistemic_kind subject coordinates canonical_item_key value derivation"
_VALUES = frozenset(
    {
        t.FindingCoordinateV1,
        t.AssociationFindingSubjectV1,
        t.MetricFindingSubjectV1,
        t.FunnelFindingSubjectV1,
        t.FindingDerivationV1,
        t.NoAssociationLagV1,
        t.AssociationLagV1,
        t.DefinedFindingRatioV1,
        t.UndefinedRelativeDeltaV1,
        t.UndefinedFindingShareV1,
        t.AssociationFindingValueV1,
        t.DeltaFindingValueV1,
        t.ContributionFindingValueV1,
        t.ForecastPointFindingValueV1,
        t.FunnelDeltaFindingValueV1,
    }
)


def _encode(value: object) -> object:
    if value is None or type(value) in (str, int, float, bool):
        return value
    if type(value) is Decimal:
        return {"scalar_kind": "decimal", "value": str(value)}
    if type(value) is datetime:
        return {"scalar_kind": "datetime", "value": value.isoformat()}
    if type(value) is date:
        return {"scalar_kind": "date", "value": value.isoformat()}
    if type(value) is tuple:
        return [_encode(item) for item in value]
    if type(value) is ArtifactRef:
        return {"ref": value.ref}
    if type(value) is d.DatasetFieldId:
        return {"value": value.value}
    if isinstance(value, d.DatasetFieldIdentity):
        return _identity_payload(value)
    if type(value) is RefPayloadV1:
        return value.to_dict()
    if type(value) in _VALUES and isinstance(value, t._Value):
        return {field.name: _encode(getattr(value, field.name)) for field in fields(value)}
    raise invalid("unsupported Finding body value")


def _identity(value: object) -> d.DatasetFieldIdentity:
    if not isinstance(value, dict):
        raise invalid("invalid Finding field identity")
    kind = value.get("kind")
    if kind == "catalog_ref":
        return d._catalog_identity(_text(_obj(value, "kind identity_id")["identity_id"]))
    if kind == "runtime_metric":
        return d._runtime_metric_identity(
            _text(_obj(value, "kind expression_fingerprint")["expression_fingerprint"])
        )
    if kind == "generated":
        return d._generated_identity(
            d._make_field_id(_text(_obj(value, "kind producer_field_id")["producer_field_id"]))
        )
    raise invalid("identity-bearing or unsupported Finding field identity")


def _decode(value: object, annotation: object) -> object:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        arguments = get_args(annotation)
        for candidate in arguments:
            if t._matches(value, candidate):
                return _decode(value, candidate)
            if isinstance(value, dict):
                if candidate in (Decimal, date, datetime):
                    tag = {Decimal: "decimal", date: "date", datetime: "datetime"}[candidate]
                    if value.get("scalar_kind") == tag:
                        return _decode(value, candidate)
                if isinstance(candidate, type) and candidate in _VALUES:
                    kind = get_args(get_type_hints(candidate).get("kind"))
                    if kind and value.get("kind") in kind:
                        return _decode(value, candidate)
        raise invalid("unknown Finding discriminated variant")
    if origin is Literal:
        if not t._matches(value, annotation):
            raise invalid("unknown Finding literal")
        return value
    if origin is tuple:
        return tuple(_decode(item, get_args(annotation)[0]) for item in _array(value))
    if annotation in (str, int, float, bool, type(None)):
        if not t._matches(value, annotation):
            raise invalid("Finding scalar does not match its exact type")
        return value
    if annotation in (Decimal, date, datetime):
        obj = _obj(value, "scalar_kind value")
        text = _text(obj["value"])
        if annotation is Decimal:
            try:
                number = Decimal(text)
            except InvalidOperation as exc:
                raise invalid("invalid Finding decimal") from exc
            if not number.is_finite() or obj["scalar_kind"] != "decimal" or str(number) != text:
                raise invalid("non-canonical Finding decimal")
            return number
        try:
            if annotation is datetime:
                timestamp = datetime.fromisoformat(text)
                if obj["scalar_kind"] != "datetime" or timestamp.isoformat() != text:
                    raise invalid("non-canonical Finding datetime")
                return timestamp
            day = date.fromisoformat(text)
            if obj["scalar_kind"] != "date" or day.isoformat() != text:
                raise invalid("non-canonical Finding date")
            return day
        except ValueError as exc:
            raise invalid("invalid Finding time scalar") from exc
    if annotation is ArtifactRef:
        text = _text(_obj(value, "ref")["ref"])
        ref = ArtifactRef(ref=text)
        if ref.ref != text:
            raise invalid("non-canonical Finding Artifact ref")
        return ref
    if annotation is d.DatasetFieldId:
        return d._make_field_id(_text(_obj(value, "value")["value"]))
    if annotation is d.DatasetFieldIdentity:
        return _identity(value)
    if annotation is RefPayloadV1:
        obj = _obj(value, "schema kind path")
        if obj["schema"] != "marivo.semantic_ref/v1" or obj["kind"] != "entity":
            raise invalid("invalid Funnel Entity subject ref")
        return RefPayloadV1(
            schema="marivo.semantic_ref/v1", kind=SemanticKind.ENTITY, path=_text(obj["path"])
        )
    if isinstance(annotation, type) and annotation in _VALUES:
        obj = _obj(value, " ".join(field.name for field in fields(annotation)))
        hints = get_type_hints(annotation)
        decoded: object = annotation(
            **{name: _decode(item, hints[name]) for name, item in obj.items()}
        )
        return decoded
    raise invalid("unsupported Finding codec annotation")


def encode_finding_body(finding: t.Finding) -> str:
    """Encode exactly the typed body, excluding Store identity, ownership and time."""
    if type(finding) is not t.Finding:
        raise invalid("unsupported Finding type")
    return canonical_json({name: _encode(getattr(finding, name)) for name in _BODY_FIELDS.split()})


def decode_finding_body(
    payload: str,
    *,
    finding_id: str,
    artifact_ref: str,
    session_id: str,
    committed_at: datetime,
) -> t.Finding:
    """Attach the authoritative selected Store envelope to one closed Finding body."""
    obj = _obj(parse_json(payload), _BODY_FIELDS)
    hints = get_type_hints(t.Finding)
    decoded = {name: _decode(item, hints[name]) for name, item in obj.items()}
    # The selected constructor is fixed; runtime annotation checks reject coercion.
    constructor: type[t._Value] = t.Finding
    value: object = constructor(
        **{
            **decoded,
            "finding_id": finding_id,
            "artifact_ref": ArtifactRef(ref=artifact_ref),
            "session_id": session_id,
            "committed_at": committed_at,
        }
    )
    if not isinstance(value, t.Finding):
        raise invalid("invalid Finding body constructor")
    return value


def finding_identity(finding: t.Finding) -> str:
    return digest(
        [
            finding.artifact_ref.ref,
            finding.finding_type,
            finding.canonical_item_key,
            finding.derivation.extractor_contract_version,
        ]
    )


def finding_set_member(finding: t.Finding, ordinal: int) -> dict[str, object]:
    return {
        "finding_ref": finding.finding_id,
        "finding_ordinal": ordinal,
        "finding_identity_digest": finding_identity(finding),
        "finding_body_payload": parse_json(encode_finding_body(finding)),
    }


def finding_set_digest(findings: tuple[t.Finding, ...]) -> str:
    hasher = hashlib.sha256()
    hasher.update(b"[")
    for ordinal, finding in enumerate(findings):
        if ordinal:
            hasher.update(b",")
        hasher.update(canonical_json(finding_set_member(finding, ordinal)).encode("utf-8"))
    hasher.update(b"]")
    return hasher.hexdigest()
