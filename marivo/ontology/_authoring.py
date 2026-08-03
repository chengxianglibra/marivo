"""Relation-specific ontology authoring constructors."""

from __future__ import annotations

import inspect
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal, NoReturn, cast

from marivo._authoring.model import AuthoringRepair
from marivo.introspection.live.model import LiveHelpTarget
from marivo.ontology.errors import InvalidOntologyRefError, InvalidSemanticEdgeError
from marivo.ontology.types import (
    OntologyEndpointRef,
    OntologyOutcomeRef,
    SemanticEdgeIR,
    SemanticEdgeRef,
    _make_semantic_edge_ref,
)
from marivo.refs import Ref, SemanticKind, SemanticKindTag
from marivo.semantic._authoring_values import _build_ai_context
from marivo.semantic.ir import SourceLocation
from marivo.semantic.typing import AiContextValue

_EDGE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_ENDPOINT_KINDS = {SemanticKind.ENTITY, SemanticKind.MEASURE, SemanticKind.METRIC}


@dataclass(slots=True)
class _OntologyAuthoringContext:
    edges: list[SemanticEdgeIR] = field(default_factory=list)


_CONTEXT: ContextVar[_OntologyAuthoringContext | None] = ContextVar(
    "_marivo_ontology_authoring_context", default=None
)


def _location() -> SourceLocation:
    frame = inspect.currentframe()
    try:
        caller = frame.f_back if frame is not None else None
        while caller is not None:
            if not caller.f_code.co_filename.startswith(str(__file__).rsplit("/ontology/", 1)[0]):
                return SourceLocation(file=caller.f_code.co_filename, line=caller.f_lineno)
            caller = caller.f_back
    finally:
        del frame
    return SourceLocation(file="<unknown>", line=0)


def _repair(action: str) -> AuthoringRepair:
    return AuthoringRepair(
        kind="reauthor",
        help_target=LiveHelpTarget(surface="ontology", canonical_id="authoring"),
        action=action,
    )


def _invalid_ref(*, parameter: str, value: object, expected: str) -> NoReturn:
    entry_ref = getattr(value, "ref", None)
    conversion = (
        f" Pass {type(value).__name__}.ref instead of the catalog entry."
        if type(entry_ref) is Ref
        else ""
    )
    raise InvalidOntologyRefError(
        kind="invalid_ontology_ref",
        message=f"{parameter} must be an exact typed semantic Ref.{conversion}",
        expected=expected,
        received=f"{type(value).__name__}: {value!r}",
        location=_location(),
        repair=_repair("Pass the exact .ref from a current semantic catalog entry."),
    )


def _endpoint(value: object, *, parameter: str, allowed: set[SemanticKind]) -> OntologyEndpointRef:
    if type(value) is not Ref:
        _invalid_ref(
            parameter=parameter,
            value=value,
            expected=" | ".join(f"Ref[{kind.value}]" for kind in sorted(allowed, key=str)),
        )
    ref = cast("Ref[SemanticKindTag]", value)
    if ref.kind not in allowed:
        _invalid_ref(
            parameter=parameter,
            value=value,
            expected=" | ".join(f"Ref[{kind.value}]" for kind in sorted(allowed, key=str)),
        )
    return cast("OntologyEndpointRef", ref)


def _edge(
    *,
    name: str,
    relation: Literal["influences", "related_to"],
    source: OntologyEndpointRef,
    target: OntologyEndpointRef,
    ai_context: AiContextValue,
) -> SemanticEdgeRef:
    location = _location()
    if type(name) is not str or not _EDGE_NAME_RE.fullmatch(name):
        raise InvalidSemanticEdgeError(
            kind="invalid_semantic_edge",
            message="edge name must be a lowercase dotted snake_case identity",
            expected="^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)*$",
            received=repr(name),
            location=location,
            repair=_repair("Rename the edge with lowercase snake_case path segments."),
        )
    try:
        context = _build_ai_context(ai_context)
    except Exception as error:
        if getattr(error, "kind", None) != "invalid_ai_context":
            raise
        raise InvalidSemanticEdgeError(
            kind="invalid_ai_context",
            message=str(getattr(error, "message", error)),
            expected="AiContextValue from ms.ai_context(...) ",
            received=type(ai_context).__name__,
            location=location,
            repair=_repair("Construct ai_context with ms.ai_context(...)."),
        ) from error
    if not context.business_definition or not context.business_definition.strip():
        raise InvalidSemanticEdgeError(
            kind="invalid_semantic_edge",
            message="ontology edges require a non-empty ai_context.business_definition",
            expected="ms.ai_context(business_definition='<business meaning>')",
            received=repr(context.business_definition),
            location=location,
            repair=_repair("Add the edge-specific business meaning to ai_context."),
        )
    if relation == "related_to" and source == target:
        raise InvalidSemanticEdgeError(
            kind="invalid_semantic_edge",
            message="related_to requires two distinct endpoint identities",
            expected="two different EntityRef, MeasureRef, or MetricRef values",
            received=source.key,
            location=location,
            repair=_repair("Choose a distinct related endpoint."),
        )
    ctx = _CONTEXT.get()
    if ctx is None:
        raise InvalidSemanticEdgeError(
            kind="invalid_semantic_edge",
            message="ontology constructors are only valid while loading models/ontology.py",
            expected="an authored call executed by mo.load(...) ",
            received="constructor call outside ontology loading",
            location=location,
            repair=_repair("Move the declaration into models/ontology.py and call mo.load(...)."),
        )
    ref = _make_semantic_edge_ref(name)
    if any(edge.ref == ref for edge in ctx.edges):
        raise InvalidSemanticEdgeError(
            kind="invalid_semantic_edge",
            message=f"duplicate ontology edge name {name!r}",
            expected="a unique edge name within models/ontology.py",
            received=name,
            location=location,
            repair=_repair("Rename or remove the duplicate edge declaration."),
        )
    if relation == "related_to":
        pair = tuple(sorted((source.key, target.key)))
        for existing in ctx.edges:
            if (
                existing.relation == "related_to"
                and tuple(sorted((existing.source.key, existing.target.key))) == pair
            ):
                raise InvalidSemanticEdgeError(
                    kind="invalid_semantic_edge",
                    message="duplicate related_to endpoint pair",
                    expected="one assertion per canonical endpoint pair",
                    received=f"{pair[0]} <-> {pair[1]}",
                    location=location,
                    repair=_repair("Keep one related_to declaration for this pair."),
                )
        if target.key < source.key:
            source, target = target, source
    ctx.edges.append(
        SemanticEdgeIR(
            ref=ref,
            relation=relation,
            source=source,
            target=target,
            context=context,
            location=location,
        )
    )
    return ref


def influences(
    *,
    name: str,
    driver: OntologyEndpointRef,
    outcome: OntologyOutcomeRef,
    ai_context: AiContextValue,
) -> SemanticEdgeRef:
    """Author a directional, non-causal driver hypothesis.

    Args:
        name: Unique lowercase dotted snake_case edge identity.
        driver: Entity, Measure, or Metric ref proposed as a driver.
        outcome: Entity or Metric ref matched as the outcome anchor.
        ai_context: Required business definition and optional edge guardrails.

    Returns:
        The immutable SemanticEdgeRef registered in the active ontology load.

    Example:
        >>> refund_pressure = mo.influences(
        ...     name="refund_pressure",
        ...     driver=refund_rate_ref,
        ...     outcome=healthy_order_rate_ref,
        ...     ai_context=ms.ai_context(
        ...         business_definition="Refunds may degrade order health."
        ...     ),
        ... )

    Constraints:
        Represents discovery guidance only; it does not assert causality or executable semantics.
    """
    return _edge(
        name=name,
        relation="influences",
        source=_endpoint(driver, parameter="driver", allowed=_ENDPOINT_KINDS),
        target=_endpoint(
            outcome,
            parameter="outcome",
            allowed={SemanticKind.ENTITY, SemanticKind.METRIC},
        ),
        ai_context=ai_context,
    )


def related_to(
    *,
    name: str,
    left: OntologyEndpointRef,
    right: OntologyEndpointRef,
    ai_context: AiContextValue,
) -> SemanticEdgeRef:
    """Author a symmetric contextual relation between semantic refs.

    Args:
        name: Unique lowercase dotted snake_case edge identity.
        left: Entity, Measure, or Metric endpoint.
        right: A distinct Entity, Measure, or Metric endpoint.
        ai_context: Required business definition and optional edge guardrails.

    Returns:
        The immutable SemanticEdgeRef registered in the active ontology load.

    Example:
        >>> edge = mo.related_to(
        ...     name="refund_and_support_pressure",
        ...     left=refund_rate_ref,
        ...     right=support_ticket_rate_ref,
        ...     ai_context=ms.ai_context(
        ...         business_definition="Both describe order friction."
        ...     ),
        ... )

    Constraints:
        Does not imply joinability, statistical association, or executable semantics.
    """
    return _edge(
        name=name,
        relation="related_to",
        source=_endpoint(left, parameter="left", allowed=_ENDPOINT_KINDS),
        target=_endpoint(right, parameter="right", allowed=_ENDPOINT_KINDS),
        ai_context=ai_context,
    )


__all__ = ["influences", "related_to"]
