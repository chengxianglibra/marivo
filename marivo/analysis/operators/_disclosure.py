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
                summary="Pair compatible complete calendars by frozen positions, or one declared interval per side.",
                parameters=(),
                output="CalendarCorrespondence",
                constraints=(
                    "Calendars require compatible frozen interpretation and complete nominal/actual buckets; filtering preserves original positions. Interval ordinal requires one declared record per side and equal boundary timezones; filtered multi-record directories still reject.",
                ),
                effects="Construct an axis-free rule without I/O.",
                failures=("AnalysisError: repair incompatible frozen time bindings.",),
                example=ExampleInput(
                    "result = ordinal()", ("ordinal",), "result", "CalendarCorrespondence"
                ),
                discovery_group="inputs.comparison",
            ),
            value_type(
                "CalendarCorrespondence",
                CalendarCorrespondence,
                summary="Immutable axis-free calendar rule.",
                acquisition="Use mv.ordinal(), mv.offset(years=...), or mv.offset(periods=...). Direct construction and extension are unsupported.",
                producers=("ordinal", "offset"),
                consumers=("calendar_axis.shift", "analysis_frame.align", "analysis_frame.compare"),
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
                    "shift accepts offset only and freezes a direct relation without business reads. align uses explicit match; named compare may consume a verified immediate shift relation, or an explicit rule inside mv.baseline(...). ordinal pairs explicit complete endpoints only.",
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
