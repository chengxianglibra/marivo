"""Anchor domain and relative observation derivation under the common core."""

from dataclasses import replace
from datetime import datetime

from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.model import (
    AnchorDomainPart,
    AnchorObservationPart,
    JourneyPart,
    OccurrencePart,
    OriginalStatePart,
    Signature,
    SubjectPart,
    require_part,
    validate_part,
)
from marivo.analysis.core.rules import (
    AnchorBind,
    AnchorObserve,
    ObserveCount,
    ObserveMetric,
    OccurrenceCombine,
    OriginalRatio,
    RuleDerivation,
    _observe_metric,
    _occurrence_combine,
    _original_ratio,
    _result,
)


def validate(part: AnchorDomainPart | AnchorObservationPart) -> None:
    if isinstance(part, AnchorObservationPart):
        validate(part.domain)
        if (
            part.binding != part.domain.binding
            or part.version != "v1"
            or (
                len(part.component_roots) != len(part.component_types)
                or not part.component_roots
                or part.components
                != (
                    *part.domain.components,
                    "deadline",
                    *(f"uses_{i}" for i in range(len(part.component_roots))),
                )
            )
        ):
            fail("anchor_binding", "invalid relative component/window declaration")
        return
    validate_part(part.preparation)
    if part.journey is not None:
        validate_part(part.journey)
        if part.journey.preparation != part.preparation:
            fail("anchor_binding", "Journey assignment differs from its captured start")
    if (
        part.version != "v1"
        or part.binding != part.preparation.binding
        or (
            datetime.fromisoformat(part.during_start).utcoffset() is None
            or not datetime.fromisoformat(part.during_start)
            < datetime.fromisoformat(part.during_end)
            or part.components
            != (
                "started_at",
                "sequence_int",
                "sequence_enum",
                *(("assignment",) if part.journey else ()),
            )
        )
    ):
        fail("anchor_binding", "invalid exact Anchor domain declaration")


def observation_template(
    population: Signature,
    observations: tuple[ObserveMetric | ObserveCount, ...],
    composition: OriginalRatio | OccurrenceCombine | None,
) -> Signature:
    """Derive the relative Metric template without synthetic graph node identities."""
    prototypes = tuple(
        _observe_metric((population,), observation, prepared=True).output
        for observation in observations
    )
    if isinstance(composition, OriginalRatio):
        return _original_ratio(prototypes, composition).output
    if isinstance(composition, OccurrenceCombine):
        return _occurrence_combine(prototypes, composition).output
    if len(prototypes) == 1:
        return prototypes[0]
    fail("anchor_metric", "a closed count/sum/ratio/linear Metric is required")


def derive(inputs: tuple[Signature, ...], params: AnchorBind | AnchorObserve) -> RuleDerivation:
    if isinstance(params, AnchorBind):
        if len(inputs) != 1 or inputs[0].domain.kind not in ("occurrence", "journey"):
            fail("anchor_binding", "one captured Event or retained Journey is required")
        source = inputs[0]
        subject = require_part(source, "subject")
        assert isinstance(subject, SubjectPart)
        journey = next((p for p in source.parts if isinstance(p, JourneyPart)), None)
        capture = journey.preparation if journey else require_part(source, "occurrences")
        assert isinstance(capture, OccurrencePart)
        keys = (
            source.domain.instance_key
            if journey
            else (*subject.subject_key, *source.domain.instance_key)
        )
        if params.output.kind != "anchor" or params.output.instance_key != keys:
            fail("anchor_binding", "Anchor identity must retain complete Subject/start keys")
        part = AnchorDomainPart(
            source.domain.binding, capture, params.during_start, params.during_end, journey
        )
        if journey:
            part = replace(part, components=(*part.components, "assignment"))
        validate(part)
        return _result(
            "anchor@v1",
            inputs,
            params.output,
            None,
            (replace(subject, source_key=keys, injective=False), part),
            pre=(),
            required=("subject", "journey" if journey else "occurrences"),
            created=("anchor",),
            post=(),
            obligations=(),
            eval_id="anchor.bind@v1",
        )
    if len(inputs) != 2 or inputs[0].domain.kind != "anchor":
        fail("anchor_binding", "Anchor and its original complete population are required")
    domain = require_part(inputs[0], "anchor")
    assert isinstance(domain, AnchorDomainPart)
    template = observation_template(inputs[1], params.observations, params.composition)
    if template != params.template:
        fail("anchor_metric", "Metric template differs from its frozen components")
    observation_part = AnchorObservationPart(
        domain.binding,
        domain,
        params.window,
        tuple(o.contribution for o in params.observations),
        tuple(
            "int64" if isinstance(o, ObserveCount) else o.amount_type for o in params.observations
        ),
        (*domain.components, "deadline", *(f"uses_{i}" for i in range(len(params.observations)))),
    )
    validate(observation_part)
    original = require_part(template, "original_state")
    assert isinstance(original, OriginalStatePart)
    subject = require_part(inputs[0], "subject")
    return _result(
        "anchor@v1",
        inputs,
        inputs[0].domain,
        template.quantity,
        (
            subject,
            observation_part,
            replace(original, temporal_policy="overlapping"),
            require_part(template, "coverage"),
        ),
        pre=tuple(item.fact for item in template.obligations),
        required=("anchor", "subject"),
        created=("original_state", "coverage"),
        post=(),
        obligations=template.obligations,
        eval_id="anchor.observe@v1",
    )
