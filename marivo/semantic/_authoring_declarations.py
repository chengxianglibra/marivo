"""Domain namespaces and pure local Metric declarations."""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

from ibis.common.deferred import Deferred

from marivo._symbolic import (
    DomainSymbol,
    native_symbol_ref,
    symbol_factory,
)
from marivo.refs import SemanticKind
from marivo.refs import ref as ref_factory
from marivo.semantic._authoring_context import (
    _caller_location,
    _push_ir,
    _register_authoring_file,
    _require_ctx,
)
from marivo.semantic._authoring_retired import reject_parameters, retired_factory
from marivo.semantic._authoring_values import _build_ai_context
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import ErrorKind, SemanticDecoratorError, _raise
from marivo.semantic.ir import DomainIR
from marivo.semantic.typing import AiContextValue


def domain(
    *,
    name: str,
    owner: str,
    default: bool = True,
    ai_context: AiContextValue | None = None,
) -> DomainSymbol:
    """Declare a semantic domain namespace inside a project file.

    A domain groups entities, dimensions, metrics, and relationships under a single
    qualified name (``<domain>.<object>``). Must be called at module top-level
    inside a ``models/semantic/<model>/*.py`` project file.

    Args:
        name: Domain namespace, e.g. ``"sales"``.
        owner: Human owner accountable for this domain's semantic correctness
            and quality.
        default: If True, subsequent decorators in this file resolve to this
            domain when no explicit ``domain=`` kwarg is passed.
        ai_context: Optional ``AiContextValue`` from ``ms.ai_context(...)`` with extra
            agent-facing hints.

    Returns:
        A native ``DomainSymbol`` that can be passed as the ``domain=`` kwarg to other
        decorators to override the default domain context.

    Raises:
        OutsideLoaderContextError: Called outside a semantic loader pass.
        SemanticDecoratorError: ``name`` collides with another domain in the project.

    Example:
        >>> import marivo.semantic as ms
        >>> sales = ms.domain(name="sales", owner="Mina Zhang", default=True)
    """
    ctx = _require_ctx()
    ref = ref_factory.domain(name)
    if not isinstance(owner, str) or not owner.strip():
        _raise(
            ErrorKind.INVALID_DOMAIN_OWNER,
            f"{name!r}: owner must be a non-empty string; got {owner!r}.",
            cls=SemanticDecoratorError,
            constraint_id=ConstraintId.DOMAIN_OWNER_REQUIRED,
        )
    ai_ctx = _build_ai_context(ai_context)
    location = _caller_location()

    ir = DomainIR(
        name=name,
        owner=owner,
        default=default,
        ai_context=ai_ctx,
        location=location,
    )
    _push_ir(ctx, ref, ir, None)

    if default:
        ctx.default_domain = name

    return symbol_factory.domain(ref.path)


@reject_parameters(
    "name",
    "entities",
    "additivity",
    "time",
    "status_time_dimension",
    "status_time_fold",
    "nulls",
    "empty",
    "zero_denominator",
    "root_entity",
    "fanout_policy",
    "unit",
    "time_via",
    "fold",
)
def metric(
    *,
    domain: DomainSymbol | None = None,
    ai_context: AiContextValue | None = None,
) -> Callable[[Callable[[], Deferred]], Deferred]:
    """Name a completed Metric without changing its computation or policies.

    Args:
        domain: Optional declared domain ref; model collection resolves omitted domains.
        ai_context: Optional business meaning and guardrails from ms.ai_context(...).
    Returns:
        A decorator binding the function name to an immutable Metric declaration.
    Example:
        >>> @ms.metric()
        ... def revenue():
        ...     return amount.sum(time=paid_at)
    Constraints:
        Use a zero-argument pure body over constructed dependencies. No loader or
        Session is required. Source roots and operator policies belong to the expression.
        Declaration does not publish a Catalog Metric or execute a query.
        To publish, save at model module top level and load; retrieve
        ``metric:<domain>.<function_name>`` with ``catalog.get(...)``.
    """
    from marivo._definition.deferred import DeclarationRecord, declare
    from marivo._definition.types import SourceLocation
    from marivo.refs import _validate_segment
    from marivo.semantic._expression_binding import construct_metric_expression
    from marivo.semantic.errors import SemanticExpressionError

    domain_key = native_symbol_ref(domain)
    if domain is not None and (domain_key is None or domain_key.kind is not SemanticKind.DOMAIN):
        raise SemanticExpressionError(
            kind=ErrorKind.INVALID_EXPRESSION_VALUE,
            message="Metric domain must be a native Domain symbol.",
            expected="a native Domain symbol from ms.ref.domain(...), or None",
            received=repr(domain),
            action="Pass the native Domain symbol, or omit domain until model collection.",
        )
    _build_ai_context(ai_context)

    def decorator(fn: Callable[[], Deferred]) -> Deferred:
        location = _caller_location()
        try:
            _validate_segment(fn.__name__, role="Metric declaration name")
        except ValueError as error:
            raise SemanticExpressionError(
                kind=ErrorKind.INVALID_EXPRESSION_VALUE,
                message="Metric declaration name is invalid.",
                expected="a function name matching [a-z][a-z0-9_]*",
                received=fn.__name__,
                action="Rename the declaration function to a lowercase snake_case name.",
                location_label=f"{location.file}:{location.line}",
            ) from error
        try:
            value = construct_metric_expression(fn)
            return declare(
                value,
                DeclarationRecord(
                    kind="metric",
                    name=fn.__name__,
                    domain=ref_factory.domain(domain_key.path) if domain_key is not None else None,
                    ai_context=ai_context,
                    location=SourceLocation(location.file, location.line),
                ),
            )
        except SemanticExpressionError as error:
            error.location = location
            if not error.semantic_refs:
                error.semantic_refs = (fn.__name__,)
            raise

    return decorator


# Removed from public exports; retained solely to reject old authored source.


def aggregate(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired aggregate declaration; no legacy shape is accepted."""
    retired_factory("aggregate", kwargs)


def weighted_mean(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired weighted_mean declaration; no legacy shape is accepted."""
    retired_factory("weighted_mean", kwargs)


def where(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired where declaration; no legacy shape is accepted."""
    retired_factory("where", kwargs)


def count(*_args: object, **kwargs: object) -> NoReturn:
    """Reject the retired count declaration; no legacy shape is accepted."""
    retired_factory("count", kwargs)


_register_authoring_file(__file__)
