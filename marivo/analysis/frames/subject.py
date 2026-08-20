"""Immutable typed subject artifacts and privacy-safe cohort bindings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta
from marivo.analysis.subject import SubjectSelection
from marivo.refs import RefPayloadV1, SemanticKind
from marivo.render import Card


class SubjectSetSourceBinding(BaseModel):
    """Exact persisted source artifact authority for one SubjectSet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_ref: str
    artifact_fingerprint: str

    @model_validator(mode="after")
    def _validate_source(self) -> SubjectSetSourceBinding:
        if not self.artifact_ref.strip() or not self.artifact_fingerprint.strip():
            raise ValueError("SubjectSet source artifact identity must be non-empty")
        return self


class SubjectCohortBinding(BaseModel):
    """Privacy-safe SubjectSet binding retained by a typed consumer artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_ref: str
    artifact_fingerprint: str
    subject_entity_ref: RefPayloadV1
    subject_identity: tuple[str, ...]
    source_artifact_ref: str
    selection_fingerprint: str

    @model_validator(mode="after")
    def _validate_binding(self) -> SubjectCohortBinding:
        if self.subject_entity_ref.kind is not SemanticKind.ENTITY:
            raise ValueError("SubjectCohortBinding requires an exact Entity ref")
        if not self.subject_identity or any(not item.strip() for item in self.subject_identity):
            raise ValueError("SubjectCohortBinding subject_identity must be non-empty")
        for field in (
            self.artifact_ref,
            self.artifact_fingerprint,
            self.source_artifact_ref,
            self.selection_fingerprint,
        ):
            if not field.strip():
                raise ValueError("SubjectCohortBinding identity fields must be non-empty")
        return self


class SubjectSetMeta(BaseFrameMeta):
    """Metadata for one persisted exact set of governed subject identities."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["subject_set"] = "subject_set"
    semantic_kind: Literal["subjects"] = "subjects"
    row_contract_version: Literal["subject-set-rows/v1"] = "subject-set-rows/v1"
    operator_version: Literal["select_subjects/v1"] = "select_subjects/v1"
    catalog_definition_fingerprint: str
    subject_entity_ref: RefPayloadV1
    subject_identity: tuple[str, ...]
    source: SubjectSetSourceBinding
    selection: SubjectSelection
    selection_fingerprint: str
    selected_count: int = Field(ge=0)
    excluded_coverage_censored_count: int = Field(ge=0)
    coverage_status: Literal["ready", "coverage_censored"]

    @model_validator(mode="after")
    def _validate_subject_set(self) -> SubjectSetMeta:
        if not self.catalog_definition_fingerprint.strip():
            raise ValueError("catalog_definition_fingerprint must be non-empty")
        if self.subject_entity_ref.kind is not SemanticKind.ENTITY:
            raise ValueError("SubjectSet subject_entity_ref must be an exact Entity ref")
        if not self.subject_identity or any(not item.strip() for item in self.subject_identity):
            raise ValueError("SubjectSet subject_identity must contain ordered components")
        if self.selection_fingerprint != self.selection.fingerprint:
            raise ValueError("selection_fingerprint must match the exact typed selection")
        if self.selected_count != self.row_count:
            raise ValueError("selected_count must equal row_count")
        expected_coverage = (
            "coverage_censored" if self.excluded_coverage_censored_count > 0 else "ready"
        )
        if self.coverage_status != expected_coverage:
            raise ValueError(
                f"coverage_status must be {expected_coverage!r} for excluded censored subjects"
            )
        return self

    def cohort_binding(self) -> SubjectCohortBinding:
        """Return the privacy-safe typed binding retained by consumer metadata."""
        if self.content_hash is None:
            raise ValueError("SubjectSet must be persisted before it can bind as a cohort")
        return SubjectCohortBinding(
            artifact_ref=self.ref,
            artifact_fingerprint=self.content_hash,
            subject_entity_ref=self.subject_entity_ref,
            subject_identity=self.subject_identity,
            source_artifact_ref=self.source.artifact_ref,
            selection_fingerprint=self.selection_fingerprint,
        )


def _identity_tuple(value: object) -> object:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        converted = tolist()
        if isinstance(converted, list):
            return tuple(converted)
    return value


def _identity_order_key(value: tuple[object, ...]) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)


@dataclass(repr=False)
class SubjectSet(BaseFrame):
    """Persisted exact subject identities for typed cohort composition.

    The only public data column is ``subject_identity``. It contains governed
    tuples ordered by the subject Entity primary-key signature. Metadata,
    cards, evidence, jobs, and consumer cohort bindings never copy those raw
    identity values.
    """

    meta: SubjectSetMeta

    _AVAILABLE_ENTRIES = (".show()", ".contract()", ".to_pandas()")

    def __post_init__(self) -> None:
        if "subject_identity" in self._df.columns:
            self._df["subject_identity"] = self._df["subject_identity"].map(_identity_tuple)
        super().__post_init__()
        self._validate_rows()

    def _validate_rows(self) -> None:
        if tuple(self._df.columns) != ("subject_identity",):
            raise ValueError("SubjectSet rows must contain only subject_identity")
        identities = self._df["subject_identity"].tolist()
        expected_components = len(self.meta.subject_identity)
        if any(
            not isinstance(identity, tuple)
            or len(identity) != expected_components
            or any(component is None for component in identity)
            for identity in identities
        ):
            raise ValueError(
                "SubjectSet subject_identity rows must match the ordered identity signature"
            )
        keys = tuple(_identity_order_key(identity) for identity in identities)
        if len(set(keys)) != len(keys):
            raise ValueError("SubjectSet subject_identity rows must be unique")
        if keys != tuple(sorted(keys)):
            raise ValueError("SubjectSet subject_identity rows must be deterministically ordered")

    def _repr_identity(self) -> str:
        return (
            f"SubjectSet ref={self.meta.ref} subject={self.meta.subject_entity_ref.path} "
            f"coverage={self.meta.coverage_status} rows={self.meta.row_count}"
        )

    def _card(self) -> Card:
        card = (
            self._header_card(
                f"coverage={self.meta.coverage_status} "
                f"selected={self.meta.selected_count} "
                f"excluded_censored={self.meta.excluded_coverage_censored_count}"
            )
            .field("subject", self.meta.subject_entity_ref.path)
            .field("identity_components", str(len(self.meta.subject_identity)))
            .field("source_artifact", self.meta.source.artifact_ref)
            .field("selection", self.meta.selection.kind)
            .listing("columns", ("subject_identity: governed identity tuple",))
        )
        self._append_evidence_sections(card)
        return card


__all__ = [
    "SubjectCohortBinding",
    "SubjectSet",
    "SubjectSetMeta",
    "SubjectSetSourceBinding",
]
