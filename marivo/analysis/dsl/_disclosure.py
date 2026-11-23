"""Native disclosure for immutable data Frames and captured metadata."""

from marivo._kernel.temporal import InstantAnchor
from marivo.analysis.core.frame_method_facts import (
    FrameMethodFacts,
)
from marivo.analysis.dsl._method_fact_disclosure import add_method_fact_descriptors
from marivo.analysis.dsl._population_disclosure import provider as population_provider
from marivo.analysis.dsl._read_disclosure import provider as read_provider
from marivo.analysis.dsl._result_disclosure import provider as result_provider
from marivo.analysis.dsl._temporal_disclosure import add_time_descriptors
from marivo.analysis.dsl.frame import AnalysisFrame
from marivo.analysis.dsl.frame_axis import FrameAxis
from marivo.analysis.dsl.temporal import CalendarAxis
from marivo.analysis.model.disclosure import (
    Descriptor,
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    bind,
    operation,
    value_type,
)
from marivo.analysis.model.lineage import ColumnLineage, FrameLineage, SemanticObjectIdentity


def provider() -> DisclosureProvider:
    descriptors: list[Descriptor] = [
        value_type(
            "analysis_frame",
            AnalysisFrame,
            summary="Immutable coordinates, values and presence; expression, memory or stored backing.",
            acquisition="Use session.observe/read/values/frame, then Frame operations. compute/persist fix results; persisted_frame(id) reopens saved data. No direct construction.",
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
                "analysis_frame.lineage",
                "analysis_frame.source_reads",
                "analysis_frame.schema",
                "analysis_frame.read_page",
                "analysis_frame.read_periods",
            ),
            constraints=(
                "Ordinary operations consume current data. An instance may retain a source declaration for explicit attribute rereads; independent reopen has no declaration.",
                "Pending construction does not read business data. compute returns a fixed Frame; to_pandas evaluates pending inputs and exports isolated data without caching or saving. Only persist publishes results.",
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
    ]
    for lineage_owner, target, acquisition, producer, lineage_nested in (
        (
            FrameLineage,
            "frame_lineage",
            "Read frame.lineage on any carrier without evaluation.",
            "analysis_frame.lineage",
            ("column_lineage",),
        ),
        (
            ColumnLineage,
            "column_lineage",
            "Read a frame.lineage values, axes or presence entry.",
            "frame_lineage",
            ("semantic_object_identity",),
        ),
        (
            SemanticObjectIdentity,
            "semantic_object_identity",
            "Read a column lineage origin tuple.",
            "column_lineage",
            (),
        ),
    ):
        descriptors.append(
            value_type(
                target,
                lineage_owner,
                summary=lineage_owner.__doc__ or target,
                acquisition=acquisition,
                producers=(producer,),
                consumers=(*lineage_nested, target + ".show"),
                constraints=(
                    "Immutable static logical origins. No business rows, Catalog lookup, execution or semantic authority. Typed fields are complete independently of bounded display.",
                ),
            )
        )
        descriptors.append(
            operation(
                target + ".show",
                "lineage.show",
                lineage_owner.show,
                bindings=(bind(lineage_owner.show, lineage_owner),),
                summary="Print bounded captured semantic lineage.",
                parameters=(),
                output="None",
                constraints=("Metadata only; use typed fields for complete identities.",),
                effects="Print captured metadata.",
                failures=(),
                example=ExampleInput("result = lineage.show()", ("lineage",), "result", "None"),
            )
        )
    add_method_fact_descriptors(descriptors)
    add_time_descriptors(descriptors)
    populations = population_provider()
    results = result_provider()
    reads = read_provider()
    return DisclosureProvider(
        "analysis.dsl",
        (*descriptors, *populations.descriptors, *results.descriptors, *reads.descriptors),
        (
            ExportInput("AnalysisFrame", AnalysisFrame, "analysis_frame"),
            ExportInput("FrameAxis", FrameAxis, "frame_axis"),
            ExportInput("InstantAnchor", InstantAnchor, "InstantAnchor"),
            ExportInput("CalendarAxis", CalendarAxis, "CalendarAxis"),
            ExportInput("FrameMethodFacts", FrameMethodFacts, "frame_method_facts"),
            ExportInput("FrameLineage", FrameLineage, "frame_lineage"),
            ExportInput("ColumnLineage", ColumnLineage, "column_lineage"),
            ExportInput(
                "SemanticObjectIdentity", SemanticObjectIdentity, "semantic_object_identity"
            ),
            *populations.exports,
            *results.exports,
            *reads.exports,
        ),
    )
