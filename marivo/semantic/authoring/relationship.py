"""Capture directed Relationship declarations."""

from __future__ import annotations

from marivo._kernel.refs import SemanticKind
from marivo._kernel.refs import ref as ref_factory
from marivo._kernel.symbolic import (
    DomainSymbol,
    EntitySymbol,
    RelationshipSymbol,
    symbol_factory,
)
from marivo.semantic.authoring.context import (
    _caller_location,
    _check_duplicate,
    _push_ir,
    _register_authoring_file,
    _require_ctx,
    _require_ref_id,
    _resolve_domain,
)
from marivo.semantic.authoring.validation import (
    _validate_relationship_keys,
)
from marivo.semantic.authoring.values import _build_ai_context
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import (
    ErrorKind,
    SemanticDecoratorError,
    _raise,
)
from marivo.semantic.expressions.objects import _Entity as Entity
from marivo.semantic.ir import (
    JoinKey,
    RelationshipIR,
)
from marivo.semantic.typing import AiContextValue

_register_authoring_file(__file__)


def relationship(
    *,
    name: str,
    from_entity: Entity | EntitySymbol,
    to_entity: Entity | EntitySymbol,
    keys: list[JoinKey],
    domain: DomainSymbol | None = None,
    ai_context: AiContextValue | None = None,
) -> RelationshipSymbol:
    """Declare a join relationship between two entities.

    Top-level call (not a decorator). Used by the compiler to plan joins when a
    metric or dimension references dimensions across related entities.

    Args:
        name: Required relationship name.
        from_entity: Source entity ref.
        to_entity: Target entity ref.
        keys: List of ``ms.join_on(from_key, to_key)`` pairs.
        domain: Override the active domain namespace with a ``DomainSymbol`` returned
            by ``ms.domain(...)``. Defaults to the file's default domain.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra agent-facing hints.

    Returns:
        A native Relationship lookup symbol for directed path parameters.

    Raises:
        SemanticDecoratorError: A ref or key shape is invalid.

    Example:
        >>> ms.relationship(
        ...     name="orders_to_customers",
        ...     from_entity=orders, to_entity=customers,
        ...     keys=[ms.join_on(customer_id, id)],
        ... )

    Constraints:
        Load derives structural multiplicity from complete endpoint identity key
        coverage. The relationship declares no global required-match policy.
        Each consumer decides whether its selected members require a match and
        what an allowed absence means. Physical multiplicity can be audited through validate. Analysis trusts
        required matching as an unverified premise; a versioned endpoint needs an exact
        version selection before its one side can be consumed.

    Capture constraints: The result is an immutable native lookup symbol. It
    supports forward composition; the loader validates and binds the complete
    model before the Catalog returns closed values for analysis.
    """
    ctx = _require_ctx()
    resolved_domain = _resolve_domain(domain, ctx)

    semantic_id = f"{resolved_domain}.{name}"
    ref = ref_factory.relationship(semantic_id)
    _check_duplicate(ctx, semantic_id, RelationshipIR)

    from_ds = _require_ref_id(
        from_entity,
        parameter="from_entity",
        expected=(SemanticKind.ENTITY,),
    )
    to_ds = _require_ref_id(
        to_entity,
        parameter="to_entity",
        expected=(SemanticKind.ENTITY,),
    )
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()

    resolved_keys = _validate_relationship_keys(tuple(keys), semantic_id=semantic_id)

    if not resolved_keys:
        _raise(
            ErrorKind.INVALID_REF,
            "ms.relationship(keys=...) requires at least one ms.join_on(from_key, to_key) pair.",
            cls=SemanticDecoratorError,
            refs=(semantic_id,),
            constraint_id=ConstraintId.REF_SHAPE,
        )

    ir = RelationshipIR(
        semantic_id=semantic_id,
        domain=resolved_domain,
        name=name,
        from_entity=from_ds,
        to_entity=to_ds,
        keys=resolved_keys,
        ai_context=ai_ctx,
        location=location,
    )
    _push_ir(ctx, ref, ir, None)

    return symbol_factory.relationship(ref.path)
