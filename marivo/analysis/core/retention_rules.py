"""Fixed-population retention and explicit Subject quantification rules."""

from dataclasses import replace
from hashlib import sha256
from typing import Literal

from marivo.analysis.anchors import AnyAnchor, CalendarWindow, ElapsedWindow, EveryAnchor
from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.model import (
    AnchorDomainPart,
    DerivedQuantity,
    InstanceRetentionPart,
    OccurrencePart,
    Part,
    Signature,
    SubjectPart,
    SubjectRetentionPart,
    require_part,
    validate_part,
)
from marivo.analysis.core.predicates import leaves
from marivo.analysis.core.rules import (
    AnchorRetention,
    PartsTransport,
    RetentionBySubject,
    RuleDerivation,
    _result,
)


def validate(part: InstanceRetentionPart | SubjectRetentionPart) -> None:
    if part.version != "v1" or part.selection not in (
        "full",
        "true",
        "false",
        "unknown",
        "predicate",
    ):
        fail("retention_binding", "invalid retained scope or selection version")
    if isinstance(part, SubjectRetentionPart):
        validate(part.instances)
        if part.binding != part.instances.binding or type(part.rule) not in (
            AnyAnchor,
            EveryAnchor,
        ):
            fail(
                "retention_binding",
                "Subject quantification must bind its complete original instances",
            )
        if part.instances.selection != "full":
            fail("retention_omega", "Subject quantification requires the full instance Omega")
        return
    validate_part(part.anchors)
    validate_part(part.returning)
    if (
        part.binding != part.anchors.binding
        or part.binding != part.returning.binding
        or part.returning_domain.kind != "occurrence"
        or part.returning_domain.binding != part.binding
        or len(part.returning.events) != 1
        or type(part.window) not in (ElapsedWindow, CalendarWindow)
        or part.returning.events[0].subject.ref != part.anchors.preparation.events[0].subject.ref
    ):
        fail("retention_binding", "exact Anchor, return Event and same Subject are required")


def derive(
    inputs: tuple[Signature, ...], params: AnchorRetention | RetentionBySubject
) -> RuleDerivation:
    source = inputs[0]
    subject = require_part(source, "subject")
    assert isinstance(subject, SubjectPart)
    domain = source.domain
    if isinstance(params, AnchorRetention):
        if len(inputs) != 2 or domain.kind != "anchor":
            fail(
                "retention_binding",
                "one complete Anchor domain and captured return input are required",
            )
        anchors = require_part(source, "anchor")
        returning = require_part(inputs[1], "occurrences")
        if not isinstance(anchors, AnchorDomainPart) or not isinstance(returning, OccurrencePart):
            fail(
                "retention_binding",
                "starts and returning occurrences must retain their own authority",
            )
        part: InstanceRetentionPart | SubjectRetentionPart = InstanceRetentionPart(
            domain.binding, anchors, returning, inputs[1].domain, params.window
        )
        method = "anchor.retention@v1"
    else:
        instances = require_part(source, "retention")
        if (
            len(inputs) != 1
            or not isinstance(instances, InstanceRetentionPart)
            or instances.selection != "full"
        ):
            fail("retention_omega", "by_subject requires the full original instance result")
        domain = params.output
        if (
            domain.kind != "entity"
            or domain.instance_key != subject.subject_key
            or domain.binding != source.domain.binding
        ):
            fail("retention_binding", "Subject Omega must be the exact full-key Anchor image")
        part = SubjectRetentionPart(domain.binding, instances, params.rule)
        subject = replace(subject, source_key=subject.subject_key, injective=True)
        method = "retention.by_subject@v1"
    validate(part)
    quantity = DerivedQuantity(
        sha256(repr((inputs, params)).encode()).hexdigest(),
        method,
        (source.domain.definition_id,),
        None,
        domain.binding.scope_id,
        "retained_retention_truth",
    )
    return _result(
        "retention@v1",
        inputs,
        domain,
        quantity,
        (subject, part),
        pre=(),
        required=("subject",),
        created=("retention",),
        post=(),
        obligations=(),
        eval_id=method,
    )


def transport(part: Part, params: PartsTransport) -> Part:
    if (
        not isinstance(part, (InstanceRetentionPart, SubjectRetentionPart))
        or params.mode != "where"
    ):
        return part
    if part.selection in ("true", "false", "unknown"):
        return part
    selection: Literal["full", "true", "false", "unknown", "predicate"] = (
        part.selection if part.selection in ("true", "false", "unknown") else "predicate"
    )
    for tree in params.predicates:
        children = tuple(leaves(tree))
        # Only a direct positive equality certifies every retained row as true.
        if (
            tree.operator == "eq"
            and tree.value is True
            and tree.input_index == 0
            and tree.right_index is None
        ):
            selection = "true"
        elif (
            tree.operator == "eq"
            and tree.value is False
            and tree.input_index == 0
            and tree.right_index is None
        ):
            selection = "false"
        elif (
            tree.operator == "not_"
            and len(children) == 1
            and children[0].operator == "is_defined"
            and children[0].input_index == 0
            and children[0].right_index is None
        ):
            selection = "unknown"
    return replace(part, selection=selection)
