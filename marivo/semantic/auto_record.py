"""Auto-record authoring decisions in the evidence ledger.

When a developer declares @ms.metric or @ms.time_dimension in _domain.py,
the declaration itself constitutes a decision. This module records
corresponding DecisionRecords in the evidence ledger so the readiness
gate sees them without requiring the propose-answer loop."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from marivo.semantic.classifier import DecisionKind, floor_for
from marivo.semantic.ir import DimensionIR, MetricIR
from marivo.semantic.ledger import DecisionRecord, LedgerStore, ObjectEvidence

_AUTHORING_QUALIFYING_SOURCE = "authoring_declaration"


def _authoring_fingerprint_for_metric(ir: MetricIR) -> str:
    payload = {
        "semantic_id": ir.semantic_id,
        "decomposition_kind": ir.decomposition.kind,
        "decomposition_components": dict(ir.decomposition.components),
        "body_ast_hash": ir.body_ast_hash,
        "additivity": ir.additivity,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"


def _authoring_fingerprint_for_time_field(ir: DimensionIR) -> str:
    payload = {
        "semantic_id": ir.semantic_id,
        "entity": ir.dataset,
        "data_type": ir.data_type,
        "granularity": ir.granularity,
        "timezone": ir.timezone,
        "format": ir.format,
        "required_prefix": ir.required_prefix,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"


def _metric_chosen(ir: MetricIR) -> dict[str, object]:
    return {
        "kind": ir.decomposition.kind,
        "components": dict(ir.decomposition.components),
        "is_derived": ir.is_derived,
        "additivity": ir.additivity,
    }


def _time_field_chosen(ir: DimensionIR) -> dict[str, object]:
    return {
        "entity": ir.dataset,
        "name": ir.name,
        "data_type": ir.data_type,
        "granularity": ir.granularity,
        "timezone": ir.timezone,
        "format": ir.format,
    }


def backfill_blast_radii(
    semantic_root: Path | str,
    *,
    blast_radius_of: Callable[[tuple[str, ...]], int],
) -> None:
    """Replace ``blast_radius=0`` in stored DecisionRecords with the real
    transitive-dependent count computed from the loaded registry.

    Cold-start decisions may write ``blast_radius=0`` because no
    dependency graph exists yet. After load, this function corrects those
    stale provenance entries. A legitimate ``0`` (isolated object with no
    dependents) is preserved — backfill only acts when the computed value
    differs from the stored ``0``.
    """
    store = LedgerStore(Path(semantic_root))
    changed = False
    for obj in store.iter_object_records():
        real_br = blast_radius_of((obj.semantic_id,))
        if real_br == 0:
            continue
        new_decisions: list[DecisionRecord] = []
        for decision in obj.decisions:
            if decision.blast_radius == 0:
                d = decision.to_dict()
                d["blast_radius"] = real_br
                new_decisions.append(DecisionRecord.from_dict(d))
                changed = True
            else:
                new_decisions.append(decision)
        if changed:
            store.write_object(
                ObjectEvidence(
                    semantic_id=obj.semantic_id,
                    authored_at=obj.authored_at,
                    decisions=tuple(new_decisions),
                    rejected_candidates=obj.rejected_candidates,
                )
            )
            changed = False


def auto_record_authoring_decisions(
    registry: object,
    semantic_root: Path | str,
    *,
    blast_radius_of: Callable[[tuple[str, ...]], int] | None = None,
) -> None:
    """Auto-record DecisionRecords for authored metrics and time fields.

    For each MetricIR, record a metric_decomposition decision. For each
    time_field DimensionIR, record a time_field_identity decision. Idempotent:
    existing decisions (from prior auto-record or manual write) are preserved.
    When a definition changes, the old authoring auto-record is replaced.
    """
    store = LedgerStore(Path(semantic_root))
    decided_at = datetime.now(UTC).isoformat()

    for metric_ir in registry.metrics.values():  # type: ignore[attr-defined]
        _auto_record_if_missing(
            store=store,
            semantic_id=metric_ir.semantic_id,
            decision_kind="metric_decomposition",
            chosen=_metric_chosen(metric_ir),
            evidence_fingerprint=_authoring_fingerprint_for_metric(metric_ir),
            decided_at=decided_at,
            blast_radius=(
                blast_radius_of((metric_ir.semantic_id,)) if blast_radius_of is not None else 0
            ),
        )

    for field_ir in registry.fields.values():  # type: ignore[attr-defined]
        if not field_ir.is_time_field:
            continue
        _auto_record_if_missing(
            store=store,
            semantic_id=field_ir.semantic_id,
            decision_kind="time_dimension_identity",
            chosen=_time_field_chosen(field_ir),
            evidence_fingerprint=_authoring_fingerprint_for_time_field(field_ir),
            decided_at=decided_at,
            blast_radius=(
                blast_radius_of((field_ir.semantic_id,)) if blast_radius_of is not None else 0
            ),
        )


def _auto_record_if_missing(
    store: LedgerStore,
    semantic_id: str,
    decision_kind: DecisionKind,
    chosen: object,
    evidence_fingerprint: str,
    decided_at: str,
    blast_radius: int,
) -> None:
    existing = store.read_object(semantic_id)

    # Build the new auto-record DecisionRecord
    record = DecisionRecord(
        decision_kind=decision_kind,
        chosen=chosen,
        agreement_confidence="high",
        qualifying_sources=(_AUTHORING_QUALIFYING_SOURCE,),
        materiality=floor_for(decision_kind),
        blast_radius=blast_radius,
        evidence_fingerprint=evidence_fingerprint,
        question_id=None,
        decided_at=decided_at,
        cited_source=None,
        cited_columns=(),
    )

    # Case 2/3: existing authoring auto-record for this kind
    if existing is not None:
        authoring_records = [
            d
            for d in existing.decisions
            if d.decision_kind == decision_kind
            and d.qualifying_sources == (_AUTHORING_QUALIFYING_SOURCE,)
        ]
        if authoring_records:
            latest = authoring_records[-1]
            # Case 2: fingerprint matches → unchanged, skip
            if latest.evidence_fingerprint == evidence_fingerprint:
                return
            # Case 3: fingerprint differs → definition changed, replace
            decisions = tuple(
                d
                for d in existing.decisions
                if not (
                    d.decision_kind == decision_kind
                    and d.qualifying_sources == (_AUTHORING_QUALIFYING_SOURCE,)
                )
            )
            store.write_object(
                ObjectEvidence(
                    semantic_id=semantic_id,
                    authored_at=existing.authored_at,
                    decisions=(*decisions, record),
                    rejected_candidates=existing.rejected_candidates,
                )
            )
            return

    # Case 4: no decision at all → create new
    decisions = existing.decisions if existing else ()
    rejected = existing.rejected_candidates if existing else ()
    authored_at = existing.authored_at if existing else decided_at
    store.write_object(
        ObjectEvidence(
            semantic_id=semantic_id,
            authored_at=authored_at,
            decisions=(*decisions, record),
            rejected_candidates=rejected,
        )
    )
