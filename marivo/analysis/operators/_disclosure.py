"""Native owner for axis-free nominal calendar correspondence."""

from marivo._definition.correspondence import CalendarCorrespondence
from marivo.analysis._capabilities.dataset_model import (
    DisclosureProvider,
    ExampleInput,
    ExportInput,
    bind,
    operation,
    value_type,
)
from marivo.analysis.dsl.correspondence import ordinal


def provider() -> DisclosureProvider:
    return DisclosureProvider(
        "operators",
        (
            operation(
                "ordinal",
                "mv.ordinal",
                ordinal,
                summary="Pair compatible complete calendars by their frozen nominal positions.",
                parameters=(),
                output="CalendarCorrespondence",
                constraints=(
                    "The consuming AnalysisFrame checks frozen calendar interpretation and complete nominal/actual buckets; filtering never renumbers original positions, including named calendars.",
                ),
                effects="Construct an axis-free rule without I/O.",
                failures=("AnalysisError: repair incompatible consuming calendar bindings.",),
                example=ExampleInput(
                    "result = ordinal()", ("ordinal",), "result", "CalendarCorrespondence"
                ),
                discovery_group="inputs.time",
            ),
            value_type(
                "CalendarCorrespondence",
                CalendarCorrespondence,
                summary="Immutable axis-free calendar rule.",
                acquisition="Use ordinal(), period_offset(by), or previous_year().",
                producers=("ordinal", "period_offset", "previous_year"),
                consumers=("analysis_frame.align", "analysis_frame.compare"),
            ),
            operation(
                "CalendarCorrespondence.show",
                "correspondence.show",
                CalendarCorrespondence.show,
                bindings=(bind(CalendarCorrespondence.show, CalendarCorrespondence),),
                summary="Return the bounded calendar rule before endpoint calendars bind.",
                parameters=(),
                output="str",
                constraints=(
                    "Frozen endpoint calendars bind at AnalysisFrame.align/compare; match is explicit and is never inferred.",
                ),
                effects="Pure value rendering without I/O.",
                failures=(),
                example=ExampleInput(
                    "result = correspondence.show()", ("correspondence",), "result", "str"
                ),
            ),
        ),
        (
            ExportInput("ordinal", ordinal, "ordinal"),
            ExportInput("CalendarCorrespondence", CalendarCorrespondence, "CalendarCorrespondence"),
        ),
    )
