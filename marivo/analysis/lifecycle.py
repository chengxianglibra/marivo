"""Closed replay-based Lifecycle analysis values."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, field_validator

from marivo.analysis.errors import AnalysisRepair, ModelStateMismatchError
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import Ref, RefPayloadV1, StateModelKind, _decode_ref_payload
from marivo.semantic.state_model import ModelStateHandle


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class FromInception(BaseModel):
    """Required Phase 3 replay seed using the first qualifying inception."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["from_inception"] = "from_inception"

    @property
    def fingerprint(self) -> str:
        """Return the stable seed-contract fingerprint."""
        return _fingerprint(
            {
                "schema": "marivo.lifecycle_seed/v1",
                "kind": self.kind,
            }
        )


class InState(BaseModel):
    """Select subjects whose replayed state is established at one instant."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    kind: Literal["in_state"] = "in_state"
    state: ModelStateHandle
    as_of: str

    @field_validator("state", mode="before")
    @classmethod
    def _decode_state(cls, value: object) -> object:
        """Restore one persisted handle payload during cold artifact recovery."""
        if not isinstance(value, Mapping):
            return value
        if set(value) != {"model", "name"}:
            raise ValueError("persisted in_state handle must contain exactly model and name")
        return ModelStateHandle(
            model=cast(
                "Ref[StateModelKind]",
                _decode_ref_payload(cast("Any", value["model"])),
            ),
            name=cast("str", value["name"]),
        )

    @field_validator("state")
    @classmethod
    def _validate_state(cls, value: ModelStateHandle) -> ModelStateHandle:
        if type(value) is not ModelStateHandle:
            raise ValueError("in_state requires an exact ModelStateHandle")
        return value

    @field_validator("as_of")
    @classmethod
    def _validate_as_of(cls, value: str) -> str:
        if type(value) is not str or not value.strip():
            raise ValueError("in_state as_of must be a non-empty instant")
        return value

    @property
    def fingerprint(self) -> str:
        """Return the stable selection fingerprint before catalog resolution."""
        return _fingerprint(
            {
                "schema": "marivo.subject_selection/v1",
                "kind": self.kind,
                "state": {
                    "model": RefPayloadV1.from_ref(self.state.model).to_dict(),
                    "name": self.state.name,
                },
                "as_of": self.as_of,
            }
        )


def from_inception() -> FromInception:
    """Return the only Phase 3 Lifecycle replay seed.

    Returns:
        A frozen from-inception replay seed.

    Guidance:
        Use this when the modeled Event history contains a deterministic
        inception before the state interval you want to read. Replay
        reconstructs state from that inception even when it precedes the
        window, so the window bounds the reported intervals rather than the
        evidence. A missing inception is never replaced by assuming the
        initial state: under complete input coverage it fails, and under
        unknown coverage the subject is reported as coverage-censored.

    Example:
        >>> seed = mv.from_inception()

    Constraints:
        Replay has no default seed and no hidden fallback; pass this value
        explicitly. The StateModel must declare at least one inception
        trigger.
    """
    return FromInception()


def in_state(state: ModelStateHandle, *, as_of: str) -> InState:
    """Build an exact typed Lifecycle state selection.

    Args:
        state: Exact ``ModelStateHandle`` owned by the replayed StateModel.
        as_of: Timezone-aware ISO-8601 observation instant inside the source
            replay window.

    Returns:
        A frozen ``InState`` value accepted by ``session.select_subjects(...)``.

    Example:
        >>> selection = mv.in_state(paid, as_of="2026-07-15T00:00:00Z")

    Constraints:
        Only an exact ``ModelStateHandle`` is accepted. Model membership,
        fingerprint currency, and the instant's position inside the source
        window are validated by ``select_subjects``. Coverage-censored
        subjects never enter the resulting ready ``SubjectSet``.
    """
    if type(state) is not ModelStateHandle:
        raise ModelStateMismatchError(
            message="in_state requires an exact ModelStateHandle",
            expected="ms.model_state(model=<Ref[state_model]>, name=<state_name>)",
            received=repr(state),
            location="mv.in_state(state)",
            repair=AnalysisRepair(
                kind="user_choice",
                action=(
                    "Choose one exact current StateModel state with "
                    "ms.model_state(...), then pass that handle to mv.in_state(...)."
                ),
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id="in_state",
                ),
            ),
        )
    return InState(state=state, as_of=as_of)


__all__ = ["FromInception", "InState", "from_inception", "in_state"]
