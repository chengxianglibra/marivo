"""Closed Event Journey analysis values."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from marivo.analysis.errors import (
    AnalysisRepair,
    InvalidEventMatchingPolicyError,
    InvalidEventPatternError,
    RepairKind,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import EventKind, Ref, RefPayloadV1
from marivo.semantic.event import ParticipantRoleHandle

_STEP_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
EventHelpTarget: TypeAlias = Literal[
    "dropped_before",
    "events.funnel",
    "events.match",
    "events.time_to_event",
    "select_subjects",
]


def _event_repair(
    *,
    kind: RepairKind,
    action: str,
    help_target: EventHelpTarget = "events.match",
    snippet: str | None = None,
    candidates: tuple[str, ...] = (),
) -> AnalysisRepair:
    """Build one truthful repair owned by a closed Event analysis contract."""
    if not action.strip():
        raise ValueError("Event repair action must be non-empty")
    if kind == "retry" and not (snippet and snippet.strip()):
        raise ValueError("Event retry repair requires a runnable snippet")
    if kind != "retry" and snippet is not None:
        raise ValueError("only Event retry repairs may carry a snippet")
    return AnalysisRepair(
        kind=kind,
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id=help_target),
        snippet=snippet,
        candidates=candidates,
    )


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class PatternStep(BaseModel):
    """One typed Event role in an ordered journey pattern."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    participant: ParticipantRoleHandle
    key: str

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        if not _STEP_KEY.fullmatch(value):
            raise ValueError("step key must match [a-z][a-z0-9_]*")
        return value

    @property
    def event(self) -> Ref[EventKind]:
        return self.participant.event

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "schema": "marivo.pattern_step/v1",
                "event": RefPayloadV1.from_ref(self.event).to_dict(),
                "participant": self.participant.name,
                "key": self.key,
            }
        )


class EventPattern(BaseModel):
    """Closed ordered sequence of typed PatternSteps."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    steps: tuple[PatternStep, ...]

    @model_validator(mode="after")
    def _validate_steps(self) -> EventPattern:
        if not self.steps:
            raise ValueError("EventPattern requires at least one step")
        keys = tuple(step.key for step in self.steps)
        if len(set(keys)) != len(keys):
            raise ValueError("EventPattern step keys must be unique")
        return self

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "schema": "marivo.event_pattern/v1",
                "steps": [step.fingerprint for step in self.steps],
            }
        )


class FirstPerSubject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["first_per_subject"] = "first_per_subject"


class EveryStart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["every_start"] = "every_start"
    completion_assignment: Literal["exclusive", "shared"]


EventMatchingPolicy = Annotated[
    FirstPerSubject | EveryStart,
    Field(discriminator="kind"),
]


def step(*, participant: ParticipantRoleHandle, key: str) -> PatternStep:
    """Build one typed Event Journey step.

    Args:
        participant: Immutable handle returned by ``ms.participant_role(...)``.
        key: Unique lowercase snake-case key used in Event Dataset rows.

    Returns:
        A frozen PatternStep accepted by :func:`sequence`.

    Example:
        >>> cart = mv.step(participant=cart_user, key="cart")

    Constraints:
        Bare Event refs, strings, and numeric step positions are not accepted.
    """
    try:
        if type(participant) is not ParticipantRoleHandle:
            raise TypeError("participant must be an exact ParticipantRoleHandle")
        return PatternStep(participant=participant, key=key)
    except (TypeError, ValueError) as exc:
        raise InvalidEventPatternError(
            message="invalid Event PatternStep",
            expected="mv.step(participant=<ParticipantRoleHandle>, key=<snake_case>)",
            received=f"participant={participant!r}, key={key!r}",
            location="mv.step(participant, key)",
            repair=_event_repair(
                kind="user_choice",
                action="Use ms.participant_role(...) and a unique lowercase snake-case key.",
            ),
        ) from exc


def sequence(*steps: PatternStep) -> EventPattern:
    """Build one ordered EventPattern.

    Args:
        *steps: One or more typed steps in required occurrence order.

    Returns:
        A frozen EventPattern with a stable fingerprint.

    Example:
        >>> pattern = mv.sequence(cart_step, checkout_step, payment_step)

    Constraints:
        Step keys must be unique. Runtime validation additionally requires all
        participant endpoints to resolve to the same subject Entity.
    """
    try:
        if any(type(item) is not PatternStep for item in steps):
            raise TypeError("sequence accepts only exact PatternStep values")
        return EventPattern(steps=steps)
    except (TypeError, ValueError) as exc:
        raise InvalidEventPatternError(
            message="invalid EventPattern sequence",
            expected="one or more PatternStep values with unique keys",
            received=repr(steps),
            location="mv.sequence(*steps)",
            repair=_event_repair(
                kind="user_choice",
                action="Pass only mv.step(...) values with unique keys to mv.sequence(...).",
            ),
        ) from exc


def first_per_subject() -> FirstPerSubject:
    """Choose one journey at the earliest first-step occurrence per subject.

    Returns:
        A frozen first-per-subject matching policy.

    Guidance:
        Use this for one subject-level conversion journey: the earliest start
        anchors the journey and later starts are excluded. In Phase 2 this is
        the matching policy compatible with subject-level funnel reduction.

    Example:
        >>> matching = mv.first_per_subject()

    Constraints:
        Later first-step occurrences for the same subject do not create
        additional attempts.
    """
    return FirstPerSubject()


def every_start(
    *,
    completion_assignment: Literal["exclusive", "shared"],
) -> EveryStart:
    """Choose one journey attempt per first-step occurrence.

    Args:
        completion_assignment: ``"exclusive"`` assigns a final occurrence to
            the earliest eligible open attempt; ``"shared"`` permits one final
            occurrence to complete multiple eligible attempts.

    Returns:
        A frozen every-start matching policy.

    Guidance:
        Use ``exclusive`` when each completion belongs to at most one attempt;
        the earliest eligible open attempt receives it. Use ``shared`` only
        when one completion is business-correct for multiple overlapping
        attempts.

    Example:
        >>> matching = mv.every_start(completion_assignment="exclusive")

    Constraints:
        The assignment choice affects final-step sharing only.
    """
    try:
        return EveryStart(completion_assignment=completion_assignment)
    except ValueError as exc:
        raise InvalidEventMatchingPolicyError(
            message="invalid every_start completion assignment",
            expected="'exclusive' or 'shared'",
            received=repr(completion_assignment),
            location="mv.every_start(completion_assignment)",
            repair=_event_repair(
                kind="user_choice",
                action="Choose completion_assignment='exclusive' or 'shared'.",
                candidates=(
                    'completion_assignment="exclusive"',
                    'completion_assignment="shared"',
                ),
            ),
        ) from exc


__all__ = [
    "EventMatchingPolicy",
    "EventPattern",
    "EveryStart",
    "FirstPerSubject",
    "PatternStep",
    "every_start",
    "first_per_subject",
    "sequence",
    "step",
]
