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
    FrameLinearMethod,
    FrameLinearTerm,
    FrameMethodCoordinate,
    FrameMethodFacts,
    FrameMethodMetric,
    FrameMethodParent,
    FrameMethodSupport,
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
            acquisition="Call session.observe, session.attribute, session.read, session.values or session.frame; reopen an exact stored root with session.persisted_frame(id).",
            producers=(
                "session.observe",
                "session.attribute",
                "session.read",
                "session.values",
                "session.frame",
                "session.persisted_frame",
            ),
            consumers=(
                "analysis_frame.correlate",
                "analysis_frame.reduce",
                "analysis_frame.compute",
                "analysis_frame.persist",
                "analysis_frame.show",
                "analysis_frame.to_pandas",
                "analysis_frame.method_facts",
            ),
            constraints=(
                "Operations consume current data only; a completed result has no original Metric, new-attribute or production-history authority.",
                "Pending expressions capture without business reads. Continue from the Frame returned by compute or persist; to_pandas never executes a pending plan.",
            ),
        ),
        value_type(
            "frame_axis",
            FrameAxis,
            summary="Read-only complete coordinate handle bound to one exact Frame input.",
            acquisition="Read frame.axes or call frame.axis(name); direct construction is not supported.",
            consumers=(
                "analysis_frame.coord",
                "analysis_frame.correlate",
                "analysis_frame.reduce",
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
            acquisition="Read saved.artifact on the exact persisted Frame, or session.persisted_frame(id).artifact.",
            consumers=("Artifact.show", "Artifact.findings", "Artifact.evidence_digest"),
            constraints=(
                "Audit reads consume no result table or coordinate attachments. Preview, export and data operations belong to AnalysisFrame. Old Store generations are rejected unchanged; use a compatible environment or an export made with the original version.",
            ),
        ),
    ]
    fact_types = (
        (
            FrameMethodFacts,
            "frame_method_facts",
            "Read a fixed Frame's method_facts tuple; each item explains its origin_result_id.",
            ("frame_method_parent", "frame_method_coordinate"),
        ),
        (
            FrameMethodParent,
            "frame_method_parent",
            "Read facts.parents in complete original parent-key order, including parents with no fine rows.",
            ("frame_method_value", "frame_method_metric"),
        ),
        (
            FrameMethodMetric,
            "frame_method_metric",
            "Read parent.metrics in original metric declaration order.",
            ("frame_additive_method", "frame_ratio_method", "frame_linear_method"),
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
            "Read method.support for local root/sample policy and exact input/effective counts.",
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
            ("frame_additive_method", "frame_ratio_method", "frame_linear_method"),
        ),
        (
            FrameLinearMethod,
            "frame_linear_method",
            "Read metric.method or a linear term's method when kind is linear.",
            ("frame_method_value", "frame_linear_term"),
        ),
    )
    for fact_owner, target, acquisition, nested in fact_types:
        descriptors.append(
            value_type(
                target,
                fact_owner,
                summary=f"Immutable {fact_owner.__name__} original explanation facts.",
                acquisition=acquisition,
                producers=("analysis_frame.method_facts",)
                if fact_owner is FrameMethodFacts
                else (),
                consumers=("frame_method_facts.show", *nested),
                constraints=(
                    "Read-only typed data; no Metric, source address, executable recipe or new computation authority. Inherited facts explain their origin, not derived values.",
                    "Complete fields remain available independently of bounded show output. Numeric values retain actual physical types, Decimal precision and integer time ticks; counts do not prove source qualification.",
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
                "Shared by FrameMethodFacts and its nine concrete nested record types. At most n detail lines; no field or record truncation in the immutable carrier.",
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
            ("Frozen scope and compatible Grain are required.",),
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
            "inputs.time",
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
            "inputs.time",
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
            "Evaluate an expression or fully load a stored root into an independent memory Frame; a memory root returns itself.",
            "result = frame.compute()",
            ("frame",),
            "entry",
            (
                "No Run or Artifact is created. Stored loading validates current data and all owned method facts; use handles from the returned Frame. Explicit source leaves may read changing sources.",
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
            "entry",
            (
                "A stored root checks commit metadata and returns itself. Evaluation finishes before publication admission; failures and unknown publication outcomes require their structured repair, never an automatic recomputation.",
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
            "methods.rows",
            (
                "Validate n before reads. Pending cards show captured scopes and operations, with unresolved facts marked. n=0 reads no rows or attachments. Fixed cards show only method-fact counts, bounded origins and a .method_facts pointer; previews never read method payloads. Positive previews resolve displayed frozen coordinates; receipt hashing may read a whole consumed file. Success does not certify unread data.",
            ),
        ),
        (
            AnalysisFrame,
            "analysis_frame",
            "to_pandas",
            (),
            "pandas.DataFrame",
            "Read the complete fixed result as isolated values and frozen coordinate metadata.",
            "result = frame.compute().to_pandas()",
            ("frame",),
            "artifacts.reads",
            (
                "Pending expressions must first compute or persist; this method never executes them. Stored export reads current rows/coordinates, not method payloads, and needs no model, Catalog or source. Exported values and attrs are isolated; pandas attrs cannot be imported as computation authority.",
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
            "methods.rows",
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
            "methods.rows",
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
            "artifacts.reads",
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
            "artifacts.reads",
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
            "artifacts.reads",
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
                acquisition=f"Call {producer}(...).",
                producers=(producer, "calendar_axis.shift")
                if value is CalendarAxis
                else (producer,),
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
