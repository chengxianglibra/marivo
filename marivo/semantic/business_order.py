"""Closed authoring values for same-subject business event order."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias

from marivo.refs import (
    BusinessOrderKind,
    DimensionKind,
    DomainKind,
    EntityKind,
    EventKind,
    Ref,
    SemanticKind,
)
from marivo.refs import ref as ref_factory
from marivo.semantic._authoring_context import (
    _caller_location,
    _check_duplicate,
    _push_ir,
    _register_authoring_file,
    _require_ctx,
    _require_entity_ref,
    _require_ref_id,
    _resolve_domain,
    _user_caller_location,
)
from marivo.semantic._authoring_values import _build_ai_context
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise, repair
from marivo.semantic.event import ParticipantRoleHandle
from marivo.semantic.ir import (
    BusinessOrderDeclarationIR,
    EventPrecedenceIR,
    EventSequenceDeclarationIR,
)
from marivo.semantic.typing import AiContextValue

if TYPE_CHECKING:
    from marivo.semantic._expression_binding import CompiledExpressionSidecar
    from marivo.semantic.validator import Registry

SequenceOrder: TypeAlias = Literal["integer"] | tuple[str, ...]
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _invalid(message: str, *, expected: str, received: object, target: str) -> NoReturn:
    _raise(
        ErrorKind.INVALID_BUSINESS_ORDER,
        message,
        cls=SemanticDecoratorError,
        expected=expected,
        received=repr(received),
        location=_user_caller_location(),
        repair_value=repair(
            kind="reauthor",
            canonical_id=target,
            action="Declare exact Event roles and one explicit, comparable business order.",
        ),
    )


@dataclass(frozen=True, slots=True)
class EventSequence:
    """An Event source field with a declared business value order."""

    event: Ref[EventKind]
    value: Ref[DimensionKind]
    order: SequenceOrder


@dataclass(frozen=True, slots=True)
class EventPrecedence:
    """One exact same-subject precedence between participant roles."""

    before: ParticipantRoleHandle
    after: ParticipantRoleHandle


def event_sequence(
    event: Ref[EventKind], value: Ref[DimensionKind], *, order: SequenceOrder
) -> EventSequence:
    """Declare one Event source field's business sequence.

    Args:
        event: Exact Event ref whose occurrence source owns the field.
        value: Exact categorical Dimension ref for the sequence field.
        order: ``"integer"`` or an explicit ordered tuple of at least two unique strings.

    Returns:
        Immutable sequence authoring value.

    Example:
        >>> sequence = ms.event_sequence(payment, sequence_number, order="integer")

    Constraints:
        ``ms.load()`` checks ownership and Subject mapping. R7 checks actual
        values, per-subject uniqueness, and ordering at consumption.
    """
    _require_ref_id(event, parameter="event", expected=(SemanticKind.EVENT,))
    _require_ref_id(value, parameter="value", expected=(SemanticKind.DIMENSION,))
    if order != "integer" and (
        type(order) is not tuple
        or len(order) < 2
        or any(type(item) is not str or not item for item in order)
        or len(set(order)) != len(order)
    ):
        _invalid(
            "event_sequence order must be integer or an explicit unique value order",
            expected="'integer' | tuple[str, ...] with at least two unique values",
            received=order,
            target="event_sequence",
        )
    return EventSequence(event=event, value=value, order=order)


def precedes(before: ParticipantRoleHandle, after: ParticipantRoleHandle) -> EventPrecedence:
    """Declare one precedence between simultaneous Event participant roles.

    Args:
        before: Exact first participant role.
        after: Exact later participant role on the same Subject.

    Returns:
        Immutable precedence authoring value.

    Example:
        >>> edge = ms.precedes(activated_role, deactivated_role)

    Constraints:
        ``ms.load()`` checks role membership, same Subject, and cycles.
    """
    if type(before) is not ParticipantRoleHandle or type(after) is not ParticipantRoleHandle:
        _invalid(
            "precedence endpoints must be exact participant role handles",
            expected="two ParticipantRoleHandle values",
            received=(before, after),
            target="precedes",
        )
    if before == after:
        _invalid(
            "a participant role cannot precede itself",
            expected="distinct role handles",
            received=before,
            target="precedes",
        )
    return EventPrecedence(before=before, after=after)


def business_order(
    *,
    name: str,
    subject: Ref[EntityKind],
    sequences: tuple[EventSequence, ...] = (),
    conflicts: tuple[EventPrecedence, ...] = (),
    domain: Ref[DomainKind] | None = None,
    ai_context: AiContextValue,
) -> Ref[BusinessOrderKind]:
    """Declare one named order authority over Events of the same Subject.

    Args:
        name: Stable lowercase snake-case name.
        subject: Exact Subject Entity with a complete non-empty identity.
        sequences: Sequence field declarations for Event occurrences.
        conflicts: Closed precedence edges for simultaneous roles.
        domain: Optional exact domain override.
        ai_context: Required business rationale with a non-empty definition.

    Returns:
        Exact ``Ref[business_order]``.

    Example:
        >>> order = ms.business_order(
        ...     name="payment_order", subject=orders,
        ...     sequences=(ms.event_sequence(payment, sequence_number, order="integer"),),
        ...     ai_context=ms.ai_context(business_definition="Ledger sequence per order"),
        ... )

    Constraints:
        Declares business authority only. R7 verifies source values and whether
        all still-allowed orders preserve the requested result and trace.
    """
    ctx = _require_ctx()
    if type(name) is not str or not _NAME.fullmatch(name):
        _invalid(
            "business_order name must be lowercase snake_case",
            expected="[a-z][a-z0-9_]*",
            received=name,
            target="business_order",
        )
    subject_ref = _require_entity_ref(subject, parameter="subject")
    if type(sequences) is not tuple or any(type(item) is not EventSequence for item in sequences):
        _invalid(
            "sequences must contain exact EventSequence values",
            expected="tuple[EventSequence, ...]",
            received=sequences,
            target="business_order",
        )
    if type(conflicts) is not tuple or any(type(item) is not EventPrecedence for item in conflicts):
        _invalid(
            "conflicts must contain exact EventPrecedence values",
            expected="tuple[EventPrecedence, ...]",
            received=conflicts,
            target="business_order",
        )
    if not sequences and not conflicts:
        _invalid(
            "business_order must declare an order rule",
            expected="at least one sequence or precedence",
            received=(),
            target="business_order",
        )
    context = _build_ai_context(ai_context)
    if type(context.business_definition) is not str or not context.business_definition.strip():
        _invalid(
            "business_order needs an explicit business rationale",
            expected="non-empty ai_context.business_definition",
            received=context.business_definition,
            target="business_order",
        )
    domain_id = _resolve_domain(domain, ctx)
    semantic_id = f"{domain_id}.{name}"
    ref = ref_factory.business_order(semantic_id)
    _check_duplicate(ctx, semantic_id, BusinessOrderDeclarationIR)
    _push_ir(
        ctx,
        ref,
        BusinessOrderDeclarationIR(
            semantic_id=semantic_id,
            domain=domain_id,
            name=name,
            subject=subject_ref.path,
            sequences=tuple(
                EventSequenceDeclarationIR(item.event.path, item.value.path, item.order)
                for item in sequences
            ),
            conflicts=tuple(
                EventPrecedenceIR(
                    item.before.event.path,
                    item.before.name,
                    item.after.event.path,
                    item.after.name,
                )
                for item in conflicts
            ),
            ai_context=context,
            python_symbol=name,
            location=_caller_location(),
        ),
        None,
    )
    return ref


def _business_order_fingerprint(
    order_ref: Ref[BusinessOrderKind], *, registry: Registry, sidecar: CompiledExpressionSidecar
) -> str:
    """Return the transitive fingerprint for one loaded business order."""
    from marivo.semantic.metric_graph_canonical import fingerprint
    from marivo.semantic.metric_graph_lowering import dependency_digest

    digest = dependency_digest(registry, sidecar=sidecar, semantic_refs=(order_ref,))
    return f"sha256:{fingerprint(digest)}"


_register_authoring_file(__file__)

__all__ = ["EventPrecedence", "EventSequence", "business_order", "event_sequence", "precedes"]
