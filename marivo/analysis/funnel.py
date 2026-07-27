"""Closed funnel comparison and attribution target values."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, field_validator

from marivo.analysis.errors import AnalysisRepair, PatternStepMismatchError
from marivo.analysis.event import PatternStep
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import EventKind, Ref, _decode_ref_payload
from marivo.semantic.event import ParticipantRoleHandle


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class FunnelLossRate(BaseModel):
    """Target one PatternStep's loss from its immediately preceding step."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    kind: Literal["funnel_loss_rate"] = "funnel_loss_rate"
    step: PatternStep

    @field_validator("step", mode="before")
    @classmethod
    def _decode_step(cls, value: object) -> object:
        """Restore a persisted PatternStep during cold artifact recovery."""
        if isinstance(value, Mapping):
            participant = value.get("participant")
            key = value.get("key")
            if not isinstance(participant, Mapping) or set(participant) != {"event", "name"}:
                raise ValueError("persisted PatternStep participant is invalid")
            return PatternStep(
                participant=ParticipantRoleHandle(
                    event=cast(
                        "Ref[EventKind]",
                        _decode_ref_payload(cast("Any", participant["event"])),
                    ),
                    name=cast("str", participant["name"]),
                ),
                key=cast("str", key),
            )
        return value

    @field_validator("step")
    @classmethod
    def _validate_step(cls, value: PatternStep) -> PatternStep:
        if type(value) is not PatternStep:
            raise ValueError("funnel_loss_rate requires an exact PatternStep")
        return value

    @property
    def fingerprint(self) -> str:
        """Return the stable target fingerprint before artifact resolution."""
        return _fingerprint(
            {
                "schema": "marivo.attribution_target/v1",
                "kind": self.kind,
                "step": self.step.fingerprint,
            }
        )


def funnel_loss_rate(*, step: PatternStep) -> FunnelLossRate:
    """Target the loss into one exact funnel PatternStep for attribution.

    Args:
        step: Exact non-initial ``PatternStep`` retained by both compared funnels.

    Returns:
        A frozen ``FunnelLossRate`` accepted by ``session.attribute(...)``.

    Guidance:
        The selected target is the loss from its immediately preceding PatternStep
        into this step. Attribution describes arithmetic contribution to an
        observed change, never treatment effect, counterfactual, or causality.

    Example:
        >>> target = mv.funnel_loss_rate(step=payment_step)

    Constraints:
        Only an exact ``PatternStep`` is accepted. Membership and the non-initial
        rule are validated by ``session.attribute``.
    """
    if type(step) is not PatternStep:
        raise PatternStepMismatchError(
            message="funnel_loss_rate requires an exact PatternStep",
            expected="mv.funnel_loss_rate(step=<PatternStep>)",
            received=repr(step),
            location="mv.funnel_loss_rate(step)",
            repair=AnalysisRepair(
                kind="user_choice",
                action=(
                    "Choose one exact retained PatternStep from the compared "
                    "EventPattern, then pass it to mv.funnel_loss_rate(step=...)."
                ),
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id="funnel_loss_rate",
                ),
            ),
        )
    return FunnelLossRate(step=step)


__all__ = ["FunnelLossRate", "funnel_loss_rate"]
