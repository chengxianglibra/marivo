"""Small public retention projects shared by Runtime and process checks."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Literal, TypedDict

from typing_extensions import Unpack

import marivo.semantic as ms
from marivo.analysis.domains.completeness import SourceOriginCompletenessDeclarationV1
from marivo.analysis.public_dsl import LogicalAnchorDomain
from marivo.analysis.session.core import Session
from marivo.semantic.event import ParticipantRoleHandle
from tests.analysis.lifecycle.lifecycle_fixtures import START, build_lifecycle_public


class _RetentionOptions(TypedDict, total=False):
    subject: str
    occurrence: str
    form: str
    unit: str
    zone: str
    ordered: bool
    empty: bool
    cycle: bool
    observations: bool
    backend_name: Literal["duckdb", "sqlite"]


def build_retention(
    root: Path,
    *,
    rows: list[tuple[int, str, int, int]] | None = None,
    **kwargs: Unpack[_RetentionOptions],
) -> tuple[
    Session,
    LogicalAnchorDomain,
    ParticipantRoleHandle,
    tuple[SourceOriginCompletenessDeclarationV1, ...],
]:
    session, members, window, claims, _ = build_lifecycle_public(
        root,
        rows=rows
        if rows is not None
        else [
            (0, "started", 0, 1),
            (0, "finished", 5, 2),
            (0, "started", 8, 3),
            (1, "started", 0, 1),
            (1, "started", 8, 2),
        ],
        **kwargs,
    )
    started = ms.participant_role(event=ms.ref.event("commerce.started"), name="subject")
    returning = ms.participant_role(event=ms.ref.event("commerce.finished"), name="subject")
    anchors = session.anchors(
        started,
        population=members,
        during=window,
        business_order=ms.ref.business_order("commerce.order"),
    )
    claims = tuple(
        replace(claim, inputs=(returning.event,), complete_through=START + timedelta(seconds=10))
        for claim in claims
    )
    return session, anchors, returning, claims
