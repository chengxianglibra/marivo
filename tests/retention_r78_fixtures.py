"""Small public retention projects shared by Runtime and process checks."""

from dataclasses import replace
from datetime import timedelta

import marivo.semantic as ms
from tests.lifecycle_r75_fixtures import START, build_lifecycle_public


def build_retention(root, *, rows=None, **kwargs):
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
