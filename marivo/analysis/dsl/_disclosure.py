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
            acquisition="Call session.observe, session.read, session.values or session.frame; reopen an exact stored root with session.persisted_frame(id).",
            producers=(
                "session.observe",
                "session.read",
                "session.values",
                "session.frame",
                "session.persisted_frame",
            ),
            consumers=(
                "analysis_frame.compute",
                "analysis_frame.persist",
                "analysis_frame.show",
                "analysis_frame.to_pandas",
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
                "analysis_frame.reduce",
                "analysis_frame.align",
                "analysis_frame.compare",
                "frame_axis.show",
            ),
            constraints=(
                "token and name are local metadata, not proof of equality between independent inputs. key_names lists every physical component; complete composite axes remain indivisible in by/on.",
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
            "show",
            (),
            "None",
            "Display bounded frozen source-calendar metadata.",
            "result = axis.show()",
            ("axis",),
            "inputs.time",
            ("Source calendar declarations are not FrameAxis handles.",),
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
                "No Run or Artifact is created. Stored loading validates all consumed data; use handles from the returned Frame. Explicit source leaves may read changing sources.",
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
                "Validate n before reads. Pending cards show captured scopes and operations, with unresolved facts marked. n=0 reads no rows or attachments. Positive previews preserve result order and resolve only displayed frozen coordinates; receipt hashing may read a whole consumed file. Success does not certify unread data.",
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
                "Pending expressions must first compute or persist; this method never executes them. Stored reads need no model, Catalog or source. Exported values and attrs are isolated; pandas attrs cannot be imported as computation authority.",
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
                producers=(producer,),
                consumers=(consumer,),
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
                    "AnalysisError: follow the concrete expected/received facts and repair.",
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
            *populations.exports,
            *results.exports,
        ),
    )
