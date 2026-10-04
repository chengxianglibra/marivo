"""Closed funnel derivations over canonical Journey and retained component scopes."""

from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from typing import get_args

from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.model import (
    DerivedQuantity,
    EntryAxesPart,
    FindingPolicyPart,
    FunnelAllocationPart,
    FunnelComparisonPart,
    FunnelPart,
    JourneyPart,
    OccurrencePart,
    Part,
    Signature,
    require_part,
    validate_part,
)
from marivo.analysis.core.rules import (
    FunnelAttribute,
    FunnelAxesPrepare,
    FunnelCompare,
    FunnelField,
    FunnelRead,
    FunnelReduce,
    RuleDerivation,
    _binding,
    _output_domain,
    _result,
)


def compatible(current: FunnelPart, baseline: FunnelPart) -> None:
    """Validate the definition and period contract independently of output rows."""
    left, right = current.journey, baseline.journey
    if (
        not current.complete
        or not baseline.complete
        or current.population_id != baseline.population_id
        or left.steps != right.steps
        or left.events != right.events
        or left.policy != right.policy
        or left.policy != "first_per_subject"
        or tuple(replace(e, source_id="definition") for e in left.preparation.events)
        != tuple(replace(e, source_id="definition") for e in right.preparation.events)
        or left.preparation.order != right.preparation.order
        or tuple(
            replace(a, source_ids=tuple("definition" for _ in a.source_ids)) for a in current.axes
        )
        != tuple(
            replace(a, source_ids=tuple("definition" for _ in a.source_ids)) for a in baseline.axes
        )
        or datetime.fromisoformat(left.cohort_end) - datetime.fromisoformat(left.cohort_start)
        != datetime.fromisoformat(right.cohort_end) - datetime.fromisoformat(right.cohort_start)
        or datetime.fromisoformat(left.completion_through) - datetime.fromisoformat(left.cohort_end)
        != datetime.fromisoformat(right.completion_through)
        - datetime.fromisoformat(right.cohort_end)
    ):
        fail(
            "funnel_period",
            "funnel endpoints need the same population, definitions, axes and equal start/follow-up lengths",
        )


def validate(part: Part) -> None:
    if isinstance(part, EntryAxesPart):
        validate_part(part.occurrence)
        if not part.axes or part.first_event not in {e.ref.path for e in part.occurrence.events}:
            fail("funnel_axes", "entry axes need the exact captured first Event")
    elif isinstance(part, FunnelPart):
        validate_part(part.journey)
        if (
            part.journey.policy != "first_per_subject"
            or not part.population_id
            or not part.capture_scope
        ):
            fail(
                "funnel_binding",
                "funnel requires first-per-subject assignments and an explicit population",
            )
        if len({a.dimension.ref for a in part.axes}) != len(part.axes):
            fail("funnel_axes", "duplicate entry axes")
    elif isinstance(part, FunnelComparisonPart):
        validate_part(part.current)
        validate_part(part.baseline)
        compatible(part.current, part.baseline)
    elif isinstance(part, FunnelAllocationPart):
        validate_part(part.comparison)
        validate_part(part.original)
        if (
            not 0 < part.target_step < len(part.comparison.current.journey.steps)
            or not part.axes
            or len(set(part.axes)) != len(part.axes)
            or part.mode not in ("joint", "hierarchy")
            or (part.mode == "hierarchy" and len(part.axes) < 2)
            or (
                part.top_k is not None
                and (type(part.top_k) is not int or not 1 <= part.top_k <= 1000)
            )
            or part.view not in ("contribution", "current", "baseline")
        ):
            fail(
                "funnel_allocation",
                "exact noninitial step, unique axes, closed mode and top_k 1..1000 required",
            )
    elif isinstance(part, FindingPolicyPart):
        if part.producer in ("deviation.zscore", "deviation.mad"):
            if (
                part.extractor != "graph.no_findings@v1"
                or part.policy != "zero_findings@v1"
                or part.version != "v1"
            ):
                fail("finding_policy", "deviation requires its exact empty Finding policy")
            return
        expected = (
            "graph.funnel_delta_findings@v1"
            if part.producer == "funnel.compare"
            else "graph.funnel_contribution_findings@v1"
        )
        if part.extractor != expected or part.policy != "bounded_algebraic_findings@v1":
            fail("finding_policy", "producer and extractor policy differ")
    if getattr(part, "version", None) != "v1":
        fail("funnel_binding", "unsupported funnel state version")


def derive(
    inputs: tuple[Signature, ...],
    params: FunnelAxesPrepare | FunnelReduce | FunnelCompare | FunnelRead | FunnelAttribute,
) -> RuleDerivation:
    binding = _binding(inputs, "analysis.funnel")
    identity = sha256(
        repr((tuple(s.domain.definition_id for s in inputs), params)).encode()
    ).hexdigest()
    parts: tuple[Part, ...]
    quantity = None
    domain = inputs[0].domain
    if isinstance(params, FunnelAxesPrepare):
        occurrence = require_part(inputs[0], "occurrences")
        assert isinstance(occurrence, OccurrencePart)
        parts = (
            EntryAxesPart(
                binding,
                params.cohort_start,
                params.cohort_end,
                occurrence,
                params.axes,
                params.first_event,
            ),
        )
    elif isinstance(params, FunnelReduce):
        _output_domain(binding, params.output, "analysis.funnel")
        journey = require_part(inputs[0], "journey")
        assert isinstance(journey, JourneyPart)
        if not journey.complete or len(inputs) != (2 if params.axes else 1):
            fail("funnel_binding", "funnel requires the full assignment and exact entry-axis input")
        if params.axes:
            axes = require_part(inputs[1], "entry_axes")
            if (
                not isinstance(axes, EntryAxesPart)
                or axes.occurrence != journey.preparation
                or axes.axes != params.axes
                or axes.cohort_start != journey.cohort_start
                or axes.cohort_end != journey.cohort_end
                or axes.first_event != journey.events[0]
            ):
                fail("funnel_axes", "axis preparation differs from the consumed assignment")
        domain = params.output
        parts = (
            FunnelPart(binding, params.capture_scope, journey, params.axes, params.population_id),
        )
    elif isinstance(params, FunnelCompare):
        if len(inputs) != 2:
            fail("funnel_period", "two ordered funnel endpoints required")
        left, right = (require_part(s, "funnel_state") for s in inputs)
        if not isinstance(left, FunnelPart) or not isinstance(right, FunnelPart):
            fail("funnel_period", "comparison needs two complete FunnelResult endpoints")
        compatible(left, right)
        domain = params.output
        parts = (
            FunnelComparisonPart(binding, left, right),
            FindingPolicyPart(binding, "funnel.compare", "graph.funnel_delta_findings@v1"),
        )
    elif isinstance(params, FunnelRead):
        original = require_part(inputs[0], "funnel_state")
        if not isinstance(
            original, (FunnelPart, FunnelComparisonPart)
        ) or params.field not in get_args(FunnelField):
            fail("funnel_read", "read needs an owned exact funnel field")
        compared = isinstance(original, FunnelComparisonPart)
        if compared != (
            params.field.startswith(("current_", "baseline_")) or params.field == "loss_rate_delta"
        ):
            fail("funnel_read", "field belongs to a different funnel result kind")
        journey = (
            original.current.journey
            if isinstance(original, FunnelComparisonPart)
            else original.journey
        )
        if params.step is not None and not 0 < params.step < len(journey.steps):
            fail("funnel_read", "loss target requires a noninitial exact retained step")
        parts = (original,)
        quantity = DerivedQuantity(
            identity,
            "funnel.read@v1",
            (
                domain.definition_id,
                params.field,
                "all" if params.step is None else str(params.step),
            ),
            "count" if params.field.endswith("_count") else None,
            binding.scope_id,
            "exact_components",
        )
    else:
        original = require_part(inputs[0], "funnel_state")
        expanded = require_part(inputs[1], "funnel_state")
        if (
            not isinstance(original, FunnelComparisonPart)
            or not isinstance(expanded, FunnelComparisonPart)
            or not original.complete
            or not expanded.complete
        ):
            fail("funnel_allocation", "allocation needs complete original and expanded comparisons")
        if tuple(a.dimension.ref.path for a in expanded.current.axes) != tuple(
            a.path for a in params.axes
        ):
            fail("funnel_axes", "expanded comparison lacks the exact ordered axes")
        if (
            original.current.journey != expanded.current.journey
            or original.baseline.journey != expanded.baseline.journey
        ):
            fail("funnel_allocation", "expanded basis must consume the same assignments")
        domain = params.output
        parts = (
            FunnelAllocationPart(
                binding,
                expanded,
                original,
                params.axes,
                params.target_step,
                params.mode,
                params.top_k,
            ),
            FindingPolicyPart(binding, "funnel_ratio_mix", "graph.funnel_contribution_findings@v1"),
        )
        quantity = DerivedQuantity(
            identity,
            "funnel_ratio_mix@v1",
            (inputs[0].domain.definition_id,),
            None,
            binding.scope_id,
            "exact_fraction",
        )
    for part in parts:
        validate_part(part)
    return _result(
        "funnel@v1",
        inputs,
        domain,
        quantity,
        parts,
        pre=(),
        required=(),
        created=(),
        post=(),
        obligations=(),
        eval_id=identity,
    )
