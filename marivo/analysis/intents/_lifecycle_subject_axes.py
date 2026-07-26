"""Point-in-time subject-axis enrichment for Lifecycle distributions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from marivo.analysis.errors import AnalysisRepair, InvalidSubjectAxisError
from marivo.analysis.frames.lifecycle import LifecycleAxisBinding
from marivo.analysis.intents._event_subject_axes import (
    ResolvedSubjectAxis,
    materialize_subject_axes,
    resolve_subject_axes,
)
from marivo.analysis.session.core import Session
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import EntityKind, Ref

_RESERVED_DISTRIBUTION_COLUMNS = frozenset({"as_of", "model_state", "subject_count", "share"})


@dataclass(frozen=True)
class LifecycleAxisMaterialization:
    """One exact declared axis tuple per known subject and distribution instant."""

    values: pd.DataFrame
    bindings: tuple[LifecycleAxisBinding, ...]
    query_refs: tuple[str, ...]
    lineage: tuple[dict[str, object], ...]


def _lifecycle_axis_error(error: InvalidSubjectAxisError) -> InvalidSubjectAxisError:
    return InvalidSubjectAxisError(
        message=error.message.replace("funnel", "Lifecycle distribution"),
        expected=error.expected,
        received=error.received,
        location=(
            error.location.replace("session.events.funnel", "session.lifecycle.distribution")
            if error.location
            else "session.lifecycle.distribution.axes"
        ),
        repair=AnalysisRepair(
            kind=error.repair.kind if error.repair is not None else "inspect",
            action=(
                error.repair.action.replace("funnel", "Lifecycle distribution").replace(
                    "cohort-entry", "distribution"
                )
                if error.repair is not None
                else "Inspect the governed subject axis and retry distribution."
            ),
            help_target=LiveHelpTarget(
                surface="analysis",
                canonical_id="lifecycle.distribution",
            ),
            candidates=error.repair.candidates if error.repair is not None else (),
        ),
    )


def resolve_lifecycle_subject_axes(
    session: Session,
    *,
    subject_entity: Ref[EntityKind],
    axes: Sequence[object],
) -> tuple[ResolvedSubjectAxis, ...]:
    """Resolve Lifecycle axes through the shared governed subject-axis planner."""

    try:
        return resolve_subject_axes(
            session,
            subject_entity=subject_entity,
            axes=axes,
            _reserved_columns=_RESERVED_DISTRIBUTION_COLUMNS,
        )
    except InvalidSubjectAxisError as exc:
        raise _lifecycle_axis_error(exc) from exc


def materialize_lifecycle_subject_axes(
    session: Session,
    *,
    membership: pd.DataFrame,
    instants: tuple[str, ...],
    subject_entity: Ref[EntityKind],
    subject_identity: tuple[str, ...],
    axes: tuple[ResolvedSubjectAxis, ...],
) -> LifecycleAxisMaterialization:
    """Resolve axes at each exact ``as_of`` instant for known-state subjects."""

    axis_columns = tuple(axis.binding.output_column for axis in axes)
    if not axes or membership.empty:
        return LifecycleAxisMaterialization(
            values=pd.DataFrame(columns=("subject_identity", "as_of", *axis_columns)),
            bindings=tuple(
                LifecycleAxisBinding(
                    dimension_ref=axis.binding.dimension_ref,
                    output_column=axis.binding.output_column,
                    relationship_path=axis.binding.relationship_path,
                    versioning_resolution=axis.binding.versioning_resolution,
                )
                for axis in axes
            ),
            query_refs=(),
            lineage=(),
        )

    materialized_parts: list[pd.DataFrame] = []
    query_refs: list[str] = []
    lineage: list[dict[str, object]] = []
    for instant in instants:
        instant_membership = membership.loc[membership["as_of"] == instant]
        if instant_membership.empty:
            continue
        journey_rows = pd.DataFrame(
            {
                "journey_id": [
                    f"lifecycle-axis-{instant}-{index}" for index in range(len(instant_membership))
                ],
                "subject_identity": instant_membership["subject_identity"].tolist(),
                "step_key": ["as_of"] * len(instant_membership),
                "occurred_at": [pd.Timestamp(instant)] * len(instant_membership),
            }
        )
        try:
            result = materialize_subject_axes(
                session,
                journey_rows=journey_rows,
                first_step_key="as_of",
                subject_entity=subject_entity,
                subject_identity=subject_identity,
                axes=axes,
            )
        except InvalidSubjectAxisError as exc:
            raise _lifecycle_axis_error(exc) from exc
        values = result.values.copy()
        values.insert(1, "as_of", instant)
        materialized_parts.append(values)
        query_refs.extend(result.query_refs)
        lineage.extend(
            {
                **item,
                "anchor": "as_of",
                "as_of": instant,
            }
            for item in result.lineage
        )

    values = (
        pd.concat(materialized_parts, ignore_index=True)
        if materialized_parts
        else pd.DataFrame(columns=("subject_identity", "as_of", *axis_columns))
    )
    if values.duplicated(subset=["subject_identity", "as_of"], keep=False).any():
        raise ValueError(
            "Lifecycle axes produced multiple declared-axis tuples for one subject/instant"
        )
    return LifecycleAxisMaterialization(
        values=values.loc[:, ["subject_identity", "as_of", *axis_columns]],
        bindings=tuple(
            LifecycleAxisBinding(
                dimension_ref=axis.binding.dimension_ref,
                output_column=axis.binding.output_column,
                relationship_path=axis.binding.relationship_path,
                versioning_resolution=axis.binding.versioning_resolution,
            )
            for axis in axes
        ),
        query_refs=tuple(query_refs),
        lineage=tuple(lineage),
    )


__all__ = []
