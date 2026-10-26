"""Native disclosure for immutable data Frames and committed audit views."""

from marivo._temporal import InstantAnchor, TimeScope
from marivo.analysis._capabilities.dataset_model import (
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    bind,
    operation,
    value_type,
)
from marivo.analysis._capabilities.dataset_model import (
    ParameterInput as P,
)
from marivo.analysis.core.frame_method_facts import (
    FrameAdditiveMethod,
    FrameAttributeMetric,
    FrameDivisionMethod,
    FrameLevelAdditiveMethod,
    FrameLevelDivisionMethod,
    FrameLevelLinearMethod,
    FrameLevelLinearTerm,
    FrameLevelRatioMethod,
    FrameLevelSupport,
    FrameLinearMethod,
    FrameLinearTerm,
    FrameMethodCoordinate,
    FrameMethodFacts,
    FrameMethodOutput,
    FrameMethodParent,
    FrameMethodSupport,
    FrameMethodTarget,
    FrameMethodValue,
    FrameRatioMethod,
)
from marivo.analysis.dsl.artifact import Artifact
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.dsl.temporal import CalendarAxis


def provider() -> DisclosureProvider:
    descriptors: list[Descriptor] = [
        value_type(
            "analysis_frame",
            AnalysisFrame,
            summary="One immutable data object with complete coordinates, ordered values and presence; its carrier is expression, memory or stored.",
            acquisition="Initial inputs: session.observe/read/values/frame; retained source targets support Frame.attribute. Derive with Frame operations; compute/persist return fixed carriers. Recover exactly with session.persisted_frame(id). Direct construction is unsupported.",
            producers=(
                "session.observe",
                "analysis_frame.attribute",
                "session.read",
                "session.values",
                "session.frame",
                "session.persisted_frame",
            ),
            consumers=(
                "analysis_frame.attribute",
                "analysis_frame.correlate",
                "analysis_frame.reduce",
                "analysis_frame.fix",
                "analysis_frame.over",
                "analysis_frame.compute",
                "analysis_frame.persist",
                "analysis_frame.show",
                "analysis_frame.to_pandas",
                "analysis_frame.method_facts",
            ),
            constraints=(
                "Ordinary operations consume current data. An instance may retain a source declaration for explicit attribute rereads; independent reopen has no declaration.",
                "Pending expressions capture without business reads. Continue from the Frame returned by compute or persist; to_pandas never executes a pending plan.",
            ),
        ),
        value_type(
            "frame_axis",
            FrameAxis,
            summary="Read-only complete coordinate handle bound to one exact Frame input.",
            acquisition="Read frame.axes or call frame.axis(name); direct construction is not supported.",
            producers=("analysis_frame.axes", "analysis_frame.axis"),
            consumers=(
                "analysis_frame.coord",
                "analysis_frame.correlate",
                "analysis_frame.reduce",
                "analysis_frame.fix",
                "analysis_frame.over",
                "column.share",
                "analysis_frame.align",
                "analysis_frame.compare",
                "frame_axis.show",
            ),
            constraints=(
                "token and name are local metadata, not proof of equality between independent inputs. key_names lists every physical component; complete composite axes remain indivisible in by/on.",
                "Data axes retain key types, roles and time interpretation without original Entity/Field nominal bindings. Explicit on declares caller business correspondence; validation does not prove semantic identity.",
            ),
        ),
        value_type(
            "Artifact",
            Artifact,
            summary="Read-only committed identity, submission metadata and Finding/Evidence audit view.",
            acquisition="Read saved.artifact on the exact persisted Frame, or session.persisted_frame(id).artifact. Direct construction and extension are unsupported.",
            producers=("analysis_frame.artifact",),
            consumers=("Artifact.show", "Artifact.findings", "Artifact.evidence_digest"),
            constraints=(
                "Audit reads consume no result table or coordinate attachments. Preview, export and data operations belong to AnalysisFrame. Old Store generations are rejected unchanged; use a compatible environment or an export made with the original version.",
            ),
        ),
    ]
    fact_types: tuple[tuple[type[object], str, str, tuple[str, ...]], ...] = (
        (
            FrameMethodFacts,
            "frame_method_facts",
            "Read a fixed Frame's method_facts tuple; each item explains its origin_result_id.",
            ("frame_method_parent", "frame_method_coordinate", "frame_method_target"),
        ),
        (
            FrameMethodParent,
            "frame_method_parent",
            "Read facts.parents in complete original parent-key order, including parents with no fine rows.",
            ("frame_method_value", "frame_attribute_metric"),
        ),
        (
            FrameAttributeMetric,
            "frame_attribute_metric",
            "Read parent.metrics in original target declaration order; target retains its complete output path and kind distinguishes level, absolute and relative.",
            (
                "frame_additive_method",
                "frame_ratio_method",
                "frame_linear_method",
                "frame_division_method",
                "frame_level_additive_method",
                "frame_level_ratio_method",
                "frame_level_linear_method",
                "frame_level_division_method",
                "frame_method_output",
            ),
        ),
        (
            FrameMethodValue,
            "frame_method_value",
            "Read a parent key or method total/component value; column owns its exact physical type and unit.",
            (),
        ),
        (
            FrameMethodCoordinate,
            "frame_method_coordinate",
            "Read facts.coordinates for complete independent original frozen interval/calendar/paired snapshots.",
            (),
        ),
        (
            FrameMethodSupport,
            "frame_method_support",
            "Read a two-endpoint method.support for local root/sample policy and exact current/baseline input/effective counts.",
            (),
        ),
        (
            FrameAdditiveMethod,
            "frame_additive_method",
            "Read metric.method or a linear term's method when kind is additive.",
            ("frame_method_value", "frame_method_support"),
        ),
        (
            FrameRatioMethod,
            "frame_ratio_method",
            "Read metric.method or a linear term's method when kind is ratio_component; components are parent totals.",
            ("frame_method_value", "frame_method_support"),
        ),
        (
            FrameLinearTerm,
            "frame_linear_term",
            "Read linear_method.terms in captured subexpression order; coefficient preserves its finite numeric type.",
            (
                "frame_additive_method",
                "frame_ratio_method",
                "frame_linear_method",
                "frame_division_method",
            ),
        ),
        (
            FrameLinearMethod,
            "frame_linear_method",
            "Read metric.method or a linear term's method when kind is linear.",
            ("frame_method_value", "frame_linear_term"),
        ),
    )
    fact_types += (
        (
            FrameMethodTarget,
            "frame_method_target",
            "Read facts.targets for the complete target directory, including empty parent domains.",
            ("frame_method_output",),
        ),
        (
            FrameMethodOutput,
            "frame_method_output",
            "Read target.outputs for exact role-to-output paths.",
            (),
        ),
        (
            FrameLevelSupport,
            "frame_level_support",
            "Read one-endpoint method support counts and sample policy.",
            (),
        ),
        (
            FrameLevelAdditiveMethod,
            "frame_level_additive_method",
            "Read level target.method when kind is level_additive.",
            ("frame_method_value", "frame_level_support"),
        ),
        (
            FrameLevelRatioMethod,
            "frame_level_ratio_method",
            "Read level target.method when kind is level_ratio_component.",
            ("frame_method_value", "frame_level_support"),
        ),
        (
            FrameLevelLinearTerm,
            "frame_level_linear_term",
            "Read level linear method terms in original order.",
            (
                "frame_level_additive_method",
                "frame_level_ratio_method",
                "frame_level_linear_method",
                "frame_level_division_method",
            ),
        ),
        (
            FrameLevelLinearMethod,
            "frame_level_linear_method",
            "Read level target.method when kind is level_linear.",
            ("frame_method_value", "frame_level_linear_term"),
        ),
    )
    fact_types += (
        (
            FrameDivisionMethod,
            "frame_division_method",
            "Read a paired division method's original typed divisor, operation and child method; result columns retain the declared units.",
            (
                "frame_method_value",
                "frame_additive_method",
                "frame_ratio_method",
                "frame_linear_method",
                "frame_division_method",
            ),
        ),
        (
            FrameLevelDivisionMethod,
            "frame_level_division_method",
            "Read a level division method's original typed divisor, operation and child method; result columns retain the declared units.",
            (
                "frame_method_value",
                "frame_level_additive_method",
                "frame_level_ratio_method",
                "frame_level_linear_method",
                "frame_level_division_method",
            ),
        ),
    )
    for fact_owner, target, acquisition, nested in fact_types:
        descriptors.append(
            value_type(
                target,
                fact_owner,
                summary=f"Immutable {fact_owner.__name__} original explanation facts.",
                acquisition=acquisition + " Direct construction and extension are unsupported.",
                producers=("analysis_frame.method_facts",)
                if fact_owner is FrameMethodFacts
                else {
                    FrameDivisionMethod: (
                        "frame_attribute_metric.method",
                        "frame_linear_term.method",
                        "frame_division_method.method",
                    ),
                    FrameLevelDivisionMethod: (
                        "frame_attribute_metric.method",
                        "frame_level_linear_term.method",
                        "frame_level_division_method.method",
                    ),
                    FrameMethodTarget: ("frame_method_facts.targets",),
                    FrameMethodOutput: (
                        "frame_method_target.outputs",
                        "frame_attribute_metric.outputs",
                    ),
                    FrameLevelSupport: (
                        "frame_level_additive_method.support",
                        "frame_level_ratio_method.support",
                    ),
                    FrameLevelAdditiveMethod: (
                        "frame_level_division_method.method",
                        "frame_attribute_metric.method",
                        "frame_level_linear_term.method",
                    ),
                    FrameLevelRatioMethod: (
                        "frame_level_division_method.method",
                        "frame_attribute_metric.method",
                        "frame_level_linear_term.method",
                    ),
                    FrameLevelLinearTerm: ("frame_level_linear_method.terms",),
                    FrameLevelLinearMethod: (
                        "frame_level_division_method.method",
                        "frame_attribute_metric.method",
                        "frame_level_linear_term.method",
                    ),
                    FrameMethodParent: ("frame_method_facts.parents",),
                    FrameAttributeMetric: ("frame_method_parent.metrics",),
                    FrameMethodValue: (
                        "frame_method_parent.key",
                        "frame_additive_method.left",
                        "frame_ratio_method.numerator_left",
                        "frame_ratio_method.denominator_left",
                        "frame_linear_method.left",
                    ),
                    FrameMethodCoordinate: ("frame_method_facts.coordinates",),
                    FrameMethodSupport: (
                        "frame_additive_method.support",
                        "frame_ratio_method.support",
                    ),
                    FrameAdditiveMethod: (
                        "frame_division_method.method",
                        "frame_attribute_metric.method",
                        "frame_linear_term.method",
                    ),
                    FrameRatioMethod: (
                        "frame_attribute_metric.method",
                        "frame_linear_term.method",
                        "frame_division_method.method",
                    ),
                    FrameLinearMethod: (
                        "frame_division_method.method",
                        "frame_attribute_metric.method",
                        "frame_linear_term.method",
                    ),
                    FrameLinearTerm: ("frame_linear_method.terms",),
                }[fact_owner],
                consumers=("frame_method_facts.show", *nested),
                constraints=(
                    "Read-only typed data; no Metric, source address, executable recipe or new computation authority. Inherited facts explain their origin, not derived values. facts.targets retains explicit role-to-output paths even when facts.parents is empty.",
                    "Complete fields remain available independently of bounded show output. observed_total retains the receiver value and total the fresh parent result; share_state is defined or zero_total. Level methods contain one endpoint. Numeric values retain actual physical types, Decimal precision and integer time ticks; counts do not prove source qualification.",
                ),
            )
        )
    descriptors.append(
        operation(
            "frame_method_facts.show",
            "facts.show",
            FrameMethodFacts.show,
            bindings=tuple(bind(FrameMethodFacts.show, owner) for owner, _, _, _ in fact_types),
            summary="Print a bounded view of any concrete Frame method-fact record already in memory.",
            parameters=(
                P(
                    "n",
                    "Exact nonnegative int excluding bool; default 20; zero prints fields and counts only.",
                ),
            ),
            output="None",
            constraints=(
                "Shared by FrameMethodFacts and its concrete nested record types. At most n detail lines; no field or record truncation in the immutable carrier.",
                "No file/source/model reads. Obtaining stored_frame.method_facts beforehand is a separate full method-payload read, even when this show uses n=0.",
            ),
            effects="Print already-owned immutable facts only.",
            failures=("AnalysisError: provide a nonnegative exact integer n, excluding bool.",),
            example=ExampleInput("result = facts.show(n=0)", ("facts",), "result", "None"),
            related=("frame_method_facts",),
        )
    )
    operations = (
        (
            TimeScope,
            "time_scope",
            "by",
            (P("grain", "Pass mv.grain(unit) or a certified calendar.grain(level)."),),
            "CalendarAxis",
            "Construct nominal and actual buckets from the frozen scope.",
            "result = scope.by(grain)",
            ("scope", "grain"),
            "inputs.time",
            (
                "Frozen scope and compatible Grain are required; semantic Grain must already capture a certified snapshot through calendar.grain(level).",
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
                "Builtin unit-step week/month/year; complete buckets or prefixes from their bucket starts. ordinal, named calendars, partial day/quarter and middle slices reject. Missing ISO week or civil cutoff has no target; no clamping or current-domain shortening. Selected targets may be sparse or empty; no envelope filling. Only the immediate origin is retained, with frozen UTC/civil bounds; the input stays unchanged. Nonzero DST gap/fold targets reject, while zero offset preserves the exact captured instant. No business data, Metric evaluation or source access.",
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
            "Return complete independent memory; memory roots return themselves.",
            "fixed = frame.compute()\n"
            "derived = fixed.select({'scaled': fixed['value'] * 2})\n"
            "result = derived.compute()",
            ("frame",),
            "execution",
            (
                "Example needs numeric frame['value']. No Artifact is created. Load/validate all stored data/method facts; use returned handles. Fixed derivations: lazy, no source reread. Explicit sources may change.",
            ),
        ),
        (
            AnalysisFrame,
            "analysis_frame",
            "persist",
            (),
            "AnalysisFrame",
            "Commit evaluated data and return its stored Frame without recomputing a completed memory input.",
            "result = frame.persist()",
            ("frame",),
            "execution",
            (
                "A stored root checks commit metadata and returns itself. Evaluation finishes before publication admission; failures and unknown publication outcomes require their structured repair, never an automatic recomputation. Retain Session and Artifact IDs; lost return references require explicit resubmission, which may duplicate saved outputs or read changed sources.",
            ),
        ),
        (
            AnalysisFrame,
            "analysis_frame",
            "show",
            (P("n", "Nonnegative integer, excluding bool; default 20, or 0 for metadata only."),),
            "None",
            "Print bounded captured structure and an existing result prefix without evaluating a pending expression.",
            "result = frame.show(n=0)",
            ("frame",),
            "results.inspect",
            (
                "Validate n before reads. Pending cards show captured scopes and operations, with unresolved facts marked. n=0 reads no rows or attachments. Fixed cards show only method-fact counts, bounded origins and a .method_facts pointer; previews never read method payloads. Positive previews resolve displayed frozen coordinates; receipt hashing may read a whole consumed file. Success does not certify unread data.",
            ),
        ),
        (
            AnalysisFrame,
            "analysis_frame",
            "to_pandas",
            (
                P(
                    "axis_names",
                    "Optional {current FrameAxis: name}; None keeps full names. Nonempty printable names; no surrounding whitespace.",
                ),
            ),
            "pandas.DataFrame",
            "Export complete fixed data as an isolated readable flat table.",
            "result = frame.compute().to_pandas()\nvalues = result['value']",
            ("frame",),
            "results.inspect",
            (
                "Example needs frame['value']. compute/persist first. No Catalog/source/method reads.",
                "Flat axis/value/presence names; composite tuples. Calendar: label, .actual_start/.actual_end/.timezone; interval: .start/.end/.timezone; paired: .left/.right. UTC bounds; typed named labels.",
                "Resolve collisions with axis_names={axis: 'name'} or select(). Preserve order, physical types and NA; boolean presence differs from NA/zero.",
                "Isolated attrs: axes, units, comparisons, frozen_coordinates; no stable import or typed authority.",
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
                ),
            ),
            "FrameAxis",
            "Select one complete axis from this Frame's current directory.",
            "result = frame.axis(name)",
            ("frame", "name"),
            "frame.expressions",
            (
                "Missing or duplicate names report actual candidates; names do not match axes between inputs.",
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
        (
            Artifact,
            "artifact",
            "show",
            (),
            "None",
            "Print bounded submission identity, time and saved audit summaries.",
            "result = artifact.show()",
            ("artifact",),
            "results.evidence",
            (
                "Metadata only: no preview count or result-row read. For data use session.persisted_frame(artifact.id).show(n=20) or .to_pandas().",
            ),
        ),
        (
            Artifact,
            "artifact",
            "findings",
            (
                P("limit", "Page size from 1 through 100."),
                P("cursor", "Previous page cursor or None for this exact Artifact and selection."),
            ),
            "FindingPage",
            "Read a bounded page of self-contained committed Findings.",
            "result = artifact.findings(limit=20)",
            ("artifact",),
            "results.evidence",
            (
                "Reads audit records without result tables, source access or producer-graph restoration.",
            ),
        ),
        (
            Artifact,
            "artifact",
            "evidence_digest",
            (),
            "ArtifactDigest",
            "Read all saved Evidence facts and collection digests.",
            "result = artifact.evidence_digest()",
            ("artifact",),
            "results.evidence",
            (
                "No result-table reads or current-source freshness claim; .items contains all saved facts.",
            ),
        ),
    )
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
                consumers=(consumer, "calendar_axis.shift", "session.observe", "product")
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
    ) in operations:
        value = getattr(owner, name)
        descriptors.append(
            operation(
                f"{target}.{name}",
                f"{target}.{name}",
                value,
                bindings=(bind(value, owner),),
                summary=summary,
                parameters=parameters,
                output=output,
                constraints=constraints,
                effects=summary,
                failures=(
                    "ValueError: use a supported exact calendar domain and an offset rule; target civil clocks must be unique."
                    if owner is CalendarAxis and name == "shift"
                    else "AnalysisError: follow its repair."
                    if owner is AnalysisFrame and name in ("compute", "to_pandas")
                    else "AnalysisError: follow the concrete expected/received facts and repair.",
                ),
                example=ExampleInput(code, requires, "result", output),
                discovery_group=group,
            )
        )
    from marivo.analysis.dsl._population_disclosure import provider as population_provider
    from marivo.analysis.dsl._result_disclosure import provider as result_provider

    populations = population_provider()
    results = result_provider()
    return DisclosureProvider(
        "analysis.dsl",
        (*descriptors, *populations.descriptors, *results.descriptors),
        (
            ExportInput("AnalysisFrame", AnalysisFrame, "analysis_frame"),
            ExportInput("FrameAxis", FrameAxis, "frame_axis"),
            ExportInput("Artifact", Artifact, "Artifact"),
            ExportInput("InstantAnchor", InstantAnchor, "InstantAnchor"),
            ExportInput("CalendarAxis", CalendarAxis, "CalendarAxis"),
            ExportInput("FrameMethodFacts", FrameMethodFacts, "frame_method_facts"),
            *populations.exports,
            *results.exports,
        ),
    )
