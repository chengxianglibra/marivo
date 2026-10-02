"""History projections require canonical trace and exact checkpoint authority."""

from datetime import datetime
from hashlib import sha256

from marivo.analysis.core.domain_captures import fail
from marivo.analysis.core.history_types import (
    Distribution,
    Intervals,
    StateAt,
    Violations,
)
from marivo.analysis.core.model import (
    DerivedQuantity,
    HistoryPart,
    HistoryViewPart,
    Part,
    Signature,
    SubjectPart,
    require_part,
    validate_part,
)
from marivo.analysis.core.rules import (
    HistoryAxesPrepare,
    HistoryRead,
    HistoryView,
    RuleDerivation,
    _binding,
    _output_domain,
    _result,
)
from marivo.refs import ref

FIELDS = {
    "distribution": (
        "known_state_count",
        "seeded_subject_count",
        "coverage_censored_count",
        "share_among_seeded",
    ),
    "transitions": ("count", "share_of_modeled_transitions"),
    "dwell": (
        "interval_count",
        "completed_count",
        "right_censored_count",
        "coverage_censored_count",
        "left_clipped_completed_count",
        "mean_duration",
        "median_duration",
        "p90_duration",
    ),
    "violations": ("trigger", "occurred_at", "state_at_event", "kind"),
    "intervals": ("state", "start", "end", "observed_duration", "left_clipped", "status"),
}


def validate(part: HistoryViewPart) -> None:
    validate_part(part.history)
    request = part.request
    model = part.history.preparation.model
    assert model is not None
    if part.version != "v1" or part.binding != part.history.binding:
        fail("history_binding", "view differs from canonical History binding/version")
    points = (
        (request.at,)
        if isinstance(request, StateAt)
        else request.at
        if isinstance(request, Distribution)
        else ()
    )
    start, end = (
        datetime.fromisoformat(part.history.window_start),
        datetime.fromisoformat(part.history.window_end),
    )
    if isinstance(request, (StateAt, Distribution)) and (
        not points
        or len(set(points)) != len(points)
        or any(
            datetime.fromisoformat(point).utcoffset() is None
            or not start <= datetime.fromisoformat(point) <= end
            for point in points
        )
    ):
        fail("history_checkpoint", "use unique aware checkpoints inside [window.start, window.end]")
    if isinstance(request, StateAt) and request.state not in {
        state.name for state in model.definition.states
    }:
        fail("history_state", "state must belong to the exact retained model")
    if isinstance(request, Distribution) and (
        len({axis.dimension.ref for axis in request.axes}) != len(request.axes)
        or any(axis.subject != model.triggers[0].subject for axis in request.axes)
    ):
        fail("history_axes", "axes require the model Subject and unique governed Dimensions")


def derive(
    inputs: tuple[Signature, ...], params: HistoryView | HistoryRead | HistoryAxesPrepare
) -> RuleDerivation:
    binding = _binding(inputs, "r7.history_binding")
    identity = sha256(repr((inputs, params)).encode()).hexdigest()
    quantity = None
    domain = inputs[0].domain
    parts: tuple[Part, ...]
    if isinstance(params, HistoryAxesPrepare):
        original = HistoryViewPart(binding, params.history, params.request)
        parts = (original,)
    elif isinstance(params, HistoryView):
        history = require_part(inputs[0], "history")
        assert isinstance(history, HistoryPart)
        original = HistoryViewPart(binding, history, params.request)
        _output_domain(binding, params.output, "r7.history_binding")
        domain = params.output
        if isinstance(params.request, Distribution) and params.request.axes:
            axes = require_part(inputs[1], "history_view")
            if not isinstance(axes, HistoryViewPart) or axes != original:
                fail("history_axes", "checkpoint axes must bind this exact History request")
        elif len(inputs) != 1:
            fail("history_binding", "this History view consumes one canonical History")
        parts = (original,)
        if isinstance(params.request, (StateAt, Intervals, Violations)):
            subject = history.preparation.model
            assert subject is not None
            parts += (
                SubjectPart(
                    binding,
                    ref.entity(subject.triggers[0].subject.ref.path),
                    domain.instance_key,
                    inputs[0].domain.instance_key,
                    isinstance(params.request, StateAt),
                    True,
                    "v1",
                ),
            )
        if isinstance(params.request, StateAt):
            quantity = DerivedQuantity(
                identity,
                "history.in_state@v1",
                (inputs[0].domain.definition_id, params.request.state, params.request.at),
                None,
                binding.scope_id,
                "coverage_truth",
            )
    else:
        retained = require_part(inputs[0], "history_view")
        assert isinstance(retained, HistoryViewPart)
        original = retained
        assert isinstance(original, HistoryViewPart)
        if params.field not in FIELDS.get(original.request.kind, ()):
            fail("history_field", "read a concrete field owned by this History view")
        parts = inputs[0].parts
        quantity = DerivedQuantity(
            identity,
            "history.read@v1",
            (domain.definition_id, params.field),
            "duration"
            if params.field.endswith("duration")
            else "count"
            if params.field == "count" or params.field.endswith("count")
            else None,
            binding.scope_id,
            "retained_history",
        )
    for part in parts:
        validate_part(part)
    return _result(
        "history_view@v1",
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
