"""Closed Event Journey analysis values."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from marivo.analysis.errors import (
    AnalysisRepair,
    InvalidCompletenessDeclarationError,
    InvalidEventMatchingPolicyError,
    InvalidEventPatternError,
    RepairKind,
)
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import EventKind, Ref, RefPayloadV1, SemanticKind, StateModelKind
from marivo.render import Card, RenderableResult
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


class CompletenessDeclaration(BaseModel):
    """Explicit caller assertion for exact Event inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["declared_complete_through"] = "declared_complete_through"
    inputs: tuple[Ref[EventKind], ...]
    through: str
    rationale: str

    @model_validator(mode="after")
    def _validate_declaration(self) -> CompletenessDeclaration:
        if not self.inputs:
            raise ValueError("completeness inputs must be non-empty")
        if any(
            type(value) is not Ref or value.kind is not SemanticKind.EVENT for value in self.inputs
        ):
            raise ValueError("completeness inputs must contain exact Ref[event] values")
        if len(set(self.inputs)) != len(self.inputs):
            raise ValueError("completeness inputs must be unique")
        if not self.through.strip():
            raise ValueError("completeness through must be non-empty")
        if not self.rationale.strip():
            raise ValueError("completeness rationale must be non-empty")
        return self

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "schema": "marivo.completeness_declaration/v1",
                "inputs": [RefPayloadV1.from_ref(value).to_dict() for value in self.inputs],
                "through": self.through,
                "rationale": self.rationale,
            }
        )


class EventWatermarkRequest(BaseModel):
    """Exact request passed to a backend completeness provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_ref: Ref[EventKind]
    event_fingerprint: str
    source_entity_ref: str
    occurred_at_ref: str
    required_through: str


class EventWatermarkReceipt(BaseModel):
    """Provider-owned authoritative Event completeness receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    complete_through: str
    authority: str
    observed_at: str
    source_revision: str | None = None

    @model_validator(mode="after")
    def _validate_receipt(self) -> EventWatermarkReceipt:
        if not self.complete_through.strip():
            raise ValueError("complete_through must be non-empty")
        if not self.authority.strip():
            raise ValueError("authority must be non-empty")
        if not self.observed_at.strip():
            raise ValueError("observed_at must be non-empty")
        return self


@dataclass(frozen=True, repr=False)
class EventOccurrenceBounds(RenderableResult):
    """Observed occurrence-time bounds for one exact Event or StateModel.

    ``event_refs`` records the exact Event inputs; ``earliest_occurrence_at``
    and ``latest_occurrence_at`` are UTC-normalized observed bounds, and
    ``observed_at`` records query time. The bounds are computed from the
    target's exact Event predicates. A StateModel with no Event triggers has
    empty ``event_refs`` and absent bounds. The result is a data-range
    observation, not evidence that the source is complete through either
    bound. Use ``session.events.watermark(...)`` for authoritative
    completeness evidence.
    """

    target_ref: Ref[EventKind | StateModelKind]
    event_refs: tuple[Ref[EventKind], ...]
    earliest_occurrence_at: datetime | None
    latest_occurrence_at: datetime | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.target_ref.kind not in (SemanticKind.EVENT, SemanticKind.STATE_MODEL):
            raise ValueError("EventOccurrenceBounds target_ref must be an Event or StateModel ref")
        if any(ref.kind is not SemanticKind.EVENT for ref in self.event_refs):
            raise ValueError("EventOccurrenceBounds event_refs must contain exact Event refs")
        if not self.event_refs and self.target_ref.kind is SemanticKind.EVENT:
            raise ValueError(
                "EventOccurrenceBounds Event target must contribute its exact Event ref"
            )
        if len(set(self.event_refs)) != len(self.event_refs):
            raise ValueError("EventOccurrenceBounds event_refs must be unique")
        if (self.earliest_occurrence_at is None) != (self.latest_occurrence_at is None):
            raise ValueError("EventOccurrenceBounds bounds must both be present or both be absent")
        if (
            self.earliest_occurrence_at is not None
            and self.latest_occurrence_at is not None
            and self.earliest_occurrence_at > self.latest_occurrence_at
        ):
            raise ValueError("EventOccurrenceBounds earliest bound must not exceed latest bound")
        timestamps = (
            self.earliest_occurrence_at,
            self.latest_occurrence_at,
            self.observed_at,
        )
        if any(value is not None and value.tzinfo is None for value in timestamps):
            raise ValueError("EventOccurrenceBounds timestamps must be timezone-aware")

    def _repr_identity(self) -> str:
        latest = (
            self.latest_occurrence_at.isoformat()
            if self.latest_occurrence_at is not None
            else "empty"
        )
        return f"EventOccurrenceBounds target={self.target_ref.key} latest={latest}"

    def _card(self) -> Card:
        card = Card(
            identity=(
                f"EventOccurrenceBounds target={self.target_ref.key} events={len(self.event_refs)}"
            ),
            available=(
                ".show()",
                "session.events.watermark(event, through=...)",
            ),
        )
        earliest = self.earliest_occurrence_at
        latest = self.latest_occurrence_at
        if not self.event_refs:
            card.status("empty: StateModel declares no Event triggers")
        elif earliest is None or latest is None:
            card.status("empty: no matching Event occurrences")
        else:
            card.status(f"observed_bounds={earliest.isoformat()} .. {latest.isoformat()}")
        card.field("observed_at", self.observed_at.isoformat())
        return card.listing("events", (ref.key for ref in self.event_refs))


def step(*, participant: ParticipantRoleHandle, key: str) -> PatternStep:
    """Build one typed Event Journey step.

    Args:
        participant: Immutable handle returned by ``ms.participant_role(...)``.
        key: Unique lowercase snake-case key used in EventFrame rows.

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


def declared_complete_through(
    *,
    inputs: tuple[Ref[EventKind], ...],
    through: str,
    rationale: str,
) -> CompletenessDeclaration:
    """Declare exact Event inputs complete through one governed bound.

    Args:
        inputs: Non-empty tuple of exact Event refs from the active pattern.
        through: Inclusive completeness bound.
        rationale: Non-empty provenance statement for the declaration.

    Returns:
        A frozen CompletenessDeclaration retained in EventFrame metadata.

    Guidance:
        This is an explicit caller assumption, not an observed fact. It is
        weaker than an authoritative backend watermark (obtain one with
        ``session.events.watermark(...)``). It requires a rationale that
        explains the governing reconciliation evidence.

    Example:
        >>> coverage = mv.declared_complete_through(
        ...     inputs=(cart_created, payment_succeeded),
        ...     through=followup_end,
        ...     rationale="Warehouse reconciliation completed through followup_end.",
        ... )

    Constraints:
        A pattern Event may be covered by at most one declaration. An
        authoritative backend watermark takes precedence when available.
    """
    try:
        if type(inputs) is not tuple:
            raise TypeError("inputs must be an exact tuple of EventRefs")
        return CompletenessDeclaration(
            inputs=inputs,
            through=through,
            rationale=rationale,
        )
    except (TypeError, ValueError) as exc:
        raise InvalidCompletenessDeclarationError(
            message="invalid Event completeness declaration",
            expected="non-empty unique EventRefs, through, and rationale",
            received=repr((inputs, through, rationale)),
            location="mv.declared_complete_through(inputs, through, rationale)",
            repair=_event_repair(
                kind="user_choice",
                action="Name exact EventRefs from the pattern and provide a non-empty rationale.",
            ),
        ) from exc


__all__ = [
    "CompletenessDeclaration",
    "EventMatchingPolicy",
    "EventOccurrenceBounds",
    "EventPattern",
    "EventWatermarkReceipt",
    "EventWatermarkRequest",
    "EveryStart",
    "FirstPerSubject",
    "PatternStep",
    "declared_complete_through",
    "every_start",
    "first_per_subject",
    "sequence",
    "step",
]
