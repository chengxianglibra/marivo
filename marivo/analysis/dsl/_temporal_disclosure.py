"""Declare captured time operations and their public carriers."""

from marivo._kernel.temporal import InstantAnchor, TimeScope
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.dsl.temporal import CalendarAxis
from marivo.analysis.model.disclosure import (
    Descriptor,
    ExampleInput,
    bind,
    operation,
    value_type,
)
from marivo.analysis.model.disclosure import (
    ParameterInput as P,
)

_TIME_OPERATIONS = (
    (
        TimeScope,
        "time_scope",
        "by",
        (
            P(
                "grain",
                "Pass mv.grain(unit) for a natural calendar.",
                ("grain.define",),
            ),
        ),
        "CalendarAxis",
        "Construct a frozen calendar axis for observe.by, explicit Population coordinates or a shifted endpoint relation.",
        'result = scope.by(mv.grain("day"))',
        ("scope",),
        "inputs.time",
        (
            "Use the scope report timezone and a natural Grain.",
            "Report grain never replaces a Metric's contribution clock. DATE clocks cannot supply subday buckets. Explicitly choose the source reduction's time= field; hour_prefix can supply partition hours. Clock changes can change totals; see the linked observation and clock contracts.",
            "Ordinary by=(scope.by(grain),) returns actual supported keys without filling buckets; use by=mv.product(axis, mv.coordinates(by=(), keys=((),))) for complete time output. Range-only totals use where=scope.",
        ),
    ),
    (
        InstantAnchor,
        "instant_anchor",
        "show",
        (),
        "None",
        "Display the exact frozen UTC instant.",
        "result = anchor.show()",
        ("anchor",),
        None,
        ("Reads captured metadata only.",),
    ),
    (
        CalendarAxis,
        "calendar_axis",
        "shift",
        (P("rule", "Pass mv.offset(years=n) or mv.offset(periods=n).", ("offset",)),),
        "CalendarAxis",
        "Construct an exact baseline domain and freeze its immediate current-to-baseline relation.",
        "result = axis.shift(mv.offset(years=-1))",
        ("axis",),
        "inputs.time",
        (
            "Builtin unit-step week/month/year; complete buckets or prefixes from their bucket starts. ordinal, named calendars, partial day/quarter and middle slices reject. Missing ISO week or civil cutoff has no target; no clamping or current-domain shortening. Selected targets may be sparse or empty; no envelope filling. Only the immediate origin intent and explicit result correspondence are retained; the input stays unchanged. Nonzero DST gap/fold targets reject, while zero offset preserves the exact captured instant. No business data, Metric evaluation or source access.",
        ),
    ),
    (
        CalendarAxis,
        "calendar_axis",
        "show",
        (),
        "None",
        "Display bounded frozen source-calendar metadata.",
        "result = axis.show()",
        ("axis",),
        None,
        (
            "Exact selected buckets may be sparse or empty; show reports captured bounds and direct absence counts without evaluating. Source calendar declarations are not FrameAxis handles.",
        ),
    ),
    (
        AnalysisFrame,
        "analysis_frame",
        "compute",
        (),
        "AnalysisFrame",
        "Return complete independent memory; memory roots return self.",
        "fixed = frame.compute()\nderived = fixed.select({'scaled': fixed['value'] * 2})\nresult = derived.compute()",
        ("frame",),
        "execution",
        (
            "Check stored facts; pending inputs may read sources. Fixed derivations: lazy, no rereads. No result is persisted. Example needs numeric 'value'.",
            "JSON: datasource.json. UInt64/naive: semantic.entity.define.",
        ),
    ),
    (
        AnalysisFrame,
        "analysis_frame",
        "persist",
        (
            P(
                "description",
                "Optional caller-supplied purpose string, saved unchanged; None stores no description on a new commit and retains it on an existing commit.",
            ),
        ),
        "AnalysisFrame",
        "Commit evaluated data and return its stored Frame without recomputing a completed memory input.",
        "result = frame.persist(description='Monthly revenue')\nprint(result.session_id)\nprint(result.artifact_id)",
        ("frame",),
        "execution",
        (
            "Pending inputs evaluate before publication admission; completed memory inputs are saved without reevaluation. A stored root checks commit metadata and returns itself. Only an explicit persist call saves a Frame.",
            "Retain session_id and artifact_id for reopening. Lost IDs can be discovered through session.persisted_frames(). Unknown publication outcomes require Session recovery first. Descriptions help select candidates; they do not certify meaning. Stored descriptions are immutable; a different non-None value rejects.",
        ),
    ),
    (
        AnalysisFrame,
        "analysis_frame",
        "show",
        (),
        "None",
        "Print captured structure and metadata, and the complete fixed data as a pandas table without evaluating pending inputs.",
        "result = frame.show()",
        ("frame",),
        "results.inspect",
        (
            "Pending cards never evaluate. Completed cards summarize inputs/operations and actual source reads, and display all fixed rows using pandas formatting; stored cards add committed identity/time and read the complete saved data. No method payload reads. Pandas formatting can raise OverflowError for composite temporal coordinates outside Python's date range.",
        ),
    ),
    (
        AnalysisFrame,
        "analysis_frame",
        "to_pandas",
        (
            P(
                "axis_names",
                "Name/token string -> alias (nonempty, printable, trimmed); default short names.",
            ),
            P("time_details", "Calendar label; default False."),
        ),
        "pandas.DataFrame",
        "Export complete, isolated data.",
        "result = frame.to_pandas()\nvalues = result['value']",
        ("frame",),
        "results.inspect",
        (
            "Pending inputs evaluate once per call; no mutation, cache or save. Use compute()'s returned Frame to reuse fixed values. Fixed data needs no source reads.",
            "Axes first; composite tuples; TimeField/calendar: one key; interval: .start/.end, zone (mixed/sub-minute: UTC); pairs: .left/.right.",
            "Validate options/names before evaluation or reads; axis_names/select fix collisions. Keep order/types/NA/presence. Example needs frame['value'].",
            "Large int64[pyarrow] / float may raise ArrowInvalid; approx: values.astype('float64')/3600.0 (may round; pd.NA -> NaN).",
            "Empty attrs; interpretation: frame.schema. read_periods(): saved key/label/selection progress.",
        ),
    ),
    (
        AnalysisFrame,
        "analysis_frame",
        "axis",
        (
            P(
                "name",
                "Exact current axis display name; use frame.axes when names are ambiguous.",
                ("analysis_frame.axes",),
            ),
        ),
        "FrameAxis",
        "Select one complete axis from this Frame's current directory.",
        "result = frame.axis(name)",
        ("frame", "name"),
        "inputs.frame",
        (
            "Missing or duplicate names report actual candidates. Axis handles are bound to this input; align/compare may pair unique names by their documented default convention.",
        ),
    ),
    (
        FrameAxis,
        "frame_axis",
        "show",
        (),
        "None",
        "Print captured token, name, role and complete key names without reading rows.",
        "result = axis.show()",
        ("axis",),
        None,
        (
            "The handle retains its exact Frame input; acquire a new handle after a carrier conversion creates a new input binding.",
        ),
    ),
)


def add_time_descriptors(descriptors: list[Descriptor]) -> None:
    for value, target, producer, consumer in (
        (InstantAnchor, "InstantAnchor", "session.instant", "instant_anchor.show"),
        (CalendarAxis, "CalendarAxis", "time_scope.by", "calendar_axis.show"),
    ):
        descriptors.append(
            value_type(
                target,
                value,
                summary=f"Immutable {value.__name__} temporal binding.",
                acquisition=(
                    "Call session.instant(...) or read start/end of an exact Session TimeScope; civil Catalog dates are not InstantAnchor values. Direct construction is unsupported."
                    if value is InstantAnchor
                    else "Call scope.by(grain) or calendar_axis.shift(rule). Direct construction is unsupported; scope/grid are captured metadata."
                ),
                producers=(producer, "calendar_axis.shift")
                if value is CalendarAxis
                else (producer, "time_scope.start", "time_scope.end"),
                consumers=(consumer, "session.observe", "calendar_axis.shift", "product")
                if value is CalendarAxis
                else (consumer,),
                constraints=("Frozen metadata only; no source or schema reads.",),
            )
        )
    for (
        owner,
        target,
        name,
        parameters,
        output,
        summary,
        code,
        requires,
        group,
        constraints,
    ) in _TIME_OPERATIONS:
        value = getattr(owner, name)
        descriptors.append(
            operation(
                f"{target}.{name}",
                f"{target}.{name}",
                value,
                bindings=(bind(value, owner),),
                summary=summary,
                related=("analysis_frame.persist",)
                if owner is AnalysisFrame and name == "compute"
                else (
                    "analysis_frame.session_id",
                    "analysis_frame.artifact_id",
                    "session.persisted_frame",
                    "session.persisted_frames",
                )
                if owner is AnalysisFrame and name == "persist"
                else ("session.observe", "semantic.hour_prefix")
                if owner is TimeScope and name == "by"
                else (),
                parameters=parameters,
                output=output,
                constraints=constraints,
                effects=summary,
                failures=(
                    "ValueError: use a supported exact calendar domain and an offset rule; target civil clocks must be unique."
                    if owner is CalendarAxis and name == "shift"
                    else "ValueError: choose compatible frozen scope, Grain and calendar bounds."
                    if owner is TimeScope and name == "by"
                    else "AnalysisError: follow its repair."
                    if owner is AnalysisFrame and name in ("compute", "to_pandas")
                    else "AnalysisError: follow the concrete expected/received facts and repair.",
                ),
                example=ExampleInput(code, requires, "result", output),
                discovery_group=group,
                telemetry="long"
                if owner is AnalysisFrame and name in ("compute", "persist", "to_pandas")
                else "short"
                if owner is AnalysisFrame and name == "axis"
                else False,
            )
        )
