"""Shared builders for history operations tests."""

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.refs import ref
from tests.analysis.lifecycle.lifecycle_fixtures import (
    END,
    START,
)


def replay(session, members, window, claims):
    return session.lifecycle.replay(
        ref.state_model("commerce.model"),
        population=members,
        window=window,
        seed=mv.from_inception(),
        completeness=claims,
    )


def operations(history):
    return {
        "in_state": lambda: history.read(
            mv.in_state(
                ms.model_state(model=ref.state_model("commerce.model"), name="done"), at=END
            )
        ),
        "distribution": lambda: history.distribution(at=(START, END)),
        "transitions": history.transitions,
        "violations": history.violations,
        "intervals": history.intervals,
        "dwell": history.dwell,
    }


def artifact(result):
    return result.evidence_digest().artifact_ref.ref
