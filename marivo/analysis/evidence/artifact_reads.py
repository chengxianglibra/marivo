"""Artifact-owned public Finding reads."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from marivo.analysis._pages import _BoundedPage
from marivo.analysis.errors import (
    AnalysisRepair,
    EvidenceLimitError,
    EvidenceStoreUnavailableError,
    FindingNotFoundError,
)
from marivo.analysis.evidence.audit import get_finding, query_findings
from marivo.analysis.evidence.store import open_evidence_store
from marivo.analysis.evidence.types import (
    ArtifactDigest,
    DerivationRule,
    EpistemicKind,
    EvidenceSubject,
    FindingType,
    FindingValue,
)
from marivo.analysis.evidence.types import (
    Finding as PersistedFinding,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.render import Card, RenderableResult

if TYPE_CHECKING:
    from marivo.analysis.frames.base import BaseFrame


@dataclass(frozen=True, repr=False, slots=True, kw_only=True)
class Finding(RenderableResult):
    finding_id: str
    artifact_ref: str
    session_id: str
    finding_type: FindingType
    epistemic_kind: EpistemicKind
    subject: EvidenceSubject
    canonical_item_key: str
    value: FindingValue
    derivation: DerivationRule
    source_artifact_ref: str
    source_fields: tuple[str, ...]
    source_refs: tuple[str, ...]
    retained_digest_item_refs: tuple[str, ...]
    committed_at: datetime

    def _repr_identity(self) -> str:
        return f"Finding id={self.finding_id} type={self.finding_type} artifact={self.artifact_ref}"

    def _card(self) -> Card:
        return (
            Card(
                identity=self._repr_identity(),
                available=(".show()", ".value", ".derivation", ".source_refs"),
            )
            .status(self.epistemic_kind)
            .field("committed_at", self.committed_at.isoformat())
            .field("canonical_item_key", self.canonical_item_key)
            .field("source_artifact", self.source_artifact_ref)
            .listing("source_fields", self.source_fields or ("none",))
            .listing("source_refs", self.source_refs or ("none",))
            .listing(
                "retained_digest_items",
                self.retained_digest_item_refs or ("none",),
            )
        )


class FindingPage(_BoundedPage[Finding]):
    """Immutable bounded Artifact-scoped Finding page."""


def _ledger_path(artifact: BaseFrame) -> Path:
    return (
        Path(artifact.meta.project_root)
        / ".marivo"
        / "analysis"
        / "sessions"
        / artifact.meta.session_id
        / "judgment.db"
    )


def _retained_digest_refs(*, store: object, artifact_ref: str) -> dict[str, tuple[str, ...]]:
    connection = store.read()  # type: ignore[attr-defined]
    row = connection.execute(
        "SELECT digest_payload FROM artifact_digests WHERE artifact_id = ?",
        (artifact_ref,),
    ).fetchone()
    retained: dict[str, list[str]] = defaultdict(list)
    if row is not None:
        digest = ArtifactDigest.model_validate_json(row["digest_payload"])
        for item in digest.items:
            for finding_id in item.derivation.source_finding_refs:
                retained[finding_id].append(item.item_id)
    return {key: tuple(values) for key, values in retained.items()}


def _project(old: PersistedFinding, *, retained: dict[str, tuple[str, ...]]) -> Finding:
    finding_id = str(old.finding_id)
    artifact_ref = str(old.artifact_id)
    derivation = old.derivation
    return Finding(
        finding_id=finding_id,
        artifact_ref=artifact_ref,
        session_id=str(old.session_id),
        finding_type=old.finding_type,
        epistemic_kind=old.epistemic_kind,
        subject=old.subject,
        canonical_item_key=str(old.canonical_item_key),
        value=old.value,
        derivation=derivation,
        source_artifact_ref=artifact_ref,
        source_fields=tuple(derivation.source_fields),
        source_refs=tuple(old.source_refs),
        retained_digest_item_refs=retained.get(finding_id, ()),
        committed_at=old.committed_at,
    )


def findings(artifact: BaseFrame, *, limit: int = 20, cursor: str | None = None) -> FindingPage:
    if not 1 <= limit <= 100:
        raise EvidenceLimitError(
            message="Artifact findings limit must be within [1, 100]",
            context={
                "limit": limit,
                "location": "artifact.findings(...)",
                "help_target_id": "artifact.findings",
                "default_limit": 20,
            },
        )
    db_path = _ledger_path(artifact)
    if not db_path.is_file():
        raise EvidenceStoreUnavailableError(
            message="Evidence Store is unavailable for Artifact Finding reads",
            expected="a readable exact-current judgment.db",
            received="missing",
            location=str(db_path),
        )
    store = open_evidence_store(db_path)
    try:
        page = query_findings(
            store=store,
            session_id=artifact.meta.session_id,
            artifact_ref=artifact.ref,
            limit=limit,
            cursor=cursor,
        )
        retained = _retained_digest_refs(store=store, artifact_ref=artifact.ref)
        return FindingPage(
            items=tuple(_project(item, retained=retained) for item in page.items),
            limit=page.limit,
            has_more=page.has_more,
            next_cursor=page.next_cursor,
        )
    finally:
        store.close()


def finding(artifact: BaseFrame, finding_id: str) -> Finding:
    db_path = _ledger_path(artifact)
    if not db_path.is_file():
        raise EvidenceStoreUnavailableError(
            message="Evidence Store is unavailable for Artifact Finding reads",
            expected="a readable exact-current judgment.db",
            received="missing",
            location=str(db_path),
        )
    store = open_evidence_store(db_path)
    try:
        try:
            persisted = get_finding(store=store, finding_id=finding_id)
        except FindingNotFoundError:
            _not_found(artifact.ref, finding_id)
        if (
            persisted.session_id != artifact.meta.session_id
            or persisted.artifact_id != artifact.ref
        ):
            _not_found(artifact.ref, finding_id)
        retained = _retained_digest_refs(store=store, artifact_ref=artifact.ref)
        return _project(persisted, retained=retained)
    finally:
        store.close()


def _not_found(artifact_ref: str, finding_id: str) -> None:
    raise FindingNotFoundError(
        message=f"Finding {finding_id!r} does not exist on Artifact {artifact_ref!r}",
        expected="an exact Finding id owned by this Artifact",
        received=finding_id,
        location="artifact.finding(finding_id)",
        repair=AnalysisRepair(
            kind="inspect",
            action="Read the owning Artifact's bounded Finding page and retry one returned id.",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="artifact.findings"),
            snippet="page = artifact.findings(limit=20)",
        ),
    )


__all__ = ["Finding", "FindingPage", "finding", "findings"]
