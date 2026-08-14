"""Closed StateModel authoring values and typed state identities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, TypeAlias

from marivo.refs import (
    DomainKind,
    EntityKind,
    EventKind,
    Ref,
    SemanticKind,
    StateModelKind,
)
from marivo.refs import ref as ref_factory
from marivo.semantic._authoring_context import (
    _caller_location,
    _check_duplicate,
    _push_ir,
    _register_authoring_file,
    _require_ctx,
    _require_entity_ref,
    _resolve_domain,
    _user_caller_location,
)
from marivo.semantic._authoring_values import _build_ai_context
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import (
    ErrorKind,
    SemanticDecoratorError,
    SemanticRuntimeError,
    _raise,
    repair,
)
from marivo.semantic.event import ParticipantRoleHandle
from marivo.semantic.ir import (
    LifecycleStateIR,
    StateModelDeclarationIR,
    StateTriggerDeclarationIR,
)
from marivo.semantic.typing import AiContextValue

if TYPE_CHECKING:
    from marivo.semantic._expression_binding import CompiledExpressionSidecar
    from marivo.semantic.ir import StateModelIR
    from marivo.semantic.validator import Registry

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _invalid(
    message: str,
    *,
    expected: str,
    received: object,
    canonical_id: str,
) -> NoReturn:
    _raise(
        ErrorKind.INVALID_STATE_MODEL,
        message,
        cls=SemanticDecoratorError,
        expected=expected,
        received=repr(received),
        location=_user_caller_location(),
        constraint_id=ConstraintId.STATE_MODEL_SHAPE,
        repair_value=repair(
            kind="reauthor",
            canonical_id=canonical_id,
            action="Reauthor the value with the closed StateModel constructor contract.",
        ),
    )


def _require_name(value: object, *, parameter: str, canonical_id: str) -> str:
    if type(value) is not str or not _NAME.fullmatch(value):
        _invalid(
            f"{parameter} must be a lowercase snake_case name",
            expected="[a-z][a-z0-9_]*",
            received=value,
            canonical_id=canonical_id,
        )
    return value


@dataclass(frozen=True, slots=True, eq=False)
class LifecycleState:
    """One local immutable StateModel state authoring value."""

    name: str
    initial: bool = False
    terminal: bool = False

    def __post_init__(self) -> None:
        _require_name(self.name, parameter="lifecycle_state name", canonical_id="lifecycle_state")
        if type(self.initial) is not bool or type(self.terminal) is not bool:
            _invalid(
                "lifecycle_state initial and terminal must be bool",
                expected="initial: bool and terminal: bool",
                received={"initial": self.initial, "terminal": self.terminal},
                canonical_id="lifecycle_state",
            )


StateTrigger: TypeAlias = Ref[EventKind] | ParticipantRoleHandle


@dataclass(frozen=True, slots=True)
class Inception:
    """One trigger that seeds a lifecycle into its sole initial state."""

    on: StateTrigger

    def __post_init__(self) -> None:
        _require_trigger(self.on, canonical_id="inception")


@dataclass(frozen=True, slots=True)
class StateTransition:
    """One deterministic StateModel transition authoring value."""

    from_state: LifecycleState
    on: StateTrigger
    to_state: LifecycleState

    def __post_init__(self) -> None:
        if type(self.from_state) is not LifecycleState or type(self.to_state) is not LifecycleState:
            _invalid(
                "transition endpoints must be exact LifecycleState authoring values",
                expected="LifecycleState values returned by ms.lifecycle_state(...)",
                received={"from_state": self.from_state, "to_state": self.to_state},
                canonical_id="transition",
            )
        _require_trigger(self.on, canonical_id="transition")


@dataclass(frozen=True, slots=True)
class ModelStateHandle:
    """Project-neutral identity of one local state on a StateModel."""

    model: Ref[StateModelKind]
    name: str

    def __post_init__(self) -> None:
        if type(self.model) is not Ref or self.model.kind is not SemanticKind.STATE_MODEL:
            _invalid(
                "model_state model must be an exact Ref[state_model]",
                expected="Ref[state_model]",
                received=self.model,
                canonical_id="model_state",
            )
        _require_name(self.name, parameter="model_state name", canonical_id="model_state")

    @property
    def key(self) -> str:
        return f"{self.model.key}#state:{self.name}"


def _require_trigger(value: object, *, canonical_id: str) -> StateTrigger:
    if type(value) is Ref and value.kind is SemanticKind.EVENT:
        return value
    if type(value) is ParticipantRoleHandle:
        return value
    _raise(
        ErrorKind.INVALID_STATE_MODEL,
        "StateModel trigger must be one exact Event ref or ParticipantRoleHandle",
        cls=SemanticDecoratorError,
        expected="Ref[event] | ParticipantRoleHandle",
        received=repr(value),
        location=_user_caller_location(),
        constraint_id=ConstraintId.STATE_MODEL_TRIGGER,
        repair_value=repair(
            kind="reauthor",
            canonical_id=canonical_id,
            action="Pass an Event ref, or select one exact role with ms.participant_role(...).",
        ),
    )


def _trigger_ir(value: StateTrigger) -> StateTriggerDeclarationIR:
    if type(value) is Ref:
        return StateTriggerDeclarationIR(event_ref=value.path, participant_role=None)
    return StateTriggerDeclarationIR(
        event_ref=value.event.path,
        participant_role=value.name,
    )


def lifecycle_state(
    *,
    name: str,
    initial: bool = False,
    terminal: bool = False,
) -> LifecycleState:
    """Declare one immutable local StateModel state.

    Args:
        name: Lowercase snake-case state name.
        initial: Whether this is the model's sole initial state.
        terminal: Whether this state forbids outgoing transitions.

    Returns:
        A local state value accepted by ``ms.state_model(...)``.

    Example:
        >>> created = ms.lifecycle_state(name="created", initial=True)

    Constraints:
        ``name`` is lowercase snake_case; ``initial`` and ``terminal`` are bool.
    """
    return LifecycleState(name=name, initial=initial, terminal=terminal)


def inception(*, on: StateTrigger) -> Inception:
    """Declare a trigger from unseeded history into the initial state.

    Args:
        on: Exact Event ref or participant-role handle.

    Returns:
        An immutable inception declaration.

    Example:
        >>> seed = ms.inception(on=order_created)

    Constraints:
        ``on`` is one exact Event ref or ParticipantRoleHandle.
    """
    return Inception(on=on)


def transition(
    *,
    from_state: LifecycleState,
    on: StateTrigger,
    to_state: LifecycleState,
) -> StateTransition:
    """Declare one deterministic transition between local state values.

    Args:
        from_state: Exact source state included in the model.
        on: Exact Event ref or participant-role handle.
        to_state: Exact target state included in the model.

    Returns:
        An immutable transition declaration.

    Example:
        >>> paid_transition = ms.transition(
        ...     from_state=created, on=payment_captured, to_state=paid
        ... )

    Constraints:
        State membership and trigger role are resolved by ``ms.state_model(...)``.
    """
    return StateTransition(from_state=from_state, on=on, to_state=to_state)


def state_model(
    *,
    name: str,
    subject: Ref[EntityKind],
    states: tuple[LifecycleState, ...],
    transitions: tuple[Inception | StateTransition, ...],
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue | None = None,
) -> Ref[StateModelKind]:
    """Declare a closed normative lifecycle for one subject Entity.

    Args:
        name: Lowercase snake-case model name.
        subject: Exact Entity whose identities carry this lifecycle.
        states: Non-empty ordered tuple with exactly one initial state.
        transitions: Closed tuple of inception and ordinary transitions.
        domain: Optional explicit domain override.
        ai_context: Optional business definition and guardrails.

    Returns:
        Exact ``Ref[state_model]``.

    Example:
        >>> lifecycle = ms.state_model(
        ...     name="order_lifecycle",
        ...     subject=orders,
        ...     states=(created, paid),
        ...     transitions=(
        ...         ms.inception(on=order_created),
        ...         ms.transition(from_state=created, on=payment_captured, to_state=paid),
        ...     ),
        ... )

    Constraints:
        States are closed with exactly one initial state. Triggers must resolve
        to deterministic cardinality-one participant roles at ``subject``.
    """
    ctx = _require_ctx()
    model_name = _require_name(name, parameter="state_model name", canonical_id="state_model")
    subject_ref = _require_entity_ref(subject, parameter="subject")
    resolved_domain = _resolve_domain(domain, ctx)
    semantic_id = f"{resolved_domain}.{model_name}"
    ref = ref_factory.state_model(semantic_id)
    _check_duplicate(ctx, semantic_id, StateModelDeclarationIR)

    if type(states) is not tuple or not states:
        _invalid(
            "state_model states must be a non-empty tuple",
            expected="tuple[LifecycleState, ...] with exactly one initial state",
            received=states,
            canonical_id="state_model",
        )
    if any(type(state) is not LifecycleState for state in states):
        _invalid(
            "state_model states must contain exact LifecycleState values",
            expected="tuple[LifecycleState, ...]",
            received=states,
            canonical_id="state_model",
        )
    if len({state.name for state in states}) != len(states):
        _invalid(
            "state_model local state names must be unique",
            expected="unique LifecycleState.name values",
            received=tuple(state.name for state in states),
            canonical_id="state_model",
        )
    if sum(state.initial for state in states) != 1:
        _invalid(
            "state_model must contain exactly one initial state",
            expected="exactly one LifecycleState(initial=True)",
            received=sum(state.initial for state in states),
            canonical_id="state_model",
        )
    if type(transitions) is not tuple:
        _invalid(
            "state_model transitions must be a tuple",
            expected="tuple[Inception | StateTransition, ...]",
            received=transitions,
            canonical_id="state_model",
        )

    state_ids = {id(state) for state in states}
    inceptions: list[StateTriggerDeclarationIR] = []
    ordinary: list[tuple[str, StateTriggerDeclarationIR, str]] = []
    for item in transitions:
        if type(item) is Inception:
            inceptions.append(_trigger_ir(item.on))
            continue
        if type(item) is not StateTransition:
            _invalid(
                "state_model transitions contain an unsupported value",
                expected="Inception | StateTransition",
                received=item,
                canonical_id="state_model",
            )
        if id(item.from_state) not in state_ids or id(item.to_state) not in state_ids:
            _invalid(
                "transition states must be the exact authoring values present in states",
                expected="identity membership in the states tuple",
                received={"from": item.from_state.name, "to": item.to_state.name},
                canonical_id="state_model",
            )
        ordinary.append((item.from_state.name, _trigger_ir(item.on), item.to_state.name))

    declaration = StateModelDeclarationIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        name=model_name,
        subject=subject_ref.path,
        states=tuple(
            LifecycleStateIR(
                name=state.name,
                initial=state.initial,
                terminal=state.terminal,
            )
            for state in states
        ),
        inceptions=tuple(inceptions),
        transitions=tuple(ordinary),
        ai_context=_build_ai_context(ai_context),
        python_symbol=model_name,
        location=_caller_location(),
    )
    _push_ir(ctx, ref, declaration, None)
    return ref


def model_state(*, model: Ref[StateModelKind], name: str) -> ModelStateHandle:
    """Create the typed identity of one state on a StateModel.

    Args:
        model: Exact StateModel ref.
        name: Exact local state name.

    Returns:
        Immutable project-neutral state handle.

    Example:
        >>> paid = ms.model_state(
        ...     model=ms.ref.state_model("commerce.order_lifecycle"),
        ...     name="paid",
        ... )

    Constraints:
        Membership is resolved against the active catalog by the consumer.
    """
    return ModelStateHandle(model=model, name=name)


def _state_model_fingerprint(
    model_ref: Ref[StateModelKind],
    *,
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
) -> str:
    """Return the canonical transitive fingerprint for one loaded StateModel."""
    from marivo.semantic.metric_graph_canonical import fingerprint
    from marivo.semantic.metric_graph_lowering import dependency_digest

    digest = dependency_digest(
        registry,
        sidecar=sidecar,
        semantic_refs=(model_ref,),
    )
    return f"sha256:{fingerprint(digest)}"


def _resolve_model_state(
    handle: ModelStateHandle,
    *,
    registry: Registry,
    sidecar: CompiledExpressionSidecar,
) -> tuple[StateModelIR, str]:
    """Resolve exact current state membership and parent fingerprint."""
    if type(handle) is not ModelStateHandle:
        _raise(
            ErrorKind.MODEL_STATE_MISMATCH,
            "state consumer requires one exact ModelStateHandle",
            cls=SemanticRuntimeError,
            expected="ModelStateHandle returned by ms.model_state(...)",
            received=repr(handle),
            location=_user_caller_location(),
            constraint_id=ConstraintId.MODEL_STATE_MEMBERSHIP,
            repair_value=repair(
                kind="retry",
                canonical_id="model_state",
                action="Construct one exact state handle with ms.model_state(...).",
                snippet=(
                    "ms.model_state("
                    "model=ms.ref.state_model('commerce.order_lifecycle'), name='paid')"
                ),
            ),
        )
    model = registry.state_models.get(handle.model.path)
    if model is None:
        candidates = tuple(
            f"ms.ref.state_model({path!r})" for path in tuple(sorted(registry.state_models))[:6]
        )
        _raise(
            ErrorKind.MODEL_STATE_MISMATCH,
            "model_state parent is not present in the active compiled catalog",
            cls=SemanticRuntimeError,
            expected="a current loaded Ref[state_model]",
            received=handle.model.key,
            location=_user_caller_location(),
            constraint_id=ConstraintId.MODEL_STATE_MEMBERSHIP,
            repair_value=repair(
                kind="inspect",
                canonical_id="model_state",
                action="Inspect catalog.state_models and rebuild the handle from a current ref.",
                candidates=candidates,
            ),
        )
    available = tuple(state.name for state in model.states)
    if handle.name not in available:
        candidates = tuple(
            (f"ms.model_state(model=ms.ref.state_model({model.semantic_id!r}), name={name!r})")
            for name in available[:6]
        )
        _raise(
            ErrorKind.MODEL_STATE_MISMATCH,
            "model_state name is not owned by its current StateModel definition",
            cls=SemanticRuntimeError,
            expected=f"one of {available!r}",
            received=handle.name,
            location=_user_caller_location(),
            refs=(model.semantic_id,),
            constraint_id=ConstraintId.MODEL_STATE_MEMBERSHIP,
            repair_value=repair(
                kind="retry",
                canonical_id="model_state",
                action="Choose one exact state name from the current model.",
                snippet=candidates[0] if candidates else None,
                candidates=candidates,
            ),
        )
    return (
        model,
        _state_model_fingerprint(
            handle.model,
            registry=registry,
            sidecar=sidecar,
        ),
    )


__all__ = [
    "Inception",
    "LifecycleState",
    "ModelStateHandle",
    "StateTransition",
    "inception",
    "lifecycle_state",
    "model_state",
    "state_model",
    "transition",
]

_register_authoring_file(__file__)
