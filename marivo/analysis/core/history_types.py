"""Closed History requests, independent of execution and live authoring."""

from dataclasses import dataclass
from typing import Literal, TypeAlias

from marivo.analysis.core.domain_captures import EntryAxisCapture


@dataclass(frozen=True, slots=True)
class StateAt:
    state: str
    at: str
    kind: Literal["in_state"] = "in_state"


@dataclass(frozen=True, slots=True)
class Distribution:
    at: tuple[str, ...]
    axes: tuple[EntryAxisCapture, ...] = ()
    kind: Literal["distribution"] = "distribution"


@dataclass(frozen=True, slots=True)
class Transitions:
    kind: Literal["transitions"] = "transitions"


@dataclass(frozen=True, slots=True)
class Violations:
    kind: Literal["violations"] = "violations"


@dataclass(frozen=True, slots=True)
class Intervals:
    kind: Literal["intervals"] = "intervals"


@dataclass(frozen=True, slots=True)
class Dwell:
    estimand: Literal["completed_window_fragment_duration@v1"] = (
        "completed_window_fragment_duration@v1"
    )
    kind: Literal["dwell"] = "dwell"


HistoryRequest: TypeAlias = StateAt | Distribution | Transitions | Violations | Intervals | Dwell
HistoryField: TypeAlias = Literal[
    "known_state_count",
    "seeded_subject_count",
    "coverage_censored_count",
    "share_among_seeded",
    "count",
    "share_of_modeled_transitions",
    "interval_count",
    "completed_count",
    "right_censored_count",
    "left_clipped_completed_count",
    "mean_duration",
    "median_duration",
    "p90_duration",
    "trigger",
    "occurred_at",
    "state_at_event",
    "kind",
    "state",
    "start",
    "end",
    "observed_duration",
    "left_clipped",
    "status",
]
