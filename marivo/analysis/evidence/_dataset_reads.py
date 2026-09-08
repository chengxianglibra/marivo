"""Selected v3 Finding reads and a separate complete-set integrity inspection."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from marivo.analysis._pages import decode_keyset_cursor, encode_keyset_cursor
from marivo.analysis.datasets import descriptors as d
from marivo.analysis.errors import AnalysisRepair, FindingNotFoundError
from marivo.analysis.evidence import _dataset_types as t
from marivo.analysis.evidence._dataset_codec import (
    decode_finding_body,
    finding_identity,
    finding_set_member,
)
from marivo.analysis.materialization.contracts import ArtifactRecord, canonical_json, invalid
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.refs import ArtifactRef
from marivo.introspection.live.model import LiveHelpTarget


@dataclass(frozen=True, slots=True)
class CoordinateRule:
    """One exact field admitted by its producer/extractor's coordinate contract."""

    field: d.DatasetField
    kind: Literal["dimension", "time", "comparison_ordinal", "step"]
    decomposition_axis_index: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("dimension", "time", "comparison_ordinal", "step"):
            raise invalid("unregistered Finding coordinate class")
        if self.field.identity.kind == "entity_identity" or self.field.role_id in (
            "entity_identity",
            "entity_key",
            "subject_identity",
            "subject_key",
        ):
            raise invalid("identity-bearing Finding coordinate registration")
        if self.kind == "dimension" and not (
            isinstance(self.field.identity, d._CatalogFieldIdentity)
            and self.field.identity.identity_id.startswith("dimension:")
            and self.field.role_id == "dimension"
        ):
            raise invalid("Finding Dimension must be a retained governed Dimension")
        if self.decomposition_axis_index is not None:
            t._count(self.decomposition_axis_index)
            if self.kind != "dimension":
                raise invalid("only a governed Dimension is a decomposition axis")


@dataclass(frozen=True, slots=True)
class FindingRegistration:
    """Exact owner-supplied contract; no production nonzero extractor is registered yet."""

    producer_id: str
    extractor_contract_id: str
    extractor_contract_version: str
    shape_id: d.DatasetShapeId
    finding_type: t.FindingType
    subject: t.FindingSubjectV1
    coordinates: tuple[CoordinateRule, ...]
    source_artifact_refs: tuple[ArtifactRef, ...]
    source_fields: tuple[d.DatasetFieldId, ...]
    canonical_item_key: Callable[[t.Finding], str]
    contribution_method: str | None = None

    def __post_init__(self) -> None:
        for value in (
            self.producer_id,
            self.extractor_contract_id,
            self.extractor_contract_version,
        ):
            t._text(value)
        if type(self.coordinates) is not tuple or len(
            {item.field.field_id for item in self.coordinates}
        ) != len(self.coordinates):
            raise invalid("non-canonical coordinate registration")
        indices = tuple(
            item.decomposition_axis_index
            for item in self.coordinates
            if item.decomposition_axis_index is not None
        )
        if indices != tuple(range(len(indices))):
            raise invalid("decomposition axes do not follow their authored order")


def evidence_digest(record: ArtifactRecord) -> t.ArtifactDigest:
    evidence = record.evidence
    return t.ArtifactDigest(
        artifact_ref=ArtifactRef(ref=record.artifact_ref),
        quality_summary_digest=evidence.quality_summary_digest,
        typed_issue_digest=evidence.typed_issue_digest,
        evidence_digest=evidence.evidence_digest,
        finding_count=evidence.finding_count,
        finding_set_digest=evidence.finding_set_digest,
        extractor_contract_versions=evidence.extractor_contract_versions,
    )


def _selection_error() -> MaterializationError:
    return MaterializationError(
        expected="an Artifact-scoped Finding page with limit from 1 to 100 and its own cursor",
        received="invalid Finding selection",
        repair="Call findings(limit=20) on the selected Artifact, then use that page's next_cursor.",
        stage="presentation",
    )


def _registration(record: ArtifactRecord, registration: FindingRegistration | None) -> None:
    contract = record.descriptor.dataset_materialization_contract
    if registration is None:
        if contract.finding_policy_id != "zero_findings@v1" or record.evidence.finding_count != 0:
            raise invalid("no registered nonzero Finding extractor for this Artifact")
        return
    if (
        registration.producer_id != contract.producer_id
        or registration.extractor_contract_id != contract.finding_extractor_id
        or registration.extractor_contract_version != str(contract.finding_extractor_version)
        or registration.shape_id != record.descriptor.row_contract.shape_id
        or record.evidence.finding_count > 1000
    ):
        raise invalid("Finding registration does not match its producing Artifact")
    columns = {field.field_id: field for field in record.descriptor.realized_schema.columns}
    if any(columns.get(rule.field.field_id) != rule.field for rule in registration.coordinates):
        raise invalid("Finding coordinate is not the exact retained Artifact field")


def _scalar_matches(value: t.Scalar, field: d.DatasetField) -> bool:
    logical = field.logical_type_id
    if logical in ("boolean", "bool"):
        return type(value) is bool
    if logical == "integer" or logical.startswith(("int", "uint")):
        return type(value) is int
    if logical == "floating" or logical.startswith("float"):
        return type(value) is float
    if logical.startswith("decimal"):
        return type(value) is Decimal
    if logical == "string":
        return type(value) is str
    if logical == "date":
        return type(value) is date
    if logical.startswith(("timestamp", "datetime")):
        return type(value) is datetime
    return False


def _coordinate(value: t.FindingCoordinateV1, rule: CoordinateRule, finding: t.Finding) -> None:
    if value.field_id != rule.field.field_id or value.identity != rule.field.identity:
        raise invalid("Finding coordinate field identity mismatch")
    contribution = finding.value
    index = rule.decomposition_axis_index
    if index is not None:
        if not isinstance(contribution, t.ContributionFindingValueV1):
            raise invalid("decomposition coordinate outside Contribution Finding")
        active = contribution.active_axis_mask[index]
        other = contribution.other_mask[index]
        if not active or other:
            if value.value is not None or not rule.field.nullable:
                raise invalid("inactive or Other Dimension cell contradicts its exact row contract")
            return
    if value.value is None:
        if rule.kind != "dimension" or not rule.field.nullable:
            raise invalid("unregistered null Finding coordinate")
    elif not _scalar_matches(value.value, rule.field):
        raise invalid("Finding coordinate scalar does not match its logical type")


def _validate(
    finding: t.Finding, record: ArtifactRecord, registration: FindingRegistration
) -> None:
    derivation = finding.derivation
    if (
        finding.finding_id != finding_identity(finding)
        or finding.canonical_item_key != registration.canonical_item_key(finding)
        or finding.finding_type != registration.finding_type
        or finding.subject != registration.subject
        or derivation.producer_id != registration.producer_id
        or derivation.extractor_contract_id != registration.extractor_contract_id
        or derivation.extractor_contract_version != registration.extractor_contract_version
        or derivation.source_artifact_refs != registration.source_artifact_refs
        or derivation.source_fields != registration.source_fields
        or len(finding.coordinates) != len(registration.coordinates)
    ):
        raise invalid("Finding body contradicts its exact producer/extractor registration")
    if isinstance(finding.value, t.ContributionFindingValueV1):
        axes = sum(rule.decomposition_axis_index is not None for rule in registration.coordinates)
        if len(finding.value.active_axis_mask) != axes or (
            finding.value.method != registration.contribution_method
        ):
            raise invalid("Contribution mask or method contradicts its registration")
    for coordinate, rule in zip(finding.coordinates, registration.coordinates, strict=True):
        _coordinate(coordinate, rule, finding)
    if finding.artifact_ref.ref != record.artifact_ref or finding.session_id != record.session_ref:
        raise invalid("Finding Store ownership mismatch")


def _row(
    row: sqlite3.Row,
    record: ArtifactRecord,
    registration: FindingRegistration | None,
) -> t.Finding:
    if registration is None:
        raise invalid("Finding row exists under a zero-Finding production contract")
    ordinal: object = row["finding_ordinal"]
    if type(ordinal) is not int or not 0 <= ordinal < record.evidence.finding_count:
        raise invalid("selected Finding ordinal is outside its committed envelope")
    identity: object = row["finding_ref"]
    payload: object = row["finding_body_payload"]
    identity_digest: object = row["finding_identity_digest"]
    if type(identity) is not str or type(payload) is not str or identity_digest != identity:
        raise invalid("invalid selected Finding Store envelope")
    try:
        committed_at = datetime.fromisoformat(record.committed_at)
    except ValueError as exc:
        raise invalid("invalid Finding commit timestamp") from exc
    finding = decode_finding_body(
        payload,
        finding_id=identity,
        artifact_ref=record.artifact_ref,
        session_id=record.session_ref,
        committed_at=committed_at,
    )
    _validate(finding, record, registration)
    return finding


def findings(
    conn: sqlite3.Connection,
    record: ArtifactRecord,
    *,
    limit: int = 20,
    cursor: str | None = None,
    registration: FindingRegistration | None = None,
) -> t.FindingPage:
    """Select one Artifact page before decoding; a lookahead reads identities only."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise _selection_error()
    ordinal = -1
    if cursor is not None:
        if type(cursor) is not str or len(cursor) > 4096:
            raise _selection_error()
        decoded: tuple[str | int, str] | None = None
        with suppress(ValueError, RecursionError):
            decoded = decode_keyset_cursor(cursor)
        if decoded is None:
            raise _selection_error()
        position, owner = decoded
        if type(position) is not int or position < 0 or owner != record.artifact_ref:
            raise _selection_error()
        if encode_keyset_cursor(position, owner) != cursor:
            raise _selection_error()
        ordinal = position
    _registration(record, registration)
    selected = conn.execute(
        "SELECT finding_ref,finding_ordinal FROM findings "
        "WHERE artifact_ref=? AND finding_ordinal>? ORDER BY finding_ordinal LIMIT ?",
        (record.artifact_ref, ordinal, limit + 1),
    ).fetchall()
    if registration is None and selected:
        raise invalid("Finding row exists under a zero-Finding production contract")
    items: list[t.Finding] = []
    for identity in selected[:limit]:
        row = conn.execute(
            "SELECT finding_ref,finding_ordinal,finding_identity_digest,finding_body_payload FROM findings "
            "WHERE artifact_ref=? AND finding_ref=?",
            (record.artifact_ref, identity["finding_ref"]),
        ).fetchone()
        if row is None:
            raise invalid("selected Finding disappeared within one snapshot")
        items.append(_row(row, record, registration))
    more = len(selected) > limit
    return t.FindingPage(
        items=tuple(items),
        limit=limit,
        has_more=more,
        next_cursor=encode_keyset_cursor(
            selected[limit - 1]["finding_ordinal"], record.artifact_ref
        )
        if more
        else None,
    )


def finding(
    conn: sqlite3.Connection,
    record: ArtifactRecord,
    finding_id: str,
    *,
    registration: FindingRegistration | None = None,
) -> t.Finding:
    """Decode one exact Artifact-owned Finding, without inspecting adjacent bodies."""
    if type(finding_id) is not str or not finding_id or len(finding_id) > 4096:
        raise _selection_error()
    _registration(record, registration)
    row = conn.execute(
        "SELECT finding_ref,finding_ordinal,finding_identity_digest,finding_body_payload FROM findings "
        "WHERE artifact_ref=? AND finding_ref=?",
        (record.artifact_ref, finding_id),
    ).fetchone()
    if row is None:
        raise FindingNotFoundError(
            message="Finding is absent from the selected Artifact.",
            expected="a Finding committed by this exact Artifact",
            received="no matching Artifact-owned Finding",
            location="artifact.finding(finding_id)",
            repair=AnalysisRepair(
                kind="inspect",
                action="Call findings() on the selected Artifact and use one returned finding_id.",
                help_target=LiveHelpTarget(surface="analysis", canonical_id="artifact.findings"),
                snippet="page = artifact.findings(limit=20)",
            ),
        )
    return _row(row, record, registration)


def audit_findings(
    conn: sqlite3.Connection,
    record: ArtifactRecord,
    *,
    registration: FindingRegistration | None = None,
    check: Callable[[], None] | None = None,
) -> None:
    """Stream the complete ordinal set and validate count, bodies, and canonical digest."""
    _registration(record, registration)
    if check is not None:
        check()
    rows = conn.execute(
        "SELECT finding_ref,finding_ordinal,finding_identity_digest,finding_body_payload "
        "FROM findings WHERE artifact_ref=? ORDER BY finding_ordinal",
        (record.artifact_ref,),
    )
    hasher = hashlib.sha256()
    hasher.update(b"[")
    count = 0
    for row in rows:
        if check is not None:
            check()
        if type(row["finding_ordinal"]) is not int or row["finding_ordinal"] != count:
            raise invalid("Finding ordinal set is not contiguous from zero")
        value = _row(row, record, registration)
        if count:
            hasher.update(b",")
        hasher.update(canonical_json(finding_set_member(value, count)).encode("utf-8"))
        count += 1
    hasher.update(b"]")
    if count != record.evidence.finding_count:
        raise invalid("Finding count differs from its committed Evidence envelope")
    if hasher.hexdigest() != record.evidence.finding_set_digest:
        raise invalid("Finding set digest differs from its committed Evidence envelope")
